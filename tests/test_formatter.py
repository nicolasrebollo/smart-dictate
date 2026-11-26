import pytest

from dictate.cleanup.formatter import (
    FormatOptions,
    add_final_period,
    capitalize,
    collapse_repeats,
    format_raw,
    remove_fillers,
)
from dictate.config import parse_config


def opts(lang: str = "en", **kw) -> FormatOptions:
    cfg = parse_config("")
    cfg.asr.language = lang
    o = FormatOptions.from_config(cfg)
    for k, v in kw.items():
        setattr(o, k, v)
    return o


def fmt(text: str, lang: str = "en", **kw) -> str:
    return format_raw(text, opts(lang, **kw)).text


# -- fillers ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Um, I think we should go.", "I think we should go."),
        ("So, um, I think we should go.", "So, I think we should go."),
        ("I uh think we should go", "I think we should go."),
        ("I think, uh.", "I think."),
        ("Umm... yeah, that works.", "Yeah, that works."),
        ("Hmm, let me check. Uh, it's on Friday.", "Let me check. It's on Friday."),
        ("We need, erm, three more.", "We need three more."),
        ("Should we, uh, hire a contractor?", "Should we hire a contractor?"),
        ("um", ""),
    ],
)
def test_fillers_en(raw: str, expected: str) -> None:
    assert fmt(raw) == expected


def test_filler_words_inside_other_words_are_kept() -> None:
    assert fmt("The umbrella is under the drum.") == "The umbrella is under the drum."
    assert fmt("Summer is here, uh-huh.") == "Summer is here, uh-huh."
    assert fmt("The error was in the err log") == "The error was in the err log."


def test_ambiguous_fillers_are_left_for_the_llm() -> None:
    assert fmt("It was, like, really fast, you know.") == "It was, like, really fast, you know."


def test_fillers_es_fr() -> None:
    assert fmt("Eh, creo que sí.", "es") == "Creo que sí."
    assert fmt("Este proyecto está listo.", "es") == "Este proyecto está listo."
    assert fmt("Euh, je pense que oui.", "fr") == "Je pense que oui."
    assert fmt("C'est, euh, prêt.", "fr") == "C'est prêt."


def test_remove_fillers_disabled() -> None:
    assert fmt("Um, okay.", remove_fillers=False) == "Um, okay."


def test_remove_fillers_function_alone() -> None:
    assert remove_fillers("I uh think", "en").split() == ["I", "think"]


# -- repeats ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("I I think so.", "I think so."),
        ("The the main thing is time.", "The main thing is time."),
        ("We we we need more tests.", "We need more tests."),
        ("I, I don't know.", "I don't know."),
        ("Th- the plan is fine.", "The plan is fine."),
        ("I know that that is true.", "I know that that is true."),
        ("It was very very good.", "It was very very good."),
        ("No, no, that's wrong.", "No, no, that's wrong."),
        ("Call 5 5 5 1 2 3 4.", "Call 5 5 5 1 2 3 4."),
        ("He had had enough.", "He had had enough."),
    ],
)
def test_repeats_en(raw: str, expected: str) -> None:
    assert fmt(raw) == expected


def test_repeats_fr_reflexive_kept() -> None:
    assert fmt("Nous nous sommes vus hier.", "fr") == "Nous nous sommes vus hier."


def test_collapse_repeats_keeps_case_of_first() -> None:
    assert collapse_repeats("The the end", "en") == "The end"


# -- capitalization / punctuation ------------------------------------------------------
def test_capitalize_sentence_starts() -> None:
    assert capitalize("hello. how are you? fine!", "en") == "Hello. How are you? Fine!"


def test_capitalize_standalone_i_in_english_only() -> None:
    assert capitalize("so i think i'm right, i.e. correct", "en") == "So I think I'm right, i.e. correct"
    assert capitalize("i", "fr") == "I"  # sentence start still capitalized
    assert capitalize("oui i", "fr") == "Oui i"


def test_capitalize_respects_abbreviations() -> None:
    assert capitalize("bring fruit, e.g. apples.", "en") == "Bring fruit, e.g. apples."
    assert capitalize("ask dr. smith", "en") == "Ask dr. smith"


def test_capitalize_spanish_inverted_marks() -> None:
    assert capitalize("¿puedes venir? ¡claro!", "es") == "¿Puedes venir? ¡Claro!"


def test_capitalize_does_not_touch_numbers_or_urls() -> None:
    assert capitalize("at 5.30 pm see example.com", "en") == "At 5.30 pm see example.com"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello", "Hello."),
        ("Hello.", "Hello."),
        ("Really?", "Really?"),
        ('He said "yes"', 'He said "yes".'),
        ('He said "yes."', 'He said "yes."'),
        ("List:", "List:"),
        ("Hi\n", "Hi.\n"),
    ],
)
def test_final_period(text: str, expected: str) -> None:
    assert add_final_period(text) == expected


def test_final_period_can_be_disabled() -> None:
    assert fmt("sounds good", final_period=False) == "Sounds good"


def test_whitespace_and_orphan_punctuation() -> None:
    assert fmt("  hello   world ,  again  ") == "Hello world, again."
    assert fmt("Well, , okay") == "Well, okay."


def test_french_keeps_space_before_question_mark() -> None:
    assert fmt("Tu viens ?", "fr") == "Tu viens ?"


def test_already_clean_text_is_unchanged() -> None:
    text = "Hi Sarah, could you send me the slides by Friday? Thanks."
    assert fmt(text) == text


def test_identifiers_survive() -> None:
    text = "Call get_user_id() in utils.py and check the HTTP 404 response."
    assert fmt(text) == text


# -- commands wired through format_raw ----------------------------------------
def test_new_paragraph_command() -> None:
    assert fmt("Hi team. New paragraph. The release is Monday.") == "Hi team.\n\nThe release is Monday."


def test_new_line_command_mid_letter() -> None:
    assert fmt("Dear Sam, new line, thanks for the update.") == "Dear Sam,\nThanks for the update."


def test_commands_need_standalone_phrase() -> None:
    assert fmt("I wrote a new paragraph today.") == "I wrote a new paragraph today."
    assert fmt("New line of code is needed.") == "New line of code is needed."


def test_commands_disabled() -> None:
    assert fmt("Hi. New line. Bye.", commands_enabled=False) == "Hi. New line. Bye."


def test_scratch_that_within_utterance() -> None:
    assert fmt("Send it Monday. Scratch that. Send it Tuesday.") == "Send it Tuesday."
    assert fmt("First point. Second point. Scratch that. Third.") == "First point. Third."


def test_scratch_that_alone_flags_previous() -> None:
    res = format_raw("Scratch that.", opts())
    assert res.text == ""
    assert res.scratch_previous is True


def test_spanish_and_french_commands() -> None:
    assert fmt("Hola. Nuevo párrafo. Adiós.", "es") == "Hola.\n\nAdiós."
    assert fmt("Bonjour. Nouvelle ligne. Merci.", "fr") == "Bonjour.\nMerci."


def test_email_addresses_lowercased() -> None:
    assert fmt("Send it to John.Smith@Example.com today") == "Send it to john.smith@example.com today."
    assert fmt("Ping @Nick_Dev about it") == "Ping @Nick_Dev about it."
