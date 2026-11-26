"""Spoken commands: punctuation and breaks, and when a command phrase is just a word."""

import pytest

from dictate.cleanup.formatter import FormatOptions, format_raw
from dictate.cleanup.incremental import rules_for, safe_boundary
from dictate.config import parse_config


def fmt(text: str, lang: str = "en") -> str:
    cfg = parse_config("")
    cfg.asr.language = lang
    return format_raw(text, FormatOptions.from_config(cfg)).text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Hi Sam comma how are you question mark", "Hi Sam, how are you?"),
        # the recognizer's own punctuation around a command is replaced by what was dictated
        ("Hi Sam, comma, how are you? Question mark.", "Hi Sam, how are you?"),
        ("Hi Sam. Comma. How are you doing today", "Hi Sam, how are you doing today."),
        ("the deadline is Friday period no exceptions", "The deadline is Friday. No exceptions."),
        ("Things to buy colon milk comma eggs comma bread", "Things to buy: milk, eggs, bread."),
        ("Great job exclamation point", "Great job!"),
        ("he said open quote I'm done close quote and left", 'He said "I\'m done" and left.'),
        ("call me open paren after five close paren please", "Call me (after five) please."),
        ("wait for it dot dot dot done", "Wait for it… Done."),
        ("Thanks for the help next paragraph see you tomorrow", "Thanks for the help\n\nSee you tomorrow."),
        ("Dear Sam, new line, thanks for the update.", "Dear Sam,\nThanks for the update."),
    ],
)
def test_spoken_punctuation_and_breaks(raw: str, expected: str) -> None:
    assert fmt(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "The trial period ends tomorrow.",
        "It was a difficult period during the war.",
        "That period, honestly, was rough.",
        "I love the Oxford comma.",
        "There's a question mark over his future.",
        "Read the next paragraph carefully.",
        "We launched a new line of products.",
        "I wrote a new paragraph today.",
    ],
)
def test_command_words_used_as_words_are_kept(raw: str) -> None:
    assert fmt(raw) == raw


def test_breaks_at_the_edges_are_kept() -> None:
    assert fmt("new paragraph thanks for coming") == "\n\nThanks for coming."
    assert fmt("Dear team new line") == "Dear team\n"  # no period: the next words go on the next line
    assert fmt("New paragraph.") == "\n\n"


def test_spanish_and_french_punctuation() -> None:
    assert fmt("Hola coma qué tal punto nos vemos mañana punto y aparte adiós", "es") == (
        "Hola, qué tal. Nos vemos mañana.\n\nAdiós."
    )
    assert fmt("Bonjour virgule comment ça va point d'interrogation", "fr") == "Bonjour, comment ça va ?"
    assert fmt("Merci point à la ligne à bientôt", "fr") == "Merci.\nÀ bientôt."


@pytest.mark.parametrize(
    ("raw", "lang"),
    [
        ("Llego a las cinco en punto.", "es"),
        ("El valor es tres punto cinco.", "es"),
        ("Escribe a ana punto lee arroba gmail punto com.", "es"),
        ("Il fait trois virgule cinq degrés.", "fr"),
        ("C'est mon point de vue.", "fr"),
    ],
)
def test_spanish_and_french_words_kept(raw: str, lang: str) -> None:
    assert fmt(raw, lang).lower() == raw.lower()


def test_incremental_never_splits_around_spoken_punctuation() -> None:
    r = rules_for("en")
    assert not safe_boundary("I went to the store and bought milk comma.", "Eggs and bread.", r)
    assert not safe_boundary("I went to the store and bought milk.", "Period. Then I went home.", r)
    assert safe_boundary("I went to the store and bought milk.", "Then I went home.", r)
