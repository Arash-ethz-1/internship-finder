"""The email review queue.

Two routes and a third for the other half of "accept or reject". Note what is
missing: there is no route that runs a sync. Fetching mail is a slow, network-
bound, credential-holding operation that belongs to ``cli sync-email``, and
putting it behind a dashboard button would mean a page load could spend a
minute talking to Google.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ..inbox import (
    InboxError,
    accept_suggestion,
    dismiss_suggestion,
    list_suggestions,
    pending_count,
)
from ..runtime import get_db
from .schemas import ApplicationState, InboxAccept, InboxPage, InboxSuggestion

router = APIRouter(prefix="/api", tags=["inbox"])

Conn = Annotated[sqlite3.Connection, Depends(get_db)]


@router.get("/inbox", response_model=InboxPage)
def get_inbox(
    conn: Conn,
    pending_only: bool = Query(default=True, description="hide accepted and dismissed"),
    actionable_only: bool = Query(default=False, description="hide 'other' classifications"),
) -> InboxPage:
    """The suggestions waiting for review, most confident first."""
    rows = list_suggestions(conn, pending_only=pending_only, actionable_only=actionable_only)
    return InboxPage(
        items=[InboxSuggestion(**dict(row)) for row in rows],
        pending=pending_count(conn),
    )


@router.post("/inbox/{match_id}/accept", response_model=ApplicationState)
def post_accept(conn: Conn, match_id: int, body: InboxAccept | None = None) -> ApplicationState:
    """Apply one suggestion.

    This is the only route in the app that changes a status from an email, and
    it runs because a person clicked accept. 409 means the suggestion needs
    something from the user first — usually which posting it belongs to.
    """
    body = body or InboxAccept()
    try:
        result = accept_suggestion(conn, match_id, posting_id=body.posting_id, status=body.status)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except InboxError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    return ApplicationState(
        posting_id=str(result["posting_id"]),
        from_status=result["from_status"],  # type: ignore[arg-type]
        status=str(result["status"]),
        note=str(result["note"]),
        updated_at=str(result["updated_at"]),
    )


@router.post("/inbox/{match_id}/dismiss", response_model=InboxSuggestion)
def post_dismiss(conn: Conn, match_id: int) -> InboxSuggestion:
    """Reject one suggestion. Nothing but the flag is written."""
    try:
        dismiss_suggestion(conn, match_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except InboxError as exc:
        raise HTTPException(409, str(exc)) from exc

    row = conn.execute(
        "SELECT e.*, p.company, p.title, p.url, a.status AS current_status "
        "FROM email_matches e "
        "LEFT JOIN postings p ON p.id = e.posting_id "
        "LEFT JOIN applications a ON a.posting_id = e.posting_id "
        "WHERE e.id = ?",
        (match_id,),
    ).fetchone()
    return InboxSuggestion(**dict(row))
