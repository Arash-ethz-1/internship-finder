"""Editing the project write-ups the letter drafter grounds in.

`profile/` is the only record of what the author has actually done, and every
letter is built from it. Editing it in a text editor and then *remembering* to
run `cli ingest-profile` is a silent footgun: the letters keep being drafted
from the old chunks, and nothing anywhere says so. Saving through this router
rewrites the file, re-chunks it and marks the chunks for embedding in one step,
so the two can never drift.

Re-embedding is the one part that is not immediate. Chunking is free and local;
turning chunks into vectors is the step that costs, so a saved document is
searchable by keyword at once and by meaning after the next `cli embed`. The
response says which, rather than pretending it is all done.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ..config import Settings, get_settings
from ..ingest.profile import SKIP, doc_slug
from ..runtime import get_db
from .schemas import ProfileDoc, ProfileDocBody, ProfileList, ProfileSummary

router = APIRouter(prefix="/api/profile", tags=["profile"])

Conn = Annotated[sqlite3.Connection, Depends(get_db)]

# A slug is a filename, so it must not be able to climb out of `profile/`.
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _settings() -> Settings:
    settings = get_settings()
    settings.ensure_dirs()
    return settings


def _path_for(slug: str) -> Path:
    """Resolve a slug to a file inside `profile/`, or refuse it.

    The pattern rejects separators and dots outright, and the containment check
    is the belt to that braces: a path parameter that reaches the filesystem is
    exactly where directory traversal lives.
    """
    if not _SLUG.match(slug):
        raise HTTPException(422, f"{slug!r} is not a valid document name")

    directory = _settings().profile_dir.resolve()
    path = (directory / f"{slug}.md").resolve()
    if path.parent != directory:
        raise HTTPException(422, f"{slug!r} is not a valid document name")
    return path


def _chunk_counts(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """Per document: how many chunks it has, and how many are embedded."""
    return {
        row["profile_doc"]: (row["chunks"], row["embedded"])
        for row in conn.execute(
            "SELECT profile_doc, count(*) AS chunks, "
            "sum(CASE WHEN vector_row IS NOT NULL THEN 1 ELSE 0 END) AS embedded "
            "FROM chunks WHERE profile_doc IS NOT NULL GROUP BY profile_doc"
        )
    }


@router.get("", response_model=ProfileList)
def list_docs(conn: Conn) -> ProfileList:
    """Every write-up, with how much of it is currently searchable."""
    directory = _settings().profile_dir
    counts = _chunk_counts(conn)

    docs: list[ProfileSummary] = []
    for path in sorted(directory.glob("*.md")) if directory.exists() else []:
        slug = doc_slug(path)
        chunks, embedded = counts.get(slug, (0, 0))
        docs.append(
            ProfileSummary(
                slug=slug,
                title=_first_heading(path) or slug,
                bytes=path.stat().st_size,
                chunks=chunks,
                embedded=embedded,
                # README and the placeholder are deliberately not grounded in:
                # a letter built on example text is how a letter starts lying.
                ingested=slug not in SKIP,
            )
        )
    return ProfileList(documents=docs, pending_embedding=sum(c - e for c, e in counts.values()))


@router.get("/{slug}", response_model=ProfileDoc)
def get_doc(conn: Conn, slug: str) -> ProfileDoc:
    """One write-up in full."""
    path = _path_for(slug)
    if not path.exists():
        raise HTTPException(404, f"No profile document named {slug!r}")

    chunks, embedded = _chunk_counts(conn).get(slug, (0, 0))
    return ProfileDoc(
        slug=slug,
        title=_first_heading(path) or slug,
        text=path.read_text(encoding="utf-8"),
        chunks=chunks,
        embedded=embedded,
        ingested=slug not in SKIP,
    )


@router.put("/{slug}", response_model=ProfileDoc)
def put_doc(conn: Conn, slug: str, body: ProfileDocBody) -> ProfileDoc:
    """Write a document and re-chunk it in the same request.

    The old chunks are deleted rather than updated. They describe text that no
    longer exists, and a letter grounded in a paragraph the author has since
    rewritten is exactly the failure this whole corpus exists to avoid.
    """
    from ..core.chunking import chunk_profile_doc

    path = _path_for(slug)
    text = body.text

    path.write_text(text, encoding="utf-8")

    chunks = [] if slug in SKIP else chunk_profile_doc(text)
    with conn:
        conn.execute("DELETE FROM chunks WHERE profile_doc = ?", (slug,))
        if chunks:
            conn.executemany(
                "INSERT INTO chunks (profile_doc, ordinal, text) VALUES (?, ?, ?)",
                [(slug, chunk.ordinal, chunk.text) for chunk in chunks],
            )

    return ProfileDoc(
        slug=slug,
        title=_first_heading(path) or slug,
        text=text,
        chunks=len(chunks),
        embedded=0,
        ingested=slug not in SKIP,
    )


@router.delete("/{slug}", status_code=204)
def delete_doc(conn: Conn, slug: str) -> None:
    """Remove a write-up and the chunks built from it."""
    path = _path_for(slug)
    if not path.exists():
        raise HTTPException(404, f"No profile document named {slug!r}")

    path.unlink()
    with conn:
        conn.execute("DELETE FROM chunks WHERE profile_doc = ?", (slug,))


def _first_heading(path: Path) -> str | None:
    """The document's own title, for a list that reads like prose."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                return line.lstrip("#").strip() or None
            if line.strip():
                break
    except OSError:
        return None
    return None
