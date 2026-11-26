"""Message layout: greeting and sign-off on their own lines, by rules."""

import pytest

from dictate.cleanup.layout import layout_message
from dictate.config import parse_config
from dictate.pipeline import Pipeline


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Hey Maria, thanks for the quick reply. The contract looks fine. Can we do net 30? Thanks, Nick.",
            "Hey Maria,\n\nThanks for the quick reply. The contract looks fine. Can we do net 30?\n\nThanks,\nNick",
        ),
        (
            "Dear Professor Lee, I'm writing about the deadline. Would an extension be possible? "
            "Best regards, Sam Rivera",
            "Dear Professor Lee,\n\nI'm writing about the deadline. Would an extension be possible?\n\n"
            "Best regards,\nSam Rivera",
        ),
        # paragraphs the cleanup already made are kept
        (
            "Hi team, the release moved to Thursday.\n\nPlease update your calendars. Cheers, Priya",
            "Hi team,\n\nThe release moved to Thursday.\n\nPlease update your calendars.\n\nCheers,\nPriya",
        ),
        ("Thanks for everything. Best, Nick", "Thanks for everything.\n\nBest,\nNick"),
        (
            "Hola Ana, gracias por el documento. Lo reviso mañana. Un abrazo, Nico",
            "Hola Ana,\n\nGracias por el documento. Lo reviso mañana.\n\nUn abrazo,\nNico",
        ),
    ],
)
def test_messages_are_laid_out(text: str, expected: str) -> None:
    assert layout_message(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Hi Ben, thanks for the notes. The second section reads much better now.",  # short: stays inline
        "Can you send it over? Thanks, Nick.",  # Nick may be the reader
        "Hey Maria, the deck looks great. Thanks Maria!",  # thanking the reader
        "Best of luck with the launch, everyone!",
    ],
)
def test_short_or_ambiguous_messages_are_left_alone(text: str) -> None:
    assert layout_message(text) == text


def test_pipeline_lays_out_but_respects_dictated_breaks() -> None:
    cfg = parse_config("")
    cfg.cleanup.enabled = False
    p = Pipeline(cfg, None, None)
    res = p.finish_stream(["Hi Sam, the report is done. It's on the drive. Best, Nick"], incremental=None)
    assert res.text == "Hi Sam,\n\nThe report is done. It's on the drive.\n\nBest,\nNick"
    # the speaker dictated their own layout: it wins
    res = p.finish_stream(["Hi Sam, new line, the report is done. It's on the drive. Best, Nick"], incremental=None)
    assert res.text == "Hi Sam,\nThe report is done. It's on the drive. Best, Nick."
    assert p.finish_stream(["Hi Sam, the report is done. Best, Nick"], incremental=None, raw=True).text.count("\n") == 0
