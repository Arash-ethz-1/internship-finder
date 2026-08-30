"""Problems 1 and 2: chunk_posting and chunk_profile_doc.

These check the contract, not the quality. A chunker that passes every test
here can still be a bad chunker — use `try_chunking.py` to look at the output
and `cli eval` later to measure it.
"""

from __future__ import annotations

import pytest

from agent_app.core.chunking import Chunk, chunk_posting, chunk_profile_doc
from exercises.helpers import make_posting

pytestmark = pytest.mark.filterwarnings("ignore")


def paragraphs(*lengths: int) -> str:
    """A body of paragraphs with the given character lengths."""
    return "\n\n".join("x" * n for n in lengths)


# --- problem 1: chunk_posting ----------------------------------------------


def test_returns_chunk_objects() -> None:
    chunks = chunk_posting(make_posting(paragraphs(500)))
    assert isinstance(chunks, list)
    assert all(isinstance(c, Chunk) for c in chunks)


def test_ordinals_are_contiguous_from_zero() -> None:
    chunks = chunk_posting(make_posting(paragraphs(900, 900, 900, 900)))
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_no_chunk_is_empty() -> None:
    # Blank runs and stray whitespace must not become chunks of their own.
    body = "First paragraph.\n\n\n\n   \n\nSecond paragraph."
    chunks = chunk_posting(make_posting(body))
    assert chunks, "a body with real text must produce at least one chunk"
    assert all(c.text.strip() for c in chunks)


def test_respects_max_chars() -> None:
    chunks = chunk_posting(make_posting(paragraphs(5000)), max_chars=500)
    assert chunks
    assert all(len(c.text) <= 500 for c in chunks), (
        "a single paragraph longer than max_chars still has to be split"
    )


def test_a_long_body_produces_several_chunks() -> None:
    chunks = chunk_posting(make_posting(paragraphs(1000, 1000, 1000, 1000)), max_chars=1200)
    assert len(chunks) >= 3, "4,000 chars at 1,200 per chunk cannot fit in two"


def test_a_short_body_is_one_chunk() -> None:
    chunks = chunk_posting(make_posting("A short posting."), max_chars=1200)
    assert len(chunks) == 1


def test_small_paragraphs_are_packed_together() -> None:
    # Ten 50-char paragraphs fit comfortably in one 1,200-char chunk. Emitting
    # ten fragments instead is the mistake this catches: a chunk reading
    # "Strong communication skills" matches every job ever posted.
    chunks = chunk_posting(make_posting(paragraphs(*([50] * 10))), max_chars=1200)
    assert len(chunks) <= 2


def test_is_deterministic() -> None:
    posting = make_posting(paragraphs(800, 400, 900))
    first = chunk_posting(posting)
    second = chunk_posting(posting)
    assert [(c.ordinal, c.text) for c in first] == [(c.ordinal, c.text) for c in second]


def test_keeps_the_content() -> None:
    # Whatever the boundaries, distinctive words must survive somewhere. If you
    # deliberately drop boilerplate, keep the parts that carry meaning.
    body = "We use PyTorch and Ray.\n\nYou will build inference pipelines in Rust."
    joined = " ".join(c.text for c in chunk_posting(make_posting(body)))
    for word in ("PyTorch", "Ray", "Rust"):
        assert word in joined, f"{word!r} disappeared"


def test_survives_an_empty_body() -> None:
    # Decide what an empty posting means and do it without raising.
    chunks = chunk_posting(make_posting(""))
    assert chunks == [] or all(c.text.strip() for c in chunks)


def test_handles_the_longest_realistic_posting() -> None:
    chunks = chunk_posting(make_posting(paragraphs(*([400] * 45))), max_chars=1200)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert all(len(c.text) <= 1200 for c in chunks)


# --- problem 2: chunk_profile_doc ------------------------------------------

MARKDOWN = """# pyblio

A command-line tool that keeps a BibTeX bibliography in sync with PDFs.

## What I built

I wrote the matching layer: it pairs an entry to a PDF using DOI first, then a
normalised title comparison, and refuses to guess when both fail.

## Numbers

1,200 entries, 940 PDFs. 96% matched automatically.
"""


def test_profile_doc_contract() -> None:
    chunks = chunk_profile_doc(MARKDOWN, max_chars=1200)
    assert chunks
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert all(c.text.strip() for c in chunks)
    assert all(len(c.text) <= 1200 for c in chunks)


def test_profile_doc_respects_a_small_max_chars() -> None:
    chunks = chunk_profile_doc(MARKDOWN, max_chars=120)
    assert all(len(c.text) <= 120 for c in chunks)
    assert len(chunks) > 1


def test_profile_doc_keeps_the_details() -> None:
    joined = " ".join(c.text for c in chunk_profile_doc(MARKDOWN))
    for fragment in ("BibTeX", "DOI", "96%"):
        assert fragment in joined, f"{fragment!r} disappeared"


def test_profile_doc_survives_no_headings() -> None:
    chunks = chunk_profile_doc("Just one paragraph with no markdown headings at all.")
    assert len(chunks) == 1


def test_profile_doc_survives_empty_input() -> None:
    chunks = chunk_profile_doc("")
    assert chunks == [] or all(c.text.strip() for c in chunks)
