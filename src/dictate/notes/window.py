"""The Notes window: folders on the left, the notes in the chosen folder in the middle, the note on the
right. It saves as you type. Opened from the tray / menu-bar menu (Notes…), in a process of its own:
macOS only lets one thread draw windows, and in the app that thread belongs to the menu-bar icon.

Only one Notes window runs at a time; opening it again brings the open one to the front.
"""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess
import sys
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Any

from dictate import instance
from dictate.notes.store import (
    DATE_PRESETS,
    KEEP_DELETED_DAYS,
    NoteStore,
    date_range,
    long_time,
    notes_path,
    short_time,
)

log = logging.getLogger(__name__)

MAC = sys.platform == "darwin"
MOD = "Command" if MAC else "Control"
MOD_LABEL = "⌘" if MAC else "Ctrl+"
WHERE = {"Title and text": "all", "Title only": "title", "Text only": "body"}
DATE_FIELDS = {"Edited": "modified", "Created": "created"}

LIGHT = {
    "bg": "#ffffff",
    "side": "#f5f5f7",
    "line": "#e5e5ea",
    "text": "#1d1d1f",
    "muted": "#86868b",
    "accent": "#2563eb",
    "accent_text": "#ffffff",
    "select": "#e1e9fb",
    "hover": "#ebebef",
    "field": "#f2f2f5",
    "error": "#d93025",
    "drop": "#cddcfb",
}
DARK = {
    "bg": "#1e1e20",
    "side": "#252528",
    "line": "#38383c",
    "text": "#ececef",
    "muted": "#8e8e93",
    "accent": "#3b82f6",
    "accent_text": "#ffffff",
    "select": "#2d3b55",
    "hover": "#2f2f33",
    "field": "#2c2c30",
    "error": "#f28b82",
    "drop": "#33507f",
}


def _show_request():
    """The file a second "open Notes" leaves for the open window, which then comes to the front."""
    return notes_path().with_name("show-window")


def _dark_mode() -> bool:
    try:
        if MAC:
            out = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True, timeout=2
            ).stdout
            return "dark" in out.lower()
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            ) as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        pass
    return False


def _autohide(bar: ttk.Scrollbar, **pack: Any) -> Any:
    """A scrollbar that only shows when there's something to scroll."""

    def set_(first: str, last: str) -> None:
        if float(first) <= 0 and float(last) >= 1:
            bar.pack_forget()
        elif not bar.winfo_ismapped():
            bar.pack(**pack)
        bar.set(first, last)

    return set_


class Placeholder:
    """Grey hint text inside an empty entry ("Search notes")."""

    def __init__(self, entry: tk.Entry, var: tk.StringVar, text: str, color: str, font: Any) -> None:
        self.label = tk.Label(entry, text=text, fg=color, bg=entry.cget("bg"), font=font, cursor="xterm")
        self.label.bind("<Button-1>", lambda _e: entry.focus_set())
        self._entry, self._var = entry, var
        var.trace_add("write", lambda *_: self.update())
        self.update()

    def update(self) -> None:
        if self._var.get():
            self.label.place_forget()
        else:
            self.label.place(x=2, rely=0.5, anchor="w")


class NotesWindow:
    def __init__(self, store: NoteStore) -> None:
        self.store = store
        self.root = tk.Tk(className="Dictate Notes")
        self.root.title("Notes")
        self.c = DARK if _dark_mode() else LIGHT
        self.view: tuple[str, int | None] = ("all", None)  # ("all"|"folder"|"trash", folder id)
        self.note_id: int | None = None
        self._loading = False
        self._save_job: str | None = None
        self._search_job: str | None = None
        self._sort = "date"  # "date" (newest first, by the chosen date) or "title"
        self._drag: dict[str, Any] | None = None
        self._rename_entry: tk.Entry | None = None
        self._fonts()
        self._style()
        self._build()
        self._bind_keys()
        self._restore_geometry()
        self.reload_folders()
        self.reload_notes(select_first=True)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if MAC:
            self.root.createcommand("tk::mac::Quit", self.close)
        self.root.report_callback_exception = lambda *exc: log.error("notes window error", exc_info=exc)
        self.root.after(400, self._watch_show_requests)

    # -- look ---------------------------------------------------------------------------------------
    def _fonts(self) -> None:
        family = tkfont.nametofont("TkDefaultFont").actual()["family"]
        if sys.platform == "win32":
            family = "Segoe UI"
        size = 13 if MAC else 10
        self.f = {
            "ui": tkfont.Font(family=family, size=size),
            "small": tkfont.Font(family=family, size=size - (2 if MAC else 1)),
            "bold": tkfont.Font(family=family, size=size, weight="bold"),
            "head": tkfont.Font(family=family, size=size + 2, weight="bold"),
            "title": tkfont.Font(family=family, size=size + 9, weight="bold"),
            "body": tkfont.Font(family=family, size=size + (1 if MAC else 2)),
        }

    def _style(self) -> None:
        c, root = self.c, self.root
        root.configure(bg=c["bg"])
        s = ttk.Style(root)
        s.theme_use("clam")
        s.configure(".", background=c["bg"], foreground=c["text"], font=self.f["ui"], borderwidth=0, focuscolor=c["bg"])
        s.configure("TFrame", background=c["bg"])
        s.configure("Side.TFrame", background=c["side"])
        s.configure("TLabel", background=c["bg"], foreground=c["text"])
        s.configure("Side.TLabel", background=c["side"], foreground=c["text"], font=self.f["head"])
        s.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"], font=self.f["small"])
        s.configure("Empty.TLabel", background=c["bg"], foreground=c["muted"], font=self.f["ui"])
        row = self.f["ui"].metrics("linespace")
        for name, bg in (("Treeview", c["bg"]), ("Side.Treeview", c["side"])):
            s.configure(
                name,
                background=bg,
                fieldbackground=bg,
                foreground=c["text"],
                rowheight=row + (14 if MAC else 12),
                borderwidth=0,
                relief="flat",
            )
            s.map(name, background=[("selected", c["select"])], foreground=[("selected", c["text"])])
            s.layout(name, [("Treeview.treearea", {"sticky": "nswe"})])  # no border around it
        s.configure(
            "Treeview.Heading",
            background=c["bg"],
            foreground=c["muted"],
            font=self.f["small"],
            relief="flat",
            borderwidth=0,
            padding=(6, 4),
        )
        s.map("Treeview.Heading", background=[("active", c["bg"])], foreground=[("active", c["text"])])
        for name, bg in (("TButton", c["bg"]), ("Side.TButton", c["side"])):
            s.configure(name, background=bg, foreground=c["text"], padding=(10, 5), relief="flat", borderwidth=0)
            s.map(name, background=[("active", c["hover"]), ("pressed", c["hover"])])
        s.configure(
            "Accent.TButton",
            background=c["accent"],
            foreground=c["accent_text"],
            padding=(12, 6),
            relief="flat",
            font=self.f["bold"],
        )
        s.map("Accent.TButton", background=[("active", c["accent"]), ("pressed", c["accent"])])
        s.configure(
            "TMenubutton",
            background=c["bg"],
            foreground=c["muted"],
            padding=(6, 3),
            relief="flat",
            arrowcolor=c["muted"],
            font=self.f["small"],
        )
        s.map("TMenubutton", background=[("active", c["hover"])])
        for name in ("TCombobox", "Error.TCombobox"):
            edge = c["error"] if name.startswith("Error") else c["field"]
            s.configure(
                name,
                fieldbackground=c["field"],
                background=c["field"],
                foreground=c["text"],
                bordercolor=edge,
                lightcolor=edge,
                darkcolor=edge,
                arrowcolor=c["muted"],
                padding=(6, 3),
                relief="flat",
                selectbackground=c["field"],
                selectforeground=c["text"],
                font=self.f["small"],
            )
            s.map(
                name,
                fieldbackground=[("readonly", c["field"])],
                background=[("active", c["field"])],
                foreground=[("readonly", c["text"])],
                selectbackground=[("readonly", c["field"])],
                selectforeground=[("readonly", c["text"])],
            )
        root.option_add("*TCombobox*Listbox.background", c["bg"])
        root.option_add("*TCombobox*Listbox.foreground", c["text"])
        root.option_add("*TCombobox*Listbox.selectBackground", c["select"])
        root.option_add("*TCombobox*Listbox.selectForeground", c["text"])
        root.option_add("*TCombobox*Listbox.font", self.f["small"])
        s.layout(
            "Thin.Vertical.TScrollbar",
            [
                (
                    "Vertical.Scrollbar.trough",
                    {"sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]},
                )
            ],
        )
        s.configure(
            "Thin.Vertical.TScrollbar",
            troughcolor=c["bg"],
            background=c["line"],
            bordercolor=c["bg"],
            lightcolor=c["line"],
            darkcolor=c["line"],
            arrowsize=8,
            gripcount=0,
        )
        s.map("Thin.Vertical.TScrollbar", background=[("active", c["muted"])])

    def _menu(self) -> tk.Menu:
        c = self.c
        return tk.Menu(
            self.root,
            tearoff=False,
            bg=c["bg"],
            fg=c["text"],
            activebackground=c["select"],
            activeforeground=c["text"],
            bd=0,
            font=self.f["ui"],
        )

    # -- layout -------------------------------------------------------------------------------------
    def _build(self) -> None:
        c, root = self.c, self.root
        panes = tk.PanedWindow(root, orient="horizontal", sashwidth=1, bg=c["line"], bd=0, sashrelief="flat")
        panes.pack(fill="both", expand=True)
        self.panes = panes

        # folders
        side = ttk.Frame(panes, style="Side.TFrame", padding=(10, 14, 10, 10))
        ttk.Label(side, text="Notes", style="Side.TLabel").pack(anchor="w", padx=6, pady=(0, 10))
        self.folders = ttk.Treeview(side, style="Side.Treeview", show="tree", columns=("n",), selectmode="browse")
        self.folders.column("#0", width=150, stretch=True)
        self.folders.column("n", width=34, anchor="e", stretch=False)
        self.folders.tag_configure("muted", foreground=c["muted"])
        self.folders.tag_configure("drop", background=c["drop"])
        self.folders.pack(fill="both", expand=True)
        ttk.Button(side, text="+  New folder", style="Side.TButton", command=self.new_folder).pack(
            anchor="w", pady=(8, 0)
        )
        self.folders.bind("<<TreeviewSelect>>", lambda _e: self._folder_selected())
        self.folders.bind("<Double-1>", self._folder_double_click)
        for seq in ("<Button-2>", "<Control-Button-1>") if MAC else ("<Button-3>",):
            self.folders.bind(seq, self._folder_menu)
        self.folders.bind("<ButtonPress-1>", lambda e: self._drag_start(e, "folder"), add=True)
        self.folders.bind("<B1-Motion>", self._drag_move, add=True)
        self.folders.bind("<ButtonRelease-1>", self._drag_end, add=True)
        panes.add(side, minsize=170, width=220)

        # the list
        middle = ttk.Frame(panes, padding=(12, 14, 0, 10))
        search_box = tk.Frame(middle, bg=c["field"], highlightthickness=0)
        search_box.pack(fill="x", padx=(0, 12))
        tk.Label(search_box, text="⌕", bg=c["field"], fg=c["muted"], font=self.f["head"]).pack(side="left", padx=(8, 2))
        self.search_var = tk.StringVar()
        self.search = tk.Entry(
            search_box,
            textvariable=self.search_var,
            relief="flat",
            bd=0,
            bg=c["field"],
            fg=c["text"],
            insertbackground=c["text"],
            font=self.f["ui"],
            highlightthickness=0,
        )
        self.search.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        Placeholder(self.search, self.search_var, "Search notes", c["muted"], self.f["ui"])
        self.search_var.trace_add("write", lambda *_: self._search_later())
        self.search.bind("<Escape>", lambda _e: self.search_var.set(""))
        self.search.bind("<Down>", lambda _e: self._focus_list())

        filters = ttk.Frame(middle)
        filters.pack(fill="x", padx=(0, 12), pady=(8, 6))
        self.where_var = tk.StringVar(value="Title and text")
        self.date_var = tk.StringVar(value="Any time")
        self.field_var = tk.StringVar(value="Edited")
        self.where = ttk.Combobox(filters, textvariable=self.where_var, values=list(WHERE), state="readonly", width=12)
        self.dates = ttk.Combobox(filters, textvariable=self.date_var, values=DATE_PRESETS, width=12)
        self.field = ttk.Combobox(
            filters, textvariable=self.field_var, values=list(DATE_FIELDS), state="readonly", width=7
        )
        for i, box in enumerate((self.where, self.dates, self.field)):
            box.grid(row=0, column=i, sticky="we", padx=(0 if i == 0 else 6, 0))
            filters.columnconfigure(i, weight=(1, 1, 0)[i])
            box.bind("<<ComboboxSelected>>", lambda _e: (self.root.focus_set(), self.reload_notes(select_first=True)))
        self.dates.bind("<Return>", lambda _e: self.reload_notes(select_first=True))
        self.dates.bind("<FocusOut>", lambda _e: self.reload_notes(select_first=True))
        self.dates.bind("<KeyRelease>", lambda e: self._search_later(400) if e.keysym != "Return" else None)
        self._tip(
            self.dates,
            "Pick a period, or type a date: 2026-11-20, 2026-11, 2026,\nor a range: 2026-11-01 to 2026-11-15",
        )

        listing = ttk.Frame(middle)
        listing.pack(fill="both", expand=True)
        self.notes = ttk.Treeview(listing, columns=("title", "folder", "date"), show="headings", selectmode="browse")
        self.notes.column("title", width=170, stretch=True, anchor="w")
        self.notes.column("folder", width=90, stretch=False, anchor="w")
        self.notes.column("date", width=92, stretch=False, anchor="e")
        self.notes.heading("title", text="Title", anchor="w", command=lambda: self._set_sort("title"))
        self.notes.heading("folder", text="Folder", anchor="w")
        self.notes.heading("date", text="Edited", anchor="e", command=lambda: self._set_sort("date"))
        self.notes.tag_configure("muted", foreground=c["muted"])
        bar = ttk.Scrollbar(listing, orient="vertical", command=self.notes.yview, style="Thin.Vertical.TScrollbar")
        self.notes.configure(yscrollcommand=_autohide(bar, side="right", fill="y", padx=(2, 2)))
        self.notes.pack(side="left", fill="both", expand=True)
        self.empty = ttk.Label(listing, style="Empty.TLabel", justify="center")
        self.notes.bind("<<TreeviewSelect>>", lambda _e: self._note_selected())
        self.notes.bind("<Delete>", lambda _e: self.delete_note())
        self.notes.bind("<BackSpace>", lambda _e: self.delete_note() if MAC else None)
        self.notes.bind("<Return>", lambda _e: self.body.focus_set())
        self.notes.bind("<ButtonPress-1>", lambda e: self._drag_start(e, "note"), add=True)
        self.notes.bind("<B1-Motion>", self._drag_move, add=True)
        self.notes.bind("<ButtonRelease-1>", self._drag_end, add=True)

        foot = ttk.Frame(middle)
        foot.pack(fill="x", pady=(8, 0), padx=(0, 12))
        self.new_btn = ttk.Button(foot, text="+  New note", style="Accent.TButton", command=self.new_note)
        self.new_btn.pack(side="left")
        self.count = ttk.Label(foot, style="Muted.TLabel")
        self.count.pack(side="right")
        panes.add(middle, minsize=280, width=380)

        # the note
        right = ttk.Frame(panes, padding=(26, 14, 18, 12))
        self.editor = editor = ttk.Frame(right)
        editor.pack(fill="both", expand=True)
        top = ttk.Frame(editor)
        top.pack(fill="x")
        self.folder_btn = ttk.Menubutton(top, text="")
        self.folder_btn.pack(side="left")
        self.folder_menu = self._menu()
        self.folder_btn.configure(menu=self.folder_menu)
        self.delete_btn = ttk.Button(top, text="Delete", command=self.delete_note)
        self.delete_btn.pack(side="right")
        self.restore_btn = ttk.Button(top, text="Restore", command=self.restore_note)
        self.title_var = tk.StringVar()
        self.title = tk.Entry(
            editor,
            textvariable=self.title_var,
            relief="flat",
            bd=0,
            bg=c["bg"],
            fg=c["text"],
            insertbackground=c["text"],
            font=self.f["title"],
            highlightthickness=0,
            disabledbackground=c["bg"],
            disabledforeground=c["text"],
        )
        self.title.pack(fill="x", pady=(14, 2))
        self.title_hint = Placeholder(self.title, self.title_var, "Title", c["muted"], self.f["title"])
        self.title_var.trace_add("write", lambda *_: self._changed())
        self.title.bind("<Return>", lambda _e: (self.body.focus_set(), "break")[1])
        self.title.bind("<Down>", lambda _e: self.body.focus_set())
        self.meta = ttk.Label(editor, style="Muted.TLabel")
        self.meta.pack(anchor="w", pady=(0, 10))
        tk.Frame(editor, height=1, bg=c["line"]).pack(fill="x", pady=(0, 10))
        text_box = ttk.Frame(editor)
        text_box.pack(fill="both", expand=True)
        self.body = tk.Text(
            text_box,
            wrap="word",
            undo=True,
            autoseparators=True,
            maxundo=-1,
            relief="flat",
            bd=0,
            bg=c["bg"],
            fg=c["text"],
            insertbackground=c["text"],
            selectbackground=c["select"],
            selectforeground=c["text"],
            inactiveselectbackground=c["select"],
            font=self.f["body"],
            highlightthickness=0,
            padx=0,
            pady=2,
            spacing1=2,
            spacing3=6,
        )
        body_bar = ttk.Scrollbar(text_box, orient="vertical", command=self.body.yview, style="Thin.Vertical.TScrollbar")
        self.body.configure(yscrollcommand=_autohide(body_bar, side="right", fill="y"))
        self.body.pack(side="left", fill="both", expand=True)
        self.body.bind("<<Modified>>", self._body_modified)
        self.blank = ttk.Label(
            right,
            style="Empty.TLabel",
            justify="center",
            text=f"No note open\n\nPick one, or press {MOD_LABEL}N for a new note.",
        )
        panes.add(right, minsize=320)
        self.right = right

    def _tip(self, widget: tk.Widget, text: str) -> None:
        """A small hint that shows while the pointer rests on `widget`."""
        tip: list[tk.Toplevel] = []

        def show(_e: Any) -> None:
            if tip:
                return
            t = tk.Toplevel(widget)
            t.wm_overrideredirect(True)
            t.wm_geometry(f"+{widget.winfo_rootx()}+{widget.winfo_rooty() + widget.winfo_height() + 4}")
            tk.Label(
                t, text=text, justify="left", bg=self.c["text"], fg=self.c["bg"], font=self.f["small"], padx=8, pady=5
            ).pack()
            tip.append(t)

        def hide(_e: Any) -> None:
            while tip:
                tip.pop().destroy()

        widget.bind(
            "<Enter>",
            lambda e: widget.after(
                500, lambda: show(e) if widget.winfo_containing(*widget.winfo_pointerxy()) == widget else None
            ),
            add=True,
        )
        widget.bind("<Leave>", hide, add=True)
        widget.bind("<ButtonPress>", hide, add=True)

    def _bind_keys(self) -> None:
        r = self.root
        r.bind_all(f"<{MOD}-n>", lambda _e: self.new_note())
        r.bind_all(f"<{MOD}-N>", lambda _e: self.new_folder())  # with Shift
        r.bind_all(f"<{MOD}-f>", lambda _e: (self.search.focus_set(), self.search.select_range(0, "end")))
        r.bind_all(f"<{MOD}-w>", lambda _e: self.close())
        r.bind_all(f"<{MOD}-s>", lambda _e: self.save_now())
        if not MAC:  # Tk's old Windows defaults: Ctrl+A goes to the start of the line
            r.bind_class("Text", "<Control-a>", lambda e: (e.widget.tag_add("sel", "1.0", "end-1c"), "break")[1])
            r.bind_class(
                "Entry", "<Control-a>", lambda e: (e.widget.select_range(0, "end"), e.widget.icursor("end"), "break")[2]
            )
            r.bind_class("Text", "<Control-y>", lambda e: (e.widget.event_generate("<<Redo>>"), "break")[1])

    # -- window position ----------------------------------------------------------------------------------
    def _geometry_file(self):
        return notes_path().with_name("window.json")

    def _restore_geometry(self) -> None:
        self.root.minsize(820, 480)
        try:
            data = json.loads(self._geometry_file().read_text(encoding="utf-8"))
            self.root.geometry(data["geometry"])
            self.root.update_idletasks()
            for i, x in enumerate(data.get("sashes", [])[:2]):
                self.panes.sash_place(i, int(x), 0)
        except (OSError, ValueError, KeyError, TypeError, tk.TclError):
            w, h = 1120, 700
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.root.geometry(f"{min(w, sw - 80)}x{min(h, sh - 120)}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

    def _save_geometry(self) -> None:
        with contextlib.suppress(Exception):
            sashes = [self.panes.sash_coord(i)[0] for i in range(2)]
            data = {"geometry": self.root.geometry(), "sashes": sashes}
            self._geometry_file().write_text(json.dumps(data), encoding="utf-8")

    # -- folders ------------------------------------------------------------------------------------------
    @staticmethod
    def _fid(item: str) -> int | None:
        return int(item[1:]) if item.startswith("f") else None

    def reload_folders(self) -> None:
        tree = self.folders
        opened = {i for i in self._all_items(tree) if tree.item(i, "open")}
        first_time = not tree.get_children()
        tree.delete(*tree.get_children())
        folders = self.store.folders()
        direct = self.store.counts()
        children: dict[int | None, list] = {}
        for f in folders:
            children.setdefault(f.parent_id, []).append(f)
        known = {f.id for f in folders}

        def total(fid: int) -> int:
            return direct.get(fid, 0) + sum(total(ch.id) for ch in children.get(fid, []))

        tree.insert("", "end", "all", text="  All notes", values=(sum(direct.values()) or "",))

        def add(parent_item: str, parent_id: int | None) -> None:
            for f in children.get(parent_id, []):
                item = f"f{f.id}"
                tree.insert(
                    parent_item,
                    "end",
                    item,
                    text=f"  {f.name}",
                    values=(total(f.id) or "",),
                    open=item in opened or first_time,
                )
                add(item, f.id)

        add("", None)
        for f in folders:  # a folder whose parent vanished (shouldn't happen): show it at the top level
            if f.parent_id is not None and f.parent_id not in known and not tree.exists(f"f{f.id}"):
                tree.insert("", "end", f"f{f.id}", text=f"  {f.name}", values=(total(f.id) or "",))
                add(f"f{f.id}", f.id)
        gone = self.store.deleted_count()
        tree.insert("", "end", "trash", text="  Recently deleted", values=(gone or "",), tags=("muted",))
        kind, fid = self.view
        item = {"all": "all", "trash": "trash"}.get(kind, f"f{fid}")
        if not tree.exists(item):
            self.view, item = ("all", None), "all"
        tree.selection_set(item)
        tree.see(item)
        self._fill_folder_menu()

    @staticmethod
    def _all_items(tree: ttk.Treeview, parent: str = "") -> list[str]:
        out = []
        for i in tree.get_children(parent):
            out += [i, *NotesWindow._all_items(tree, i)]
        return out

    def _folder_selected(self) -> None:
        sel = self.folders.selection()
        if not sel:
            return
        item = sel[0]
        view = ("all", None) if item == "all" else ("trash", None) if item == "trash" else ("folder", self._fid(item))
        if view != self.view:
            self.save_now()
            self.view = view
            self.reload_notes(select_first=True)

    def _folder_double_click(self, e: tk.Event) -> str | None:
        item = self.folders.identify_row(e.y)
        if item.startswith("f"):
            self.rename_folder(item)
            return "break"
        return None

    def _folder_menu(self, e: tk.Event) -> None:
        item = self.folders.identify_row(e.y)
        if not item:
            return
        self.folders.selection_set(item)
        menu = self._menu()
        if item == "trash":
            menu.add_command(label="Empty Recently deleted", command=self.empty_trash)
        else:
            menu.add_command(label="New note", command=self.new_note)
            menu.add_command(
                label="New folder inside" if item != "all" else "New folder",
                command=lambda: self.new_folder(self._fid(item)),
            )
            if item.startswith("f"):
                menu.add_separator()
                menu.add_command(label="Rename", command=lambda: self.rename_folder(item))
                if self.folders.parent(item):
                    menu.add_command(label="Move to the top level", command=lambda: self._move_folder(item, None))
                menu.add_command(label="Delete folder…", command=lambda: self.delete_folder(item))
        menu.tk_popup(e.x_root, e.y_root)

    def new_folder(self, parent_id: int | None = None) -> None:
        fid = self.store.add_folder("New folder", parent_id)
        if parent_id is not None:
            self.folders.item(f"f{parent_id}", open=True)
        self.save_now()
        self.view = ("folder", fid)
        self.reload_folders()
        self.reload_notes(select_first=True)
        self.root.after(30, lambda: self.rename_folder(f"f{fid}", select_all=True))

    def rename_folder(self, item: str, select_all: bool = True) -> None:
        """Edit the name in place: Enter keeps it, Esc cancels."""
        fid = self._fid(item)
        if fid is None or self._rename_entry is not None:
            return
        self.folders.see(item)
        self.folders.update_idletasks()
        box = self.folders.bbox(item, "#0")
        if not box:
            return
        x, y, w, h = box
        name = self.folders.item(item, "text").strip()
        var = tk.StringVar(value=name)
        c = self.c
        entry = tk.Entry(
            self.folders,
            textvariable=var,
            relief="flat",
            bd=0,
            bg=c["bg"],
            fg=c["text"],
            insertbackground=c["text"],
            font=self.f["ui"],
            highlightthickness=1,
            highlightcolor=c["accent"],
            highlightbackground=c["accent"],
        )
        indent = 18 * (len(self._ancestors(item)) + 1)
        entry.place(x=x + indent, y=y + 2, width=max(80, w - indent - 4), height=h - 4)
        entry.focus_set()
        if select_all:
            entry.select_range(0, "end")
        self._rename_entry = entry
        done = [False]

        def finish(keep: bool) -> None:
            if done[0]:
                return
            done[0] = True
            new = var.get().strip()
            entry.destroy()
            self._rename_entry = None
            if keep and new and new != name:
                self.store.rename_folder(fid, new)
                self.reload_folders()
                self.reload_notes()

        entry.bind("<Return>", lambda _e: finish(True))
        entry.bind("<KP_Enter>", lambda _e: finish(True))
        entry.bind("<Escape>", lambda _e: finish(False))
        entry.bind("<FocusOut>", lambda _e: finish(True))

    def _ancestors(self, item: str) -> list[str]:
        out = []
        while parent := self.folders.parent(item):
            out.append(parent)
            item = parent
        return out

    def delete_folder(self, item: str) -> None:
        fid = self._fid(item)
        if fid is None:
            return
        name = self.folders.item(item, "text").strip()
        n = sum(self.store.counts().get(i, 0) for i in self.store.subtree(fid))
        inside = len(self.store.subtree(fid)) - 1
        what = []
        if inside:
            what.append(f"its {inside} folder{'s' * (inside > 1)}")
        if n:
            what.append(f"{n} note{'s' * (n > 1)}")
        detail = (
            f"This deletes {' and '.join(what)} too. The notes go to Recently deleted for {KEEP_DELETED_DAYS} days."
            if what
            else "The folder is empty."
        )
        if not messagebox.askyesno("Delete folder", f"Delete “{name}”?\n\n{detail}", parent=self.root, icon="warning"):
            return
        self.save_now()
        self.store.delete_folder(fid)
        if self.view == ("folder", fid) or (
            self.view[0] == "folder" and self.view[1] not in {f.id for f in self.store.folders()}
        ):
            self.view = ("all", None)
        self.reload_folders()
        self.reload_notes(select_first=True)

    def _move_folder(self, item: str, target: str | None) -> None:
        fid = self._fid(item)
        if fid is None:
            return
        parent = self._fid(target) if target else None
        if self.store.move_folder(fid, parent):
            if parent is not None:
                self.folders.item(f"f{parent}", open=True)
            self.reload_folders()

    def empty_trash(self) -> None:
        n = self.store.deleted_count()
        if not n:
            return
        if messagebox.askyesno(
            "Empty Recently deleted",
            f"Delete {n} note{'s' * (n > 1)} for good? This can't be undone.",
            parent=self.root,
            icon="warning",
        ):
            self.store.empty_deleted()
            self.note_id = None
            self.reload_folders()
            self.reload_notes(select_first=True)

    def _fill_folder_menu(self) -> None:
        m = self.folder_menu
        m.delete(0, "end")
        m.add_command(label="No folder", command=lambda: self.move_note(None))
        folders = self.store.folders()
        if folders:
            m.add_separator()
        for f in sorted(folders, key=lambda f: self.store.folder_path(f.id).lower()):
            m.add_command(label=self.store.folder_path(f.id), command=lambda fid=f.id: self.move_note(fid))

    # -- the list -------------------------------------------------------------------------------------------
    def _search_later(self, delay: int = 150) -> None:
        if self._search_job:
            self.root.after_cancel(self._search_job)
        # a new search opens its first match (unless the open note matches)
        self._search_job = self.root.after(delay, lambda: self.reload_notes(select_first=True))

    def _set_sort(self, sort: str) -> None:
        self._sort = sort
        self.reload_notes()

    def _filters(self) -> dict[str, Any]:
        kind, fid = self.view
        try:
            dates = date_range(self.date_var.get())
            self.dates.configure(style="TCombobox")
        except ValueError:
            dates = None
            self.dates.configure(style="Error.TCombobox")
        field = DATE_FIELDS.get(self.field_var.get(), "modified")
        return {
            "text": self.search_var.get(),
            "folder_id": fid if kind == "folder" else None,
            "deleted": kind == "trash",
            "where": WHERE.get(self.where_var.get(), "all"),
            "dates": dates,
            "date_field": field,
            "sort": "title" if self._sort == "title" else field,
        }

    def reload_notes(self, select_first: bool = False) -> None:
        self._search_job = None
        q = self._filters()
        notes = self.store.search(**q)
        tree = self.notes
        tree.delete(*tree.get_children())
        trash = q["deleted"]
        field = "deleted" if trash else q["date_field"]
        arrow = " ↓"
        tree.heading("title", text="Title" + (arrow if self._sort == "title" and not trash else ""))
        tree.heading(
            "date",
            text={"modified": "Edited", "created": "Created", "deleted": "Deleted"}[field]
            + (arrow if self._sort == "date" or trash else ""),
        )
        show_folder = self.view[0] != "folder" or bool(self.store.subtree(self.view[1] or 0) - {self.view[1]})
        tree.configure(displaycolumns=("title", "folder", "date") if show_folder else ("title", "date"))
        paths: dict[int | None, str] = {}
        now = time.time()
        for n in notes:
            if n.folder_id not in paths:
                paths[n.folder_id] = self.store.folder_path(n.folder_id)
            when = getattr(n, field) or n.modified
            tree.insert(
                "",
                "end",
                f"n{n.id}",
                values=(n.display_title, paths[n.folder_id], short_time(when, now)),
                tags=(() if n.title.strip() else ("muted",)),
            )
        searching = bool(q["text"].strip() or q["dates"])
        total = len(notes)
        self.count.configure(text=f"{total} note{'s' * (total != 1)}" + (" found" if searching else ""))
        if notes:
            self.empty.place_forget()
        else:
            self.empty.configure(
                text="No matches"
                if searching
                else "Nothing here"
                if trash
                else f"No notes yet\n\n{MOD_LABEL}N makes one"
            )
            self.empty.place(relx=0.5, rely=0.35, anchor="center")
        self.new_btn.configure(state="disabled" if trash else "normal")
        current = f"n{self.note_id}" if self.note_id is not None else ""
        if current and tree.exists(current):
            tree.selection_set(current)
            tree.see(current)
        elif select_first or (self.note_id is not None and self.store.get(self.note_id) is None):
            first = tree.get_children()
            if first:
                tree.selection_set(first[0])
                self.open_note(int(first[0][1:]))
            else:
                self.open_note(None)
        elif self.note_id is not None:  # the open note doesn't match the search: keep it open, unselected
            tree.selection_remove(*tree.selection())

    def _focus_list(self) -> None:
        kids = self.notes.get_children()
        if kids:
            self.notes.focus_set()
            target = self.notes.selection()[0] if self.notes.selection() else kids[0]
            self.notes.selection_set(target)
            self.notes.focus(target)

    def _note_selected(self) -> None:
        sel = self.notes.selection()
        if sel and int(sel[0][1:]) != self.note_id:
            self.open_note(int(sel[0][1:]))

    # -- the note ---------------------------------------------------------------------------------------
    def open_note(self, note_id: int | None) -> None:
        self.save_now()
        note = self.store.get(note_id) if note_id is not None else None
        self.note_id = note.id if note else None
        self._loading = True
        try:
            for w in (self.title, self.body):
                w.configure(state="normal")
            self.title_var.set(note.title if note else "")
            self.body.delete("1.0", "end")
            if note:
                self.body.insert("1.0", note.body)
            self.body.edit_reset()
            self.body.edit_modified(False)
        finally:
            self._loading = False
        if note is None:
            self.editor.pack_forget()
            self.blank.place(relx=0.5, rely=0.4, anchor="center")
            return
        self.blank.place_forget()
        self.editor.pack(fill="both", expand=True)
        trash = note.deleted is not None
        for w in (self.title, self.body):
            w.configure(state="disabled" if trash else "normal")
        self.restore_btn.pack_forget()
        if trash:
            self.delete_btn.configure(text="Delete forever")
            self.restore_btn.pack(side="right", padx=(0, 6))
            self.folder_btn.pack_forget()
        else:
            self.delete_btn.configure(text="Delete")
            self.folder_btn.pack(side="left")
        self._show_meta(note)

    def _show_meta(self, note: Any) -> None:
        where = self.store.folder_path(note.folder_id) or "No folder"
        self.folder_btn.configure(text=f"📁  {where}")
        parts = [f"Created {long_time(note.created)}"]
        if note.modified - note.created > 1:
            parts.append(f"Edited {long_time(note.modified)}")
        if note.deleted is not None:
            parts.append(f"Deleted {long_time(note.deleted)}")
        self.meta.configure(text="   ·   ".join(parts))

    def _body_modified(self, _e: Any) -> None:
        if self.body.edit_modified():
            self.body.edit_modified(False)
            self._changed()

    def _changed(self) -> None:
        if self._loading or self.note_id is None:
            return
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(500, self.save_now)

    def save_now(self) -> None:
        if self._save_job:
            self.root.after_cancel(self._save_job)
            self._save_job = None
        if self.note_id is None:
            return
        note = self.store.get(self.note_id)
        if note is None or note.deleted is not None:
            return
        title, body = self.title_var.get(), self.body.get("1.0", "end-1c")
        if self.store.save(self.note_id, title, body):
            note = self.store.get(self.note_id)
            self._show_meta(note)
            self.reload_notes()

    def new_note(self) -> None:
        self.save_now()
        kind, fid = self.view
        if kind == "trash":
            self.view, fid = ("all", None), None
            self.reload_folders()
        for var, default in ((self.search_var, ""), (self.date_var, "Any time")):
            var.set(default)  # so the new note is in the list
        note_id = self.store.add_note(fid if kind == "folder" else None)
        self.note_id = None
        self.reload_folders()
        self.reload_notes()
        self.notes.selection_set(f"n{note_id}")
        self.notes.see(f"n{note_id}")
        self.open_note(note_id)
        self.title.focus_set()

    def delete_note(self) -> None:
        if self.note_id is None:
            return
        note = self.store.get(self.note_id)
        if note is None:
            return
        if note.deleted is not None and not messagebox.askyesno(
            "Delete forever",
            f"Delete “{note.display_title}” for good? This can't be undone.",
            parent=self.root,
            icon="warning",
        ):
            return
        self.save_now()
        kids = list(self.notes.get_children())
        item = f"n{note.id}"
        nxt = None
        if item in kids:
            i = kids.index(item)
            rest = kids[i + 1 :] or kids[:i][-1:]
            nxt = int(rest[0][1:]) if rest else None
        self.store.delete_note(note.id)
        self.note_id = None
        self.reload_folders()
        self.reload_notes()
        if nxt is not None and self.notes.exists(f"n{nxt}"):
            self.notes.selection_set(f"n{nxt}")
            self.open_note(nxt)
        else:
            self.open_note(None)

    def restore_note(self) -> None:
        if self.note_id is None:
            return
        self.store.restore_note(self.note_id)
        self.note_id = None
        self.reload_folders()
        self.reload_notes(select_first=True)

    def move_note(self, folder_id: int | None, note_id: int | None = None) -> None:
        note_id = note_id if note_id is not None else self.note_id
        if note_id is None:
            return
        if note_id == self.note_id:
            self.save_now()
        self.store.move_note(note_id, folder_id)
        self.reload_folders()
        self.reload_notes()
        if note_id == self.note_id and (note := self.store.get(note_id)):
            self._show_meta(note)

    # -- drag a note onto a folder (or a folder into another) -------------------------------------------------
    def _drag_start(self, e: tk.Event, kind: str) -> None:
        tree = self.notes if kind == "note" else self.folders
        item = tree.identify_row(e.y)
        ok = item.startswith("n") if kind == "note" else item.startswith("f")
        self._drag = {"kind": kind, "item": item, "x": e.x_root, "y": e.y_root, "on": False} if ok else None

    def _drop_target(self, e: tk.Event) -> str | None:
        under = self.root.winfo_containing(e.x_root, e.y_root)
        if under is not self.folders:
            return None
        item = self.folders.identify_row(e.y_root - self.folders.winfo_rooty())
        return item if item and item != "trash" else None

    def _drag_move(self, e: tk.Event) -> str | None:
        d = self._drag
        if d is None:
            return
        if not d["on"] and abs(e.x_root - d["x"]) + abs(e.y_root - d["y"]) > 8:
            d["on"] = True
            self.root.configure(cursor="hand2")
        if d["on"]:
            target = self._drop_target(e)
            for i in self._all_items(self.folders):
                tags = [t for t in self.folders.item(i, "tags") if t != "drop"]
                if i == target and i != d["item"]:
                    tags.append("drop")
                self.folders.item(i, tags=tags)
            return "break"  # the tree mustn't select what's under the pointer while dragging
        return None

    def _drag_end(self, e: tk.Event) -> None:
        d, self._drag = self._drag, None
        if d is None or not d["on"]:
            return
        self.root.configure(cursor="")
        target = self._drop_target(e)
        for i in self._all_items(self.folders):
            self.folders.item(i, tags=[t for t in self.folders.item(i, "tags") if t != "drop"])
        if target is None or target == d["item"]:
            if d["kind"] == "folder":
                self.reload_folders()
            return
        if d["kind"] == "note":
            self.move_note(self._fid(target), int(d["item"][1:]))
        else:
            self._move_folder(d["item"], None if target == "all" else target)

    # -- running --------------------------------------------------------------------------------------
    def _watch_show_requests(self) -> None:
        req = _show_request()
        if req.exists():
            with contextlib.suppress(OSError):
                req.unlink()
            self.bring_to_front()
        self.root.after(400, self._watch_show_requests)

    def bring_to_front(self) -> None:
        r = self.root
        r.deiconify()
        r.lift()
        r.attributes("-topmost", True)
        r.after(300, lambda: r.attributes("-topmost", False))
        r.focus_force()
        if MAC:
            _mac_activate()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.save_now()
        self._save_geometry()
        self.store.close()
        self.root.destroy()

    def run(self) -> None:
        if sys.platform == "win32":
            _windows_dark_title_bar(self.root, self.c is DARK)
        _set_icon(self.root)
        self.root.after(50, self.bring_to_front)
        self.root.mainloop()


# -- platform touches ----------------------------------------------------------------------------------------
def _mac_activate() -> None:
    """The app normally lives in the menu bar only; while Notes is open it gets a Dock icon and comes forward."""
    with contextlib.suppress(Exception):
        from AppKit import NSApplication

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular
        app.activateIgnoringOtherApps_(True)


def _windows_dark_title_bar(root: tk.Tk, dark: bool) -> None:
    if not dark:
        return
    with contextlib.suppress(Exception):
        import ctypes

        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))


def _set_icon(root: tk.Tk) -> None:
    with contextlib.suppress(Exception):
        from PIL import Image, ImageDraw, ImageTk

        from dictate.tray import icon_image

        size = 256
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(img).rounded_rectangle((0, 0, size, size), radius=56, fill=(32, 36, 48, 255))
        mic = icon_image("ready", 184)
        img.alpha_composite(mic, ((size - mic.width) // 2, (size - mic.height) // 2))
        photo = ImageTk.PhotoImage(img)
        root.iconphoto(True, photo)
        root._icon_ref = photo  # type: ignore[attr-defined]  # keep it alive


def _log_to_file() -> None:
    """Problems go to notes.log next to Dictate's own log (two processes can't share one rotating file)."""
    with contextlib.suppress(OSError):
        from dictate import paths

        d = paths.log_dir()
        d.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(d / "notes.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)


def main() -> int:
    """Open the Notes window (or bring the open one to the front)."""
    lock = instance.hold("notes")
    if lock is None:
        req = _show_request()
        with contextlib.suppress(OSError):
            req.parent.mkdir(parents=True, exist_ok=True)
            req.write_text("1", encoding="utf-8")
        return 0
    with contextlib.suppress(OSError):
        _show_request().unlink(missing_ok=True)
    _log_to_file()
    if sys.platform == "win32":
        with contextlib.suppress(Exception):  # sharp text on high-DPI screens
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
    try:
        store = NoteStore()
    except Exception as e:
        log.exception("couldn't open the notes")
        messagebox.showerror("Notes", f"Couldn't open your notes ({e}).\n\nThey're kept in {notes_path()}.")
        return 1
    window = NotesWindow(store)
    if MAC:
        _mac_activate()
    window.run()
    del lock
    return 0
