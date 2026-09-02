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

from ..config import get_settings
from ..inbox import (
    InboxError,
    accept_suggestion,
    dismiss_suggestion,
    list_suggestions,
    pending_count,
)
from ..inbox.job import JOB
from ..runtime import get_db
from .schemas import (
    ApplicationState,
    InboxAccept,
    InboxPage,
    InboxSuggestion,
    SyncRequest,
    SyncStatus,
)

router = APIRouter(prefix="/api", tags=["inbox"])

Conn = Annotated[sqlite3.Connection, Depends(get_db)]


@router.get("/inbox", response_model=InboxPage)
def get_inbox(
    conn: Conn,
    pending_only: bool = Query(default=True, description="hide accepted and dismissed"),
    actionable_only: bool = Query(default=False, description="hide 'other' classifications"),
    classification: str | None = Query(
        default=None, description="rejection | interview | offer | other"
    ),
    min_confidence: float = Query(
        default=0.0, ge=0.0, le=1.0, description="drop suggestions the model was unsure about"
    ),
) -> InboxPage:
    """The suggestions waiting for review, most confident first.

    ``pending`` is the unfiltered count, so the dashboard can say how many rows
    the filters are holding back rather than pretending they do not exist.
    """
    rows = list_suggestions(
        conn,
        pending_only=pending_only,
        actionable_only=actionable_only,
        classification=classification,
        min_confidence=min_confidence,
    )
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


@router.get("/inbox/sync", response_model=SyncStatus)
def get_sync_status() -> SyncStatus:
    """Whether a mailbox sync is running, and what the last one did.

    Polled by the dashboard while a sync is going. Deliberately cheap and
    never blocking: reading this must not wait behind a run that is currently
    talking to Gmail.
    """
    return SyncStatus(**JOB.state.to_dict(), authorised=_is_authorised())


@router.post("/inbox/sync", response_model=SyncStatus, status_code=202)
def post_sync(request: SyncRequest | None = None) -> SyncStatus:
    """Start a mailbox sync in the background.

    202, not 200: the work has been accepted and is not done. The response is
    the job state, and the dashboard polls `GET /api/inbox/sync` for the rest.
    Nothing here holds the request open, which was the whole objection to
    having this route at all.

    A sync still only ever writes suggestions. `applications` is untouched,
    and accepting one remains a separate, deliberate act.
    """
    body = request or SyncRequest()
    if not _is_authorised():
        raise HTTPException(
            409,
            "Gmail is not authorised yet. Run: uv run python -m agent_app.cli "
            "sync-email --login",
        )
    try:
        state = JOB.start(include_sent=body.include_sent, limit=body.limit)
    except InboxError as exc:
        raise HTTPException(409, str(exc)) from exc
    return SyncStatus(**state.to_dict(), authorised=True)


def _is_authorised() -> bool:
    """Is there a stored refresh token to sync with.

    Checked before starting rather than discovered inside the job, so "you have
    not connected Gmail" is an immediate, actionable answer instead of a failed
    run the person has to go and read.
    """
    return get_settings().gmail_token_path.exists()
