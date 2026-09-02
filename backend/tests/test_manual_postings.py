"""Postings entered by hand.

The point of these is what a manual posting must *not* become: something a
board can close, something ingest can overwrite, or something that behaves
differently from a board posting once it exists.
"""

from __future__ import annotations

import sqlite3

import pytest

from agent_app.db import MANUAL_SOURCE, Posting, get_posting
from agent_app.ingest.locations import index_pending_locations
from agent_app.ingest.manual import (
    ManualPosting,
    ManualPostingError,
    create,
    delete,
    make_manual_id,
    update,
)
from agent_app.ingest.runner import CompanyEntry, reconcile_closed, upsert_postings

DRAFT = ManualPosting(
    company="Test Robotics AG",
    title="Praktikum Machine Learning",
    url="https://www.linkedin.com/jobs/view/123",
    location="Zürich, Switzerland",
    body="We are looking for an intern to work on robot perception.",
)


def test_a_manual_posting_becomes_an_ordinary_posting(conn: sqlite3.Connection) -> None:
    posting = create(conn, DRAFT)

    assert posting.source == MANUAL_SOURCE
    assert posting.id.startswith("manual:test-robotics-ag-")
    # The same level heuristic the boards get. "Praktikum" is exactly the kind
    # of posting this search is for, so it had better resolve.
    assert posting.level == "intern"
    assert get_posting(conn, posting.id) is not None


def test_it_is_filterable_by_place_immediately(conn: sqlite3.Connection) -> None:
    """Locations are indexed inside `create`, not on the next batch pass.

    A posting you just typed in and cannot then find by country would look
    broken, and correctly so.
    """
    posting = create(conn, DRAFT)
    countries = [
        row[0]
        for row in conn.execute(
            "SELECT country FROM posting_locations WHERE posting_id = ?", (posting.id,)
        )
    ]
    assert countries == ["CH"]


def test_ingest_can_never_close_it(conn: sqlite3.Connection) -> None:
    """The property that makes `source = manual` load-bearing.

    Closing is scoped to a board's own response. A manual posting belongs to no
    board, so no board's silence is evidence about it.
    """
    posting = create(conn, DRAFT)
    board = Posting(
        id="greenhouse:1",
        source="greenhouse",
        company="Test Robotics AG",
        title="Intern",
        location=None,
        remote=False,
        url="https://example.com/1",
        body="body",
        body_hash="h1",
    )
    upsert_postings(conn, [board])
    conn.commit()

    # The board answers with nothing but its own posting; the manual one is not
    # in that response and must survive anyway.
    reconcile_closed(
        conn, CompanyEntry(source="greenhouse", token="t", name="Test Robotics AG"), [board]
    )
    conn.commit()

    assert get_posting(conn, posting.id).closed_at is None  # type: ignore[union-attr]


def test_editing_replaces_the_chunks_when_the_body_changes(conn: sqlite3.Connection) -> None:
    """Old chunks describe text that no longer exists.

    Leaving them means searching a posting that is not there any more, which is
    the same failure `upsert_postings` drops chunks to avoid for boards.
    """
    posting = create(conn, DRAFT)
    conn.execute(
        "INSERT INTO chunks (posting_id, ordinal, text) VALUES (?, 0, 'old text')",
        (posting.id,),
    )
    conn.commit()

    update(conn, posting.id, ManualPosting(**{**DRAFT.__dict__, "body": "Completely new text."}))
    assert (
        conn.execute("SELECT count(*) FROM chunks WHERE posting_id = ?", (posting.id,)).fetchone()[
            0
        ]
        == 0
    )


def test_editing_keeps_the_chunks_when_only_the_title_changes(conn: sqlite3.Connection) -> None:
    """Re-embedding is the expensive step, so it is not paid for a typo fix."""
    posting = create(conn, DRAFT)
    conn.execute(
        "INSERT INTO chunks (posting_id, ordinal, text) VALUES (?, 0, 'kept')", (posting.id,)
    )
    conn.commit()

    update(conn, posting.id, ManualPosting(**{**DRAFT.__dict__, "title": "Praktikum ML (m/w/d)"}))
    assert (
        conn.execute("SELECT count(*) FROM chunks WHERE posting_id = ?", (posting.id,)).fetchone()[
            0
        ]
        == 1
    )


def test_a_board_posting_cannot_be_edited_or_deleted(conn: sqlite3.Connection) -> None:
    """Its text is owned upstream, so an edit here is undone by the next ingest
    and a delete is undone by it reappearing."""
    board = Posting(
        id="greenhouse:1",
        source="greenhouse",
        company="Acme",
        title="Intern",
        location=None,
        remote=False,
        url="https://example.com/1",
        body="body",
        body_hash="h",
    )
    upsert_postings(conn, [board])
    conn.commit()

    with pytest.raises(ManualPostingError, match="greenhouse"):
        update(conn, "greenhouse:1", DRAFT)
    with pytest.raises(ManualPostingError, match="next ingest"):
        delete(conn, "greenhouse:1")


def test_deleting_takes_the_application_with_it(conn: sqlite3.Connection) -> None:
    """Unlike closing, this really is a delete.

    A closed board posting keeps its history because the history is the record
    of an application you actually sent. Deleting a manual posting is you
    saying it should never have been there.
    """
    posting = create(conn, DRAFT)
    conn.execute(
        "INSERT INTO applications (posting_id, status, updated_at) VALUES (?, 'applied', 'now')",
        (posting.id,),
    )
    conn.commit()

    delete(conn, posting.id)
    assert conn.execute("SELECT count(*) FROM applications").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM posting_locations").fetchone()[0] == 0


def test_a_posting_with_no_url_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(ManualPostingError, match="url"):
        create(conn, ManualPosting(company="Acme", title="Intern", url="  "))


def test_ids_are_unique_per_company(conn: sqlite3.Connection) -> None:
    """You will add two Google roles, so the company name alone cannot be the
    id -- and a bare uuid would make every row in the grid unreadable."""
    first, second = make_manual_id("Google"), make_manual_id("Google")
    assert first != second
    assert first.startswith("manual:google-")


def test_it_appears_in_the_grid_beside_board_postings(conn: sqlite3.Connection) -> None:
    from agent_app.db import PostingFilters, list_postings

    create(conn, DRAFT)
    index_pending_locations(conn)
    conn.commit()

    rows, total = list_postings(conn, PostingFilters(country="CH"))
    assert total == 1
    assert rows[0]["source"] == MANUAL_SOURCE
