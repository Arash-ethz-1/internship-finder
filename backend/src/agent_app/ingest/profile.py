"""Ingesting the author's own project write-ups.

The second corpus. Job postings tell you what a company wants; this folder is
the only record of what the author has actually done, and the letter drafter
retrieves from it exclusively.

Category A except the call into :func:`agent_app.core.chunking.chunk_profile_doc`,
which is Category B and will raise until it is written.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings, get_settings

log = logging.getLogger(__name__)

# Not ingested: one explains the format and the other is placeholder text, and
# grounding a letter in placeholder text is how a letter starts lying.
SKIP = frozenset({"readme", "example-project"})


@dataclass
class ProfileReport:
    """What one ``cli ingest-profile`` run did."""

    documents: int = 0
    chunks: int = 0
    skipped: list[str] = field(default_factory=list)
    per_doc: dict[str, int] = field(default_factory=dict)

    def format(self) -> str:
        if self.documents == 0:
            return "No project write-ups found."
        lines = [f"{self.documents} document(s), {self.chunks} chunk(s)"]
        for doc, count in sorted(self.per_doc.items()):
            lines.append(f"  {doc:32} {count:4} chunk(s)")
        if self.skipped:
            lines.append(f"  skipped: {', '.join(sorted(self.skipped))}")
        return "\n".join(lines)


def doc_slug(path: Path) -> str:
    """The identifier stored in ``chunks.profile_doc``: the filename stem."""
    return path.stem.strip().lower()


def read_profile_docs(directory: Path) -> list[tuple[str, str]]:
    """Read every ingestable markdown file as ``(slug, text)``, sorted by slug."""
    if not directory.exists():
        return []

    docs: list[tuple[str, str]] = []
    for path in sorted(directory.glob("*.md")):
        slug = doc_slug(path)
        if slug in SKIP:
            continue
        text = path.read_text(encoding="utf-8").strip()
        if text:
            docs.append((slug, text))
    return docs


def ingest_profile(
    conn: sqlite3.Connection,
    settings: Settings | None = None,
) -> ProfileReport:
    """Chunk every write-up in ``profile/`` into the chunks table.

    Each document is deleted and re-chunked wholesale rather than diffed. There
    are a handful of these files, and the embedding cache is keyed by text, so
    re-chunking an unchanged document costs one database write and no API call.

    Embedding is a separate step: this leaves the new chunks with a NULL
    ``vector_row`` for ``embed_all_pending`` to fill in.
    """
    from ..core.chunking import chunk_profile_doc

    settings = settings or get_settings()
    report = ProfileReport()

    all_paths = sorted(settings.profile_dir.glob("*.md")) if settings.profile_dir.exists() else []
    report.skipped = [doc_slug(p) for p in all_paths if doc_slug(p) in SKIP]

    docs = read_profile_docs(settings.profile_dir)
    if not docs:
        return report

    for slug, text in docs:
        chunks = chunk_profile_doc(text)
        with conn:
            conn.execute("DELETE FROM chunks WHERE profile_doc = ?", (slug,))
            for chunk in chunks:
                conn.execute(
                    "INSERT INTO chunks (profile_doc, ordinal, text) VALUES (?, ?, ?)",
                    (slug, chunk.ordinal, chunk.text),
                )
        report.documents += 1
        report.chunks += len(chunks)
        report.per_doc[slug] = len(chunks)
        log.info("%s: %d chunk(s)", slug, len(chunks))

    return report
