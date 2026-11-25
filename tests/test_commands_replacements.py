from dictate.cleanup.commands import apply_commands
from dictate.cleanup.replacements import apply_replacements
from dictate.config import Replacement

EN = {"new_line": ["new line"], "new_paragraph": ["new paragraph"], "scratch_that": ["scratch that"]}


def test_command_at_start_and_end() -> None:
    assert apply_commands("New paragraph. Hello", EN).text == "\n\nHello".strip(" ")
    assert apply_commands("Hello. New line.", EN).text == "Hello.\n"


def test_command_case_and_spacing_insensitive() -> None:
    assert apply_commands("Hi. NEW   LINE! Bye", EN).text == "Hi.\nBye"


def test_paragraph_wins_over_line() -> None:
    assert apply_commands("A. New paragraph. B.", EN).text == "A.\n\nB."


def test_scratch_with_commas() -> None:
    assert apply_commands("Monday, scratch that, Tuesday.", EN).text == "Tuesday."


def test_scratch_only_last_sentence() -> None:
    assert apply_commands("A one. B two. Scratch that. C three.", EN).text == "A one. C three."


def test_multiple_scratches() -> None:
    assert apply_commands("A. B. Scratch that. Scratch that. C.", EN).text == "C."


def test_no_phrases_is_noop() -> None:
    assert apply_commands("New line.", {}).text == "New line."


def test_replacement_whole_word_case_insensitive() -> None:
    reps = [Replacement("jason", "JSON")]
    assert apply_replacements("Jason sent jason data to jasonville", reps) == "JSON sent JSON data to jasonville"


def test_replacement_case_sensitive() -> None:
    reps = [Replacement("Dictate", "dictate", case_sensitive=True)]
    assert apply_replacements("Dictate and DICTATE", reps) == "dictate and DICTATE"


def test_replacement_longest_first() -> None:
    reps = [Replacement("new york", "NY"), Replacement("new york times", "NYT")]
    assert apply_replacements("read the new york times in new york", reps) == "read the NYT in NY"


def test_replacement_with_backslashes_is_literal() -> None:
    assert apply_replacements("path here", [Replacement("path", r"C:\new\dir")]) == r"C:\new\dir here"
