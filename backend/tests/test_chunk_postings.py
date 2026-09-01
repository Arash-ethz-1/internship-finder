"""Chunking stored postings into the chunks table.

The seam between ingestion (which writes bodies) and embedding (which never
chunks). These tests exist because nothing built a posting chunk for three
phases and no test noticed.
"""

from __future__ import annotations

import sqlite3

from agent_app.db import now_iso
from agent_app.ingest.chunks import chunk_pending_postings, pending_posting_ids


def add_posting(conn: sqlite3.Connection, posting_id: str, body: str = "") -> None:
    now = now_iso()
    conn.execute(
        "INSERT INTO postings (id, source, company, title, location, remote, url, body, "
        "body_hash, level, first_seen, last_seen) "
        "VALUES (?, 'greenhouse', 'Acme', 'ML Intern', 'Zurich', 0, 'https://x', ?, "
        "'hash', 'intern', ?, ?)",
        (posting_id, body or "A paragraph about the role.\n\nAnd a second one.", now, now),
    )
    conn.commit()


def test_a_posting_with_no_chunks_is_pending(conn: sqlite3.Connection) -> None:
    add_posting(conn, "greenhouse:1")
    assert pending_posting_ids(conn) == ["greenhouse:1"]


def test_chunking_writes_rows_and_clears_the_backlog(conn: sqlite3.Connection) -> None:
    add_posting(conn, "greenhouse:1")
    report = chunk_pending_postings(conn)

    assert report.pending == 1
    assert report.postings == 1
    assert report.chunks >= 1
    assert pending_posting_ids(conn) == []

    rows = conn.execute(
        "SELECT posting_id, ordinal, text, vector_row FROM chunks ORDER BY ordinal"
    ).fetchall()
    assert [r["ordinal"] for r in rows] == list(range(len(rows)))
    assert all(r["posting_id"] == "greenhouse:1" for r in rows)
    # Embedding is a separate step; these rows are what it looks for.
    assert all(r["vector_row"] is None for r in rows)
    assert all("ML Intern (Acme)" in r["text"] for r in rows)


def test_running_twice_changes_nothing(conn: sqlite3.Connection) -> None:
    add_posting(conn, "greenhouse:1")
    first = chunk_pending_postings(conn)
    before = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]

    second = chunk_pending_postings(conn)

    assert second.pending == 0
    assert second.chunks == 0
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == before == first.chunks


def test_deleted_chunks_are_rebuilt(conn: sqlite3.Connection) -> None:
    """What ``upsert_postings`` does when a body changes must be recoverable."""
    add_posting(conn, "greenhouse:1")
    chunk_pending_postings(conn)

    conn.execute("DELETE FROM chunks WHERE posting_id = 'greenhouse:1'")
    conn.commit()

    assert pending_posting_ids(conn) == ["greenhouse:1"]
    assert chunk_pending_postings(conn).chunks >= 1


def test_an_empty_body_produces_nothing_rather_than_a_blank_chunk(
    conn: sqlite3.Connection,
) -> None:
    add_posting(conn, "greenhouse:1", body="   ")
    report = chunk_pending_postings(conn)

    assert report.empty == 1
    assert report.postings == 0
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0


def test_profile_chunks_are_left_alone(conn: sqlite3.Connection) -> None:
    """A profile chunk must never make a posting look already chunked."""
    conn.execute("INSERT INTO chunks (profile_doc, ordinal, text) VALUES ('pyblio', 0, 'x')")
    add_posting(conn, "greenhouse:1")
    conn.commit()

    assert pending_posting_ids(conn) == ["greenhouse:1"]
    chunk_pending_postings(conn)
    assert (
        conn.execute("SELECT count(*) FROM chunks WHERE profile_doc IS NOT NULL").fetchone()[0] == 1
    )


def test_max_chars_is_honoured(conn: sqlite3.Connection) -> None:
    add_posting(conn, "greenhouse:1", body="word " * 400)
    chunk_pending_postings(conn, max_chars=300)

    texts = [r["text"] for r in conn.execute("SELECT text FROM chunks")]
    assert len(texts) > 1
    assert all(len(t) <= 300 for t in texts)
