"""Splitting postings and profile docs into retrievable chunks.

**Category B**, written by Claude at the author's request on 2026-08-31
("give me your best version"). The author's own draft is in git history.

Why this matters: every retrieval number in the project is downstream of this
choice. A 5,000-word posting embedded as one vector averages into mush and
matches nothing precisely; split mid-sentence and you get fragments that match
nothing at all. Chunk size and boundary rules set the ceiling on what
`recall_at_k` can ever report, so measuring retrieval is measuring this.

The shape of both functions is the same: cut the document on the boundaries it
already carries (blank lines, markdown headings), break anything still too big
on sentences and then on spaces, then greedily pack the pieces back up to the
limit. Every chunk carries a prefix naming where it came from, so a hit read
on its own still says which posting or which project it belongs to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..db import Posting

NOT_IMPLEMENTED = "Category B — author writes this by hand"

# A sensible starting point, not a rule. Tune it and watch `cli eval` move.
DEFAULT_MAX_CHARS = 1200

_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$")
_FENCE = re.compile(r"^\s*(?:```|~~~)")


@dataclass(frozen=True)
class Chunk:
    """One retrievable piece of a document.

    ``ordinal`` is the chunk's position within its source document, starting
    at 0, and is what lets the dashboard show a hit in context.
    """

    text: str
    ordinal: int


def _blocks(text: str) -> list[str]:
    """Paragraphs and bullets, stripped, with blank runs dropped."""
    return [block.strip() for block in _PARAGRAPH.split(text) if block.strip()]


def _fit(block: str, limit: int) -> list[str]:
    """Break one over-long block into pieces that fit: sentences, then spaces."""
    if len(block) <= limit:
        return [block]
    pieces: list[str] = []
    for sentence in _SENTENCE.split(block):
        piece = sentence.strip()
        while len(piece) > limit:
            cut = piece.rfind(" ", 0, limit + 1)
            if cut <= 0:
                cut = limit  # one unbroken run of characters; cut it blind
            head, piece = piece[:cut].strip(), piece[cut:].strip()
            if head:
                pieces.append(head)
        if piece:
            pieces.append(piece)
    return pieces


def _pack(pieces: list[str], limit: int) -> list[str]:
    """Greedily fill chunks up to ``limit``, rejoining on the blank line.

    Every piece is assumed to fit already — run them through :func:`_fit`
    first. Packing beats emitting each piece alone: a 200-char paragraph on
    its own is a fragment that matches nothing.
    """
    packed: list[str] = []
    buffer = ""
    for piece in pieces:
        if not buffer:
            buffer = piece
        elif len(buffer) + 2 + len(piece) <= limit:
            buffer = f"{buffer}\n\n{piece}"
        else:
            packed.append(buffer)
            buffer = piece
    if buffer:
        packed.append(buffer)
    return packed


def _with_prefix(prefix: str, max_chars: int) -> tuple[str, int]:
    """Charge the prefix against the budget, or drop it if it is too greedy.

    At a small ``max_chars`` a long title would leave no room for content, and
    a chunk that is all context and no text is worse than one with no context.
    """
    if len(prefix) > max_chars // 2:
        return "", max_chars
    return prefix, max_chars - len(prefix)


def chunk_posting(posting: Posting, max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    """Split a posting body into retrievable chunks.

    Holds: ordinals run 0, 1, 2, … with no gaps; no chunk is empty or
    whitespace-only; every chunk is at most ``max_chars`` characters; and the
    same posting always produces the same chunks, which is what makes
    re-ingestion cheap.

    Bodies come out of ``ingest/normalize.py`` with ``\n\n`` between blocks
    and ``- `` before list items, so blank lines are the boundary. Title and
    company are prepended to every chunk — a requirements bullet retrieved on
    its own is useless if it does not say which job it is from. Chunks do not
    overlap; the packing keeps neighbouring paragraphs together instead.
    """
    prefix, limit = _with_prefix(f"{posting.title} ({posting.company})\n\n", max_chars)
    pieces = [piece for block in _blocks(posting.body) for piece in _fit(block, limit)]
    return [
        Chunk(text=prefix + text, ordinal=ordinal)
        for ordinal, text in enumerate(_pack(pieces, limit))
    ]


def _sections(markdown: str) -> list[tuple[str, str]]:
    """Split markdown into ``(breadcrumb, body)`` pairs, one per heading.

    The breadcrumb is the heading trail — ``pyblio > What I built`` — so a
    chunk still names its project after it is torn out of the document.
    Headings inside fenced code blocks are text, not structure.
    """
    trail: dict[int, str] = {}
    sections: list[tuple[str, str]] = []
    body: list[str] = []
    fenced = False

    def breadcrumb() -> str:
        return " > ".join(trail[level] for level in sorted(trail))

    for line in markdown.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        heading = None if fenced else _HEADING.match(line)
        if heading is None:
            body.append(line)
            continue
        if any(line.strip() for line in body):
            sections.append((breadcrumb(), "\n".join(body)))
        body = []
        level = len(heading.group(1))
        trail = {lv: title for lv, title in trail.items() if lv < level}
        trail[level] = heading.group(2).strip()

    if any(line.strip() for line in body):
        sections.append((breadcrumb(), "\n".join(body)))
    if not sections and markdown.strip():
        # Headings and nothing else. Keep the words rather than return nothing.
        sections.append(("", markdown))
    return sections


def chunk_profile_doc(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    """Split one of the author's project write-ups into retrievable chunks.

    Same contract as :func:`chunk_posting`. The input is the raw markdown of
    a file in ``profile/``; the caller already knows which document it is and
    records that separately as ``chunks.profile_doc``.

    Sections never share a chunk, even when two short ones would fit together:
    these chunks are what the letter drafter retrieves from, and merging *What
    I built* with the next project's *Numbers* is how a letter ends up
    attributing work to the wrong thing.
    """
    chunks: list[Chunk] = []
    for crumb, body in _sections(text):
        prefix, limit = _with_prefix(f"{crumb}\n\n" if crumb else "", max_chars)
        pieces = [piece for block in _blocks(body) for piece in _fit(block, limit)]
        for packed in _pack(pieces, limit):
            chunks.append(Chunk(text=prefix + packed, ordinal=len(chunks)))
    return chunks
