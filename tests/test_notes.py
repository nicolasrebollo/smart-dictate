"""Notes: folders, saving, Recently deleted and search (no window needed)."""

import time
from datetime import date, datetime

import pytest

from dictate.notes import open_window
from dictate.notes.store import NoteStore, date_range, fold, short_time


@pytest.fixture
def store(tmp_path):
    s = NoteStore(tmp_path / "notes.db")
    yield s
    s.close()


def test_notes_live_in_a_file_and_survive_reopening(tmp_path) -> None:
    path = tmp_path / "sub" / "notes.db"
    s = NoteStore(path)
    folder = s.add_folder("School")
    note = s.add_note(folder, "Lecture", "Bring the laptop")
    s.close()
    s = NoteStore(path)
    got = s.get(note)
    assert got is not None and (got.title, got.body, got.folder_id) == ("Lecture", "Bring the laptop", folder)
    s.close()


def test_saving_only_moves_last_edited_when_something_changed(store) -> None:
    n = store.add_note(None, "A", "text")
    before = store.get(n).modified
    time.sleep(0.01)
    assert not store.save(n, "A", "text")
    assert store.get(n).modified == before
    assert store.save(n, "A", "more text")
    assert store.get(n).modified > before
    assert store.get(n).created < store.get(n).modified


def test_untitled_notes_are_named_after_their_first_line(store) -> None:
    n = store.add_note(None, "", "\n  Groceries: milk, eggs\nmore")
    assert store.get(n).display_title == "Groceries: milk, eggs"
    assert store.get(store.add_note()).display_title == "Untitled"


def test_folders_nest_and_search_covers_subfolders(store) -> None:
    school = store.add_folder("School")
    csc = store.add_folder("CSC 108", school)
    work = store.add_folder("Work")
    store.add_note(csc, "Recursion", "base case")
    store.add_note(school, "Timetable", "")
    store.add_note(work, "Standup", "")
    store.add_note(None, "Loose", "")
    assert store.folder_path(csc) == "School / CSC 108"
    assert {n.title for n in store.search(folder_id=school)} == {"Recursion", "Timetable"}
    assert {n.title for n in store.search(folder_id=csc)} == {"Recursion"}
    assert len(store.search()) == 4
    assert store.counts() == {csc: 1, school: 1, work: 1, None: 1}


def test_a_folder_cannot_move_inside_itself(store) -> None:
    a = store.add_folder("A")
    b = store.add_folder("B", a)
    assert not store.move_folder(a, b)
    assert not store.move_folder(a, a)
    assert store.move_folder(b, None)
    assert store.move_folder(a, b)
    assert store.folder_path(a) == "B / A"


def test_search_every_word_any_case_and_accent(store) -> None:
    store.add_note(None, "Résumé draft", "Send to the café owner")
    store.add_note(None, "Shopping", "coffee beans")
    assert [n.title for n in store.search("resume")] == ["Résumé draft"]
    assert [n.title for n in store.search("CAFE owner")] == ["Résumé draft"]
    assert [n.title for n in store.search("owner cafe")] == ["Résumé draft"]
    assert store.search("cafe beans") == []
    assert fold("Ça Ñandú") == "ca nandu"


def test_search_title_only_or_text_only(store) -> None:
    store.add_note(None, "Budget", "rent and groceries")
    store.add_note(None, "Groceries", "milk")
    assert {n.title for n in store.search("groceries")} == {"Budget", "Groceries"}
    assert [n.title for n in store.search("groceries", where="title")] == ["Groceries"]
    assert [n.title for n in store.search("groceries", where="body")] == ["Budget"]


def test_search_by_created_or_edited_date_and_sort(store) -> None:
    old = store.add_note(None, "Old", "")
    new = store.add_note(None, "New", "")
    nov1 = datetime(2026, 11, 1, 9).timestamp()
    nov20 = datetime(2026, 11, 20, 9).timestamp()
    store.db.execute("UPDATE notes SET created = ?, modified = ? WHERE id = ?", (nov1, nov20, old))
    store.db.execute("UPDATE notes SET created = ?, modified = ? WHERE id = ?", (nov20 - 3600, nov20 - 3600, new))
    day = date_range("2026-11-01")
    assert [n.title for n in store.search(dates=day, date_field="created")] == ["Old"]
    assert store.search(dates=day, date_field="modified") == []
    assert [n.title for n in store.search(dates=date_range("2026-11-20"))] == ["Old", "New"]  # edited, newest first
    assert [n.title for n in store.search(sort="created")] == ["New", "Old"]
    assert [n.title for n in store.search(sort="title")] == ["New", "Old"]


def test_date_filters() -> None:
    today = date(2026, 11, 26)
    start, end = date_range("Today", today)
    assert (end - start) / 3600 in (23, 24, 25)  # a day (or a daylight-saving one)
    assert date_range("Any time", today) is None
    assert date_range("", today) is None
    assert date_range("Last 7 days", today)[0] == datetime(2026, 11, 20).timestamp()
    assert date_range("2026-11") == (datetime(2026, 11, 1).timestamp(), datetime(2026, 12, 1).timestamp())
    assert date_range("2026-12") == (datetime(2026, 12, 1).timestamp(), datetime(2027, 1, 1).timestamp())
    assert date_range("2026") == (datetime(2026, 1, 1).timestamp(), datetime(2027, 1, 1).timestamp())
    assert date_range("11/20/2026") == date_range("2026-11-20")
    assert date_range("2026-11-01 to 2026-11-15") == (
        datetime(2026, 11, 1).timestamp(),
        datetime(2026, 11, 16).timestamp(),
    )
    for bad in ("tomorrowish", "2026-13", "2026-02-30"):
        with pytest.raises(ValueError):
            date_range(bad)


def test_deleted_notes_wait_in_recently_deleted(store) -> None:
    keep = store.add_note(None, "Keep", "")
    gone = store.add_note(None, "Gone", "")
    store.delete_note(gone)
    assert [n.title for n in store.search()] == ["Keep"]
    assert [n.title for n in store.search(deleted=True)] == ["Gone"]
    store.restore_note(gone)
    assert {n.title for n in store.search()} == {"Keep", "Gone"}
    store.delete_note(gone)
    store.delete_note(gone)  # a second time: gone for good
    assert store.get(gone) is None
    assert store.get(keep) is not None


def test_old_deleted_notes_are_cleared_after_30_days(store) -> None:
    n = store.add_note(None, "Old", "")
    store.delete_note(n)
    store.db.execute("UPDATE notes SET deleted = ? WHERE id = ?", (time.time() - 31 * 86400, n))
    assert store.purge_deleted() == 1
    assert store.get(n) is None


def test_deleting_a_folder_keeps_its_notes_in_recently_deleted(store) -> None:
    school = store.add_folder("School")
    csc = store.add_folder("CSC", school)
    store.add_note(school, "A", "")
    b = store.add_note(csc, "B", "")
    store.add_note(None, "C", "")
    assert store.delete_folder(school) == 2
    assert store.folders() == []
    assert [n.title for n in store.search()] == ["C"]
    assert {n.title for n in store.search(deleted=True)} == {"A", "B"}
    store.restore_note(b)  # its folder is gone: it comes back with no folder
    assert store.get(b).folder_id is None


def test_list_times_read_naturally() -> None:
    now = datetime(2026, 11, 26, 15, 0).timestamp()
    assert short_time(datetime(2026, 11, 26, 9, 5).timestamp(), now) == "9:05 AM"
    assert short_time(datetime(2026, 11, 25, 9, 5).timestamp(), now) == "Yesterday"
    assert short_time(datetime(2026, 3, 2).timestamp(), now) == "Mar 2"
    assert short_time(datetime(2025, 3, 2).timestamp(), now) == "Mar 2, 2025"


def test_the_menu_opens_notes_in_a_process_of_its_own() -> None:
    started = []
    open_window(lambda args, **kw: started.append(args))
    assert started and started[0][-1] in ("notes", "--notes")
