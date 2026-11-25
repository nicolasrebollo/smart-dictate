import pytest

from dictate.cleanup.identifiers import apply_identifiers


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Rename the variable snake case user id to snake case account id.",
            "Rename the variable user_id to account_id.",
        ),
        ("Call camel case get user name inside the constructor.", "Call getUserName inside the constructor."),
        ("Use pascal case user profile for the class name.", "Use UserProfile for the class name."),
        ("the kebab case main menu, rendered on the left", "the main-menu, rendered on the left"),
        ("set constant case max retries to five", "set MAX_RETRIES to five"),
        ("set screaming snake case api key now", "set API_KEY_NOW"),
        ("Set max underscore retries to five.", "Set max_retries to five."),
        ("the field created underscore at underscore utc", "the field created_at_utc"),
        ("Add the dash dash verbose flag.", "Add the --verbose flag."),
        ("snake case user id, then save", "user_id, then save"),
        ("Snake Case Total Count.", "total_count."),
    ],
)
def test_identifiers(raw: str, expected: str) -> None:
    assert apply_identifiers(raw) == expected


def test_plain_text_untouched() -> None:
    text = "In this case, the snake was in the case. A dash of salt, dash dash."
    assert apply_identifiers(text) == text


def test_cue_without_words_left_alone() -> None:
    assert apply_identifiers("I prefer snake case.") == "I prefer snake case."
