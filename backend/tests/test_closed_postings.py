"""Noticing that a board has taken a posting down.

The signal is free: every board here serves a company's whole list in one
response, so anything held for that company and absent from the response has
been pulled. What matters is the two things it must not do -- close on a failed
or empty fetch, and delete anything.
"""

from __future__ import annotations

import sqlite3

from agent_app.db import Posting, now_iso
from agent_app.ingest.locations import index_pending_locations
from agent_app.ingest.runner import CompanyEntry, reconcile_closed, upsert_postings

ENTRY = CompanyEntry(source="greenhouse", token="acme", name="Acme")


def _posting(external_id: str, title: str = "Intern") -> Posting:
    return Posting(
        id=f"greenhouse:{external_id}",
        source="greenhouse",
        company="Acme",
        title=title,
        location="Berlin, Germany",
        remote=False,
        url=f"https://example.com/{external_id}",
        body="We are hiring an intern.",
        body_hash=f"hash-{external_id}",
    )


def _closed_at(conn: sqlite3.Connection, posting_id: str) -> str | None:
    row = conn.execute("SELECT closed_at FROM postings WHERE id = ?", (posting_id,)).fetchone()
    return row["closed_at"]


def test_a_posting_missing_from_the_board_is_closed(conn: sqlite3.Connection) -> None:
    upsert_postings(conn, [_posting("1"), _posting("2")])
    conn.commit()

    closed, reopened = reconcile_closed(conn, ENTRY, [_posting("1")])
    conn.commit()

    assert (closed, reopened) == (1, 0)
    assert _closed_at(conn, "greenhouse:1") is None
    assert _closed_at(conn, "greenhouse:2") is not None


def test_closing_never_deletes(conn: sqlite3.Connection) -> None:
    """An application, its letter and its history all point at the posting.

    A posting you applied to is the one row you least want to lose, so "gone
    from the board" must not mean "gone from the database".
    """
    upsert_postings(conn, [_posting("1")])
    conn.execute(
        "INSERT INTO applications (posting_id, status, updated_at) VALUES (?, 'applied', ?)",
        ("greenhouse:1", now_iso()),
    )
    conn.commit()

    reconcile_closed(conn, ENTRY, [])
    conn.commit()

    assert conn.execute("SELECT count(*) FROM postings").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM applications").fetchone()[0] == 1


def test_an_empty_response_closes_nothing(conn: sqlite3.Connection) -> None:
    """A board answering 200 with nothing is far more often broken than it is a
    company that fired everyone."""
    upsert_postings(conn, [_posting("1"), _posting("2")])
    conn.commit()

    assert reconcile_closed(conn, ENTRY, []) == (0, 0)
    assert _closed_at(conn, "greenhouse:1") is None


def test_a_relisted_posting_reopens(conn: sqlite3.Connection) -> None:
    """Boards do put a posting back, and a stale `closed_at` would hide
    something still worth applying to."""
    upsert_postings(conn, [_posting("1"), _posting("2")])
    conn.commit()

    reconcile_closed(conn, ENTRY, [_posting("1")])
    conn.commit()
    assert _closed_at(conn, "greenhouse:2") is not None

    closed, reopened = reconcile_closed(conn, ENTRY, [_posting("1"), _posting("2")])
    conn.commit()
    assert (closed, reopened) == (0, 1)
    assert _closed_at(conn, "greenhouse:2") is None


def test_another_companys_postings_are_untouched(conn: sqlite3.Connection) -> None:
    """Reconciliation is scoped to the board that answered. Anything else would
    let one company's response close another company's jobs."""
    other = Posting(
        id="greenhouse:9",
        source="greenhouse",
        company="Globex",
        title="Intern",
        location=None,
        remote=False,
        url="https://example.com/9",
        body="body",
        body_hash="h9",
    )
    upsert_postings(conn, [_posting("1"), other])
    conn.commit()

    reconcile_closed(conn, ENTRY, [_posting("1")])
    conn.commit()
    assert _closed_at(conn, "greenhouse:9") is None


def test_closed_postings_are_out_of_the_grid_by_default(conn: sqlite3.Connection) -> None:
    from agent_app.db import PostingFilters, list_postings

    upsert_postings(conn, [_posting("1"), _posting("2")])
    conn.commit()
    reconcile_closed(conn, ENTRY, [_posting("1")])
    index_pending_locations(conn)
    conn.commit()

    _rows, open_total = list_postings(conn, PostingFilters())
    assert open_total == 1

    _rows, with_closed = list_postings(conn, PostingFilters(include_closed=True))
    assert with_closed == 2

    _rows, only_closed = list_postings(conn, PostingFilters(only_closed=True))
    assert only_closed == 1
