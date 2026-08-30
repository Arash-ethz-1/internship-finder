"""Postings, applications and stats.

No business logic here: every route resolves parameters, calls into ``db.py``
or ``core/``, and serialises. If a route grows a rule, the rule belongs
downstairs.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core import tools
from ..db import (
    LEVELS,
    SOURCES,
    STATUSES,
    PostingFilters,
    distinct_values,
    list_postings,
    stats,
)
from ..runtime import get_db
from .schemas import (
    ApplicationState,
    ApplicationUpdate,
    FilterOptions,
    PostingDetail,
    PostingPage,
    PostingSummary,
    Stats,
)

router = APIRouter(prefix="/api", tags=["postings"])

Conn = Annotated[sqlite3.Connection, Depends(get_db)]


@router.get("/postings", response_model=PostingPage)
def get_postings(
    conn: Conn,
    q: str | None = None,
    company: str | None = None,
    level: str | None = Query(default=None, description=f"one of {LEVELS}"),
    location: str | None = None,
    remote: bool | None = None,
    source: str | None = Query(default=None, description=f"one of {SOURCES}"),
    status: str | None = Query(default=None, description="a status, or 'untriaged'"),
    posted_after: str | None = Query(default=None, description="UTC ISO-8601"),
    sort: str = "posted_at",
    descending: bool = True,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> PostingPage:
    """List postings, filtered and paginated.

    The grid is virtualised client-side, so the default limit is generous: one
    request for the whole working set beats paging at this corpus size.
    """
    if status is not None and status != "untriaged" and status not in STATUSES:
        raise HTTPException(422, f"Unknown status {status!r}")

    filters = PostingFilters(
        q=q,
        company=company,
        level=level,
        location=location,
        remote=remote,
        source=source,
        status=status,
        posted_after=posted_after,
    )
    rows, total = list_postings(
        conn, filters, limit=limit, offset=offset, sort=sort, descending=descending
    )
    return PostingPage(
        items=[PostingSummary(**dict(row)) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/postings/{posting_id:path}", response_model=PostingDetail)
def get_posting(posting_id: str) -> PostingDetail:
    """One posting in full, with its application state and history."""
    try:
        data = tools.get_posting(posting_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    # tools.get_posting speaks the agent's vocabulary; the API speaks the
    # frontend's. Translating here keeps both stable.
    data["id"] = data.pop("posting_id")
    return PostingDetail(**data)


@router.patch("/applications/{posting_id:path}", response_model=ApplicationState)
def patch_application(posting_id: str, update: ApplicationUpdate) -> ApplicationState:
    """Set a posting's status and record the change."""
    try:
        result = tools.update_status(posting_id, update.status, update.note)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return ApplicationState(**result)


@router.get("/stats", response_model=Stats)
def get_stats(conn: Conn, recent_days: int = Query(default=30, ge=1, le=365)) -> Stats:
    """Counts by status, company, source, level and recency."""
    return Stats(**stats(conn, recent_days=recent_days))


@router.get("/filters", response_model=FilterOptions)
def get_filters(conn: Conn) -> FilterOptions:
    """The values the left rail can offer, derived from the data present."""
    return FilterOptions(companies=distinct_values(conn, "company"))
