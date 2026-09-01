"""Turning stored postings into retrievable chunks.

The counterpart to :mod:`agent_app.ingest.profile`, for the other corpus.
Ingestion writes a posting's body; this splits that body into the rows that
:func:`agent_app.core.embeddings.embed_all_pending` then gives vectors to.
Nothing here talks to the network, so it is free to run and safe to re-run.

The seam this closes: ``upsert_postings`` already *deletes* a posting's chunks
when its body changes, and ``embed_all_pending`` explicitly never chunks. Both
were correct on their own, and between them nothing ever built a posting chunk.

Idempotence comes from the query rather than from bookkeeping: a posting is
pending when it has no chunk rows at all. That makes the function self-healing
in both directions — postings ingested before this module existed get chunked
on the next run, and a posting whose body changed gets rebuilt because the
upsert dropped its rows.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from ..db import Posting

log = logging.getLogger(__name__)

# How many postings to hold in memory at once. Bodies average ~6 kB, so a
# larger window buys nothing and a smaller one costs round trips.
DEFAULT_BATCH = 500


@dataclass
class PostingChunkReport:
    """What one chunking pass did."""

    pending: int = 0
    postings: int = 0
    chunks: int = 0
    empty: int = 0  # postings whose body produced no chunks at all

    def format(self) -> str:
        if self.pending == 0:
            return "0 postings to chunk."
        average = self.chunks / self.postings if self.postings else 0.0
        line = (
            f"{self.postings:,} posting(s) chunked into {self.chunks:,} chunk(s) "
            f"({average:.1f} per posting)"
        )
        if self.empty:
            line += f"\n  {self.empty:,} posting(s) had an empty body and produced nothing"
        return line


def pending_posting_ids(conn: sqlite3.Connection) -> list[str]:
    """Every posting with no chunk rows, oldest first.

    The LEFT JOIN is the whole definition of "pending": no bookkeeping column,
    no timestamp comparison, nothing that can drift out of step with reality.
    """
    return [
        row["id"]
        for row in conn.execute(
            "SELECT p.id FROM postings p "
            "LEFT JOIN chunks c ON c.posting_id = p.id "
            "WHERE c.id IS NULL ORDER BY p.id"
        )
    ]


def chunk_pending_postings(
    conn: sqlite3.Connection,
    *,
    max_chars: int | None = None,
    batch: int = DEFAULT_BATCH,
) -> PostingChunkReport:
    """Chunk every posting that does not have chunks yet.

    Each batch is written in one transaction, so an interrupted run leaves
    whole postings chunked rather than a posting half-chunked — and the next
    run picks up exactly where this one stopped.

    ``max_chars`` overrides the chunker's default, which is what makes tuning
    possible: drop the chunks, re-run with a different size, re-run ``cli eval``
    and compare the number.
    """
    from ..core.chunking import DEFAULT_MAX_CHARS, chunk_posting

    limit = DEFAULT_MAX_CHARS if max_chars is None else max_chars

    ids = pending_posting_ids(conn)
    report = PostingChunkReport(pending=len(ids))
    if not ids:
        return report

    for start in range(0, len(ids), batch):
        window = ids[start : start + batch]
        marks = ",".join("?" * len(window))
        rows = conn.execute(f"SELECT * FROM postings WHERE id IN ({marks})", window).fetchall()

        with conn:
            for row in rows:
                posting = Posting.from_row(row)
                chunks = chunk_posting(posting, limit)
                if not chunks:
                    # A posting whose board published no description at all.
                    # Counted, not chunked: an empty chunk would be a row that
                    # costs an embedding and can never match anything.
                    report.empty += 1
                    continue
                conn.executemany(
                    "INSERT INTO chunks (posting_id, ordinal, text) VALUES (?, ?, ?)",
                    [(posting.id, c.ordinal, c.text) for c in chunks],
                )
                report.postings += 1
                report.chunks += len(chunks)

        log.info("chunked %d/%d posting(s)", report.postings + report.empty, len(ids))

    return report
