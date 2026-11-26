import pytest

from dictate.cleanup.vocab import apply_vocabulary

VOCAB = ["Okonkwo", "pgvector", "Siobhan", "Kubernetes", "PostgreSQL", "Nakamura", "Mark", "FluxCapacitor"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("I met Professor Okonko today.", "I met Professor Okonkwo today."),
        ("We store vectors in PG Vector.", "We store vectors in pgvector."),
        ("Deploy it on Kubernetis.", "Deploy it on Kubernetes."),
        ("Use Postgre SQL for this.", "Use PostgreSQL for this."),
        ("Ask Dr. Nakamora.", "Ask Dr. Nakamura."),
        ("Wire up the flux capacitor.", "Wire up the FluxCapacitor."),
    ],
)
def test_vocabulary_fixes_near_misses(raw: str, expected: str) -> None:
    assert apply_vocabulary(raw, VOCAB) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Please mark the task as done.",  # same word, different casing: left to the LLM
        "The market is open.",  # not similar enough
        "Go to okonko.example.com/pgvector",  # URLs/paths untouched
        "Email okonko@example.com",
        "Use pgvector and Kubernetes.",  # already right
        "We need more vectors.",
    ],
)
def test_vocabulary_leaves_other_text_alone(text: str) -> None:
    assert apply_vocabulary(text, VOCAB) == text


def test_empty_vocabulary_is_noop() -> None:
    assert apply_vocabulary("Okonko", []) == "Okonko"
