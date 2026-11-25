import pytest

from dictate.cleanup.terms import apply_terms


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("we use github and postgresql", "we use GitHub and PostgreSQL"),
        ("I asked chat gpt and open ai support", "I asked ChatGPT and OpenAI support"),
        ("my i phone and mac book", "my iPhone and MacBook"),
        ("store it in pg vector", "store it in pgvector"),
        ("built with next js and type script", "built with Next.js and TypeScript"),
        ("open it in vs code", "open it in VS Code"),
        ("the json and yaml files", "the JSON and YAML files"),
        ("Nvidia GPUs", "NVIDIA GPUs"),
        ("the wifi is down", "the Wi-Fi is down"),
        ("share it on google docs", "share it on Google Docs"),
    ],
)
def test_terms_fixed(text: str, expected: str) -> None:
    assert apply_terms(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "go to github.com/openai/whisper",
        "email me at john@gmail.com",
        "open https://example.com",
        "the file docker-compose.yml",
        "set github_token in the env",
        "C:\\docker\\data",
        "we need to react quickly and slack off less",
        "the notion that zoom calls excel",
    ],
)
def test_terms_leave_urls_identifiers_and_common_words(text: str) -> None:
    assert apply_terms(text) == text


def test_terms_already_canonical_unchanged() -> None:
    text = "GitHub, PostgreSQL, iPhone, pgvector, NumPy."
    assert apply_terms(text) == text
