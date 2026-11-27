"""Where notes live: one SQLite file on this computer (no account, no server, no sync).

  Windows: %LOCALAPPDATA%\\dictate\\notes\\notes.db      Mac: ~/Library/Application Support/dictate/notes/notes.db

Folders nest. A deleted note goes to "Recently deleted" first (and is gone for good after 30 days);
deleting a folder deletes its subfolders and sends their notes there too. Times are Unix seconds (UTC).
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
import time
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from dictate import paths

KEEP_DELETED_DAYS = 30
SCHEMA_VERSION = 1


def notes_path() -> Path:
    return paths.data_dir() / "notes" / "notes.db"


@dataclass(frozen=True)
class Folder:
    id: int
    name: str
    parent_id: int | None


@dataclass(frozen=True)
class Note:
    id: int
    folder_id: int | None
    title: str
    body: str
    created: float
    modified: float
    deleted: float | None = None

    @property
    def display_title(self) -> str:
        if self.title.strip():
            return self.title.strip()
        first = next((line.strip() for line in self.body.splitlines() if line.strip()), "")
        return first[:80] or "Untitled"


def fold(text: str | None) -> str:
    """Lower case without accents, so "cafe" finds "Café" and "resume" finds "Résumé"."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


# -- dates in the search ------------------------------------------------------------------------------
DATE_PRESETS = ["Any time", "Today", "Yesterday", "Last 7 days", "Last 30 days", "This month", "This year"]
_ISO = re.compile(r"^(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?$")
_US = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _day_start(d: date) -> float:
    return datetime(d.year, d.month, d.day).timestamp()  # local midnight


def _one(text: str) -> tuple[float, float] | None:
    """A single date or period: 2026, 2026-11, 2026-11-20 or 11/20/2026."""
    if m := _ISO.match(text):
        y, mo, d = int(m[1]), m[2] and int(m[2]), m[3] and int(m[3])
        try:
            if d:
                start = date(y, mo, d)
                return _day_start(start), _day_start(start + timedelta(days=1))
            if mo:
                start = date(y, mo, 1)
                end = date(y + (mo == 12), mo % 12 + 1, 1)
                return _day_start(start), _day_start(end)
            return _day_start(date(y, 1, 1)), _day_start(date(y + 1, 1, 1))
        except ValueError:
            return None
    if m := _US.match(text):
        try:
            start = date(int(m[3]), int(m[1]), int(m[2]))
        except ValueError:
            return None
        return _day_start(start), _day_start(start + timedelta(days=1))
    return None


def date_range(text: str, today: date | None = None) -> tuple[float, float] | None:
    """The [start, end) a date filter means, or None for "any time". Raises ValueError if it can't be
    read. Takes the presets above, a date or period (2026-11-20, 2026-11, 2026, 11/20/2026) or a range
    of two ("2026-11-01 to 2026-11-15")."""
    text = text.strip()
    key = text.lower()
    if key in ("", "any time", "any date", "all"):
        return None
    today = today or date.today()
    tomorrow = _day_start(today + timedelta(days=1))
    presets = {
        "today": (_day_start(today), tomorrow),
        "yesterday": (_day_start(today - timedelta(days=1)), _day_start(today)),
        "last 7 days": (_day_start(today - timedelta(days=6)), tomorrow),
        "last 30 days": (_day_start(today - timedelta(days=29)), tomorrow),
        "this month": (_day_start(today.replace(day=1)), tomorrow),
        "this year": (_day_start(today.replace(month=1, day=1)), tomorrow),
    }
    if key in presets:
        return presets[key]
    parts = re.split(r"\s+to\s+|\s*\.\.\s*|\s+-\s+", text)
    if len(parts) == 2:
        a, b = _one(parts[0].strip()), _one(parts[1].strip())
        if a and b:
            return min(a[0], b[0]), max(a[1], b[1])
    elif got := _one(text):
        return got
    raise ValueError(f"can't read the date {text!r}")


# -- the store ----------------------------------------------------------------------------------------
class NoteStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else notes_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), isolation_level=None)  # autocommit
        self.db.row_factory = sqlite3.Row
        self.db.create_function("fold", 1, fold, deterministic=True)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA busy_timeout = 3000")
        self._migrate()
        self.purge_deleted()

    def close(self) -> None:
        self.db.close()

    @contextlib.contextmanager
    def _tx(self) -> Iterator[None]:
        """Several changes that happen together or not at all."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        self.db.executescript(
            """
            BEGIN;
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
                created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY,
                folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
                title TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL,
                modified REAL NOT NULL,
                deleted REAL
            );
            CREATE INDEX IF NOT EXISTS notes_folder ON notes(folder_id);
            PRAGMA user_version = 1;
            COMMIT;
            """
        )

    # -- folders ------------------------------------------------------------------------------------------
    def folders(self) -> list[Folder]:
        rows = self.db.execute("SELECT id, name, parent_id FROM folders ORDER BY name COLLATE NOCASE, id")
        return [Folder(r["id"], r["name"], r["parent_id"]) for r in rows]

    def add_folder(self, name: str, parent_id: int | None = None) -> int:
        name = name.strip() or "New folder"
        cur = self.db.execute(
            "INSERT INTO folders (name, parent_id, created) VALUES (?, ?, ?)", (name, parent_id, time.time())
        )
        return int(cur.lastrowid or 0)

    def rename_folder(self, folder_id: int, name: str) -> None:
        if name.strip():
            self.db.execute("UPDATE folders SET name = ? WHERE id = ?", (name.strip(), folder_id))

    def move_folder(self, folder_id: int, parent_id: int | None) -> bool:
        """Put a folder inside another one (None: top level). False if that would put it inside itself."""
        if parent_id is not None and parent_id in self.subtree(folder_id):
            return False
        self.db.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (parent_id, folder_id))
        return True

    def subtree(self, folder_id: int) -> set[int]:
        """The folder and every folder inside it, at any depth."""
        rows = self.db.execute(
            """WITH RECURSIVE sub(id) AS (SELECT ? UNION SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id)
               SELECT id FROM sub""",
            (folder_id,),
        )
        return {r[0] for r in rows}

    def delete_folder(self, folder_id: int) -> int:
        """Delete the folder and its subfolders; their notes go to Recently deleted. Returns how many."""
        ids = sorted(self.subtree(folder_id))
        marks = ",".join("?" * len(ids))
        now = time.time()
        with self._tx():
            cur = self.db.execute(
                f"UPDATE notes SET deleted = ?, folder_id = NULL WHERE folder_id IN ({marks}) AND deleted IS NULL",
                (now, *ids),
            )
            self.db.execute(f"UPDATE notes SET folder_id = NULL WHERE folder_id IN ({marks})", ids)
            self.db.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        return cur.rowcount

    def folder_path(self, folder_id: int | None) -> str:
        """'School / CSC 108' (empty for no folder)."""
        names: list[str] = []
        by_id = {f.id: f for f in self.folders()}
        seen: set[int] = set()
        while folder_id is not None and folder_id in by_id and folder_id not in seen:
            seen.add(folder_id)
            names.append(by_id[folder_id].name)
            folder_id = by_id[folder_id].parent_id
        return " / ".join(reversed(names))

    def counts(self) -> dict[int | None, int]:
        """Notes directly in each folder (None: in no folder), not counting deleted ones."""
        rows = self.db.execute("SELECT folder_id, COUNT(*) FROM notes WHERE deleted IS NULL GROUP BY folder_id")
        return {r[0]: r[1] for r in rows}

    def deleted_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM notes WHERE deleted IS NOT NULL").fetchone()[0]

    # -- notes ------------------------------------------------------------------------------------------
    @staticmethod
    def _note(r: sqlite3.Row) -> Note:
        return Note(r["id"], r["folder_id"], r["title"], r["body"], r["created"], r["modified"], r["deleted"])

    def get(self, note_id: int) -> Note | None:
        r = self.db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return self._note(r) if r else None

    def add_note(self, folder_id: int | None = None, title: str = "", body: str = "") -> int:
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO notes (folder_id, title, body, created, modified) VALUES (?, ?, ?, ?, ?)",
            (folder_id, title, body, now, now),
        )
        return int(cur.lastrowid or 0)

    def save(self, note_id: int, title: str, body: str) -> bool:
        """Save the text. "Last edited" only moves when something actually changed. True if it did."""
        cur = self.db.execute(
            "UPDATE notes SET title = ?, body = ?, modified = ? WHERE id = ? AND (title IS NOT ? OR body IS NOT ?)",
            (title, body, time.time(), note_id, title, body),
        )
        return cur.rowcount > 0

    def move_note(self, note_id: int, folder_id: int | None) -> None:
        self.db.execute("UPDATE notes SET folder_id = ? WHERE id = ?", (folder_id, note_id))

    def delete_note(self, note_id: int) -> None:
        """To Recently deleted (or gone for good, if it was already there)."""
        note = self.get(note_id)
        if note is None:
            return
        if note.deleted is not None:
            self.db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        else:
            self.db.execute("UPDATE notes SET deleted = ? WHERE id = ?", (time.time(), note_id))

    def restore_note(self, note_id: int) -> None:
        self.db.execute(
            """UPDATE notes SET deleted = NULL,
                   folder_id = CASE WHEN folder_id IN (SELECT id FROM folders) THEN folder_id END
               WHERE id = ?""",
            (note_id,),
        )

    def empty_deleted(self) -> int:
        return self.db.execute("DELETE FROM notes WHERE deleted IS NOT NULL").rowcount

    def purge_deleted(self, days: int = KEEP_DELETED_DAYS) -> int:
        cutoff = time.time() - days * 86400
        return self.db.execute("DELETE FROM notes WHERE deleted IS NOT NULL AND deleted < ?", (cutoff,)).rowcount

    def search(
        self,
        text: str = "",
        *,
        folder_id: int | None = None,
        deleted: bool = False,
        where: str = "all",
        dates: tuple[float, float] | None = None,
        date_field: str = "modified",
        sort: str = "modified",
    ) -> list[Note]:
        """Notes matching every word of `text` (in any order, ignoring case and accents).

        folder_id: only that folder and its subfolders (None: every folder). deleted: Recently deleted
        instead. where: "all" (title and text), "title" or "body". dates: [start, end) on `date_field`
        ("modified" or "created"). sort: "modified" / "created" (newest first) or "title".
        """
        sql = ["SELECT * FROM notes WHERE deleted IS " + ("NOT NULL" if deleted else "NULL")]
        args: list[object] = []
        if folder_id is not None and not deleted:
            ids = sorted(self.subtree(folder_id))
            sql.append(f"AND folder_id IN ({','.join('?' * len(ids))})")
            args += ids
        columns = {"title": ["title"], "body": ["body"]}.get(where, ["title", "body"])
        for word in fold(text).split():
            sql.append("AND (" + " OR ".join(f"instr(fold({c}), ?) > 0" for c in columns) + ")")
            args += [word] * len(columns)
        if dates is not None:
            field = "created" if date_field == "created" else "modified"
            sql.append(f"AND {field} >= ? AND {field} < ?")
            args += list(dates)
        order = "deleted DESC" if deleted else "created DESC" if sort == "created" else "modified DESC"
        sql.append(f"ORDER BY {order}, id DESC")
        notes = [self._note(r) for r in self.db.execute(" ".join(sql), args)]
        if sort == "title" and not deleted:
            notes.sort(key=lambda n: fold(n.display_title))  # untitled notes sort by their first line
        return notes


# -- how times read -------------------------------------------------------------------------------------
def _clock(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def short_time(ts: float, now: float | None = None) -> str:
    """For the list: "3:14 PM" today, "Yesterday", "Nov 20" this year, "Nov 20, 2025" before."""
    dt, today = datetime.fromtimestamp(ts), datetime.fromtimestamp(now if now is not None else time.time()).date()
    if dt.date() == today:
        return _clock(dt)
    if dt.date() == today - timedelta(days=1):
        return "Yesterday"
    if dt.year == today.year:
        return f"{dt:%b} {dt.day}"
    return f"{dt:%b} {dt.day}, {dt.year}"


def long_time(ts: float) -> str:
    """For the note itself: "Nov 26, 2026 at 3:14 PM"."""
    dt = datetime.fromtimestamp(ts)
    return f"{dt:%b} {dt.day}, {dt.year} at {_clock(dt)}"
