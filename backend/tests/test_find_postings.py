"""The agent's result list: `find_postings` and the `found` status.

Two properties matter here and nothing else does. A search must never offer a
posting the person has already dealt with, and a posting a search merely
surfaced must never be mistaken for one they applied to.
"""

from __future__ import annotations

import sqlite3

from agent_app.db import now_iso
from agent_app.ingest.chunks import chunk_pending_postings


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


#
# The list the dashboard renders. Lives here rather than in
# test_core_scaffolding.py because it is about what gets written, not about
# the tool surface.


def test_find_postings_records_what_it_returned(conn: sqlite3.Connection, monkeypatch) -> None:
    from agent_app.core import retrieval, tools

    add_posting(conn, "greenhouse:1")
    add_posting(conn, "greenhouse:2")
    chunk_pending_postings(conn)

    def fake_search(query, filters, k=10):
        assert filters.status == "untriaged", "must never offer a triaged posting"
        rows = conn.execute("SELECT id, posting_id, text FROM chunks ORDER BY id").fetchall()
        return [
            retrieval.SearchHit(
                chunk_id=r["id"],
                posting_id=r["posting_id"],
                profile_doc=None,
                ordinal=0,
                text=r["text"],
                score=0.03,
                rank=i + 1,
                component_scores={"dense": 0.02, "bm25": 0.01},
            )
            for i, r in enumerate(rows)
        ]

    monkeypatch.setattr(retrieval, "search", fake_search)
    out = tools.find_postings("machine learning", limit=30)

    # One entry per posting, not per chunk.
    assert [p["posting_id"] for p in out] == ["greenhouse:1", "greenhouse:2"]
    assert out[0]["rank"] == 1 and "component_scores" in out[0]

    statuses = dict(conn.execute("SELECT posting_id, status FROM applications"))
    assert statuses == {"greenhouse:1": "found", "greenhouse:2": "found"}
    notes = [r[0] for r in conn.execute("SELECT note FROM status_history")]
    assert all("found by search: machine learning" in n for n in notes)


def test_found_postings_stay_out_of_the_shortlist(conn: sqlite3.Connection) -> None:
    from agent_app.core import tools

    add_posting(conn, "greenhouse:1")
    add_posting(conn, "greenhouse:2")
    tools.update_status("greenhouse:1", "found")
    tools.update_status("greenhouse:2", "interested")

    assert [p["posting_id"] for p in tools.list_shortlist()] == ["greenhouse:2"]
    # ...unless asked for by name.
    assert [p["posting_id"] for p in tools.list_shortlist("found")] == ["greenhouse:1"]


def test_found_postings_are_never_matched_against_email(conn: sqlite3.Connection) -> None:
    """The exclusion that stops a rejection suggestion for a job never applied to."""
    from agent_app.core import tools
    from agent_app.inbox.match import open_applications

    add_posting(conn, "greenhouse:1")
    add_posting(conn, "greenhouse:2")
    tools.update_status("greenhouse:1", "found")
    tools.update_status("greenhouse:2", "applied")

    assert [a.posting_id for a in open_applications(conn)] == ["greenhouse:2"]
