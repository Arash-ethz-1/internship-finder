"""Splitting postings and profile docs into retrievable chunks.

**Category B.** The two functions below are written by hand by the author.

Why this is not delegated: every retrieval number in the project is downstream
of this choice. A 5,000-word posting embedded as one vector averages into
mush and matches nothing precisely; split mid-sentence and you get fragments
that match nothing at all. Chunk size and boundary rules set the ceiling on
what `recall_at_k` can ever report, so measuring retrieval is measuring this.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..db import Posting

NOT_IMPLEMENTED = "Category B — author writes this by hand"

# A sensible starting point, not a rule. Tune it and watch `cli eval` move.
DEFAULT_MAX_CHARS = 1200


@dataclass(frozen=True)
class Chunk:
    """One retrievable piece of a document.

    ``ordinal`` is the chunk's position within its source document, starting
    at 0, and is what lets the dashboard show a hit in context.
    """

    text: str
    ordinal: int


def chunk_posting(posting: Posting, max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    """Split a posting body into retrievable chunks.

    Must hold:

    * ``ordinal`` runs 0, 1, 2, … in document order with no gaps.
    * No chunk's ``text`` is empty or whitespace-only.
    * Every chunk is at most ``max_chars`` characters.
    * The same posting always produces the same chunks — no randomness, no
      dependence on wall-clock time. Re-ingestion relies on this.

    Worth deciding deliberately: whether the title and company are prepended
    to each chunk so a hit carries its own context, whether chunks overlap,
    and whether the split respects paragraph and bullet boundaries (bodies
    come out of ``ingest/normalize.py`` with ``\\n\\n`` between blocks and
    ``- `` before list items).

    Category B — author implements.
    """
    raise NotImplementedError(NOT_IMPLEMENTED)


def chunk_profile_doc(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    """Split one of the author's project write-ups into retrievable chunks.

    Same contract as :func:`chunk_posting`. The input is the raw markdown of
    a file in ``profile/``; the caller already knows which document it is and
    records that separately as ``chunks.profile_doc``.

    These chunks are what the letter drafter retrieves from, so a chunk that
    loses which project it belongs to produces a letter that attributes work
    to the wrong thing. Markdown headings are the obvious boundary.

    Category B — author implements.
    """
    raise NotImplementedError(NOT_IMPLEMENTED)
