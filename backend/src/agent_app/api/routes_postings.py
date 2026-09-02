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
from ..core.locations import COUNTRIES, REGION_LABELS, REGIONS
from ..db import (
    LEVELS,
    SOURCES,
    STATUSES,
    PostingFilters,
    distinct_values,
    list_postings,
    place_options,
    places_for,
    stats,
)
from ..ingest.manual import ManualPosting, ManualPostingError
from ..ingest.manual import create as create_manual
from ..ingest.manual import delete as delete_manual
from ..ingest.manual import update as update_manual
from ..runtime import get_db
from .schemas import (
    ApplicationState,
    ApplicationUpdate,
    BulkStatusResult,
    BulkStatusUpdate,
    CountryOption,
    FilterOptions,
    ManualPostingBody,
    Place,
    PostingDetail,
    PostingPage,
    PostingSummary,
    RegionOption,
    Stats,
)

router = APIRouter(prefix="/api", tags=["postings"])

Conn = Annotated[sqlite3.Connection, Depends(get_db)]

# Repeatable, so ?status=applied&status=interviewing is both. Annotated rather
# than a Query() default because a list default in a signature is the shape
# ruff's B008 exists to catch.
StatusQuery = Annotated[
    list[str] | None,
    Query(description="repeatable; a status, or 'untriaged' / 'tracked'. Several are OR-ed."),
]


@router.get("/postings", response_model=PostingPage)
def get_postings(
    conn: Conn,
    q: str | None = None,
    company: str | None = None,
    level: str | None = Query(default=None, description=f"one of {LEVELS}"),
    location: str | None = None,
    remote: bool | None = None,
    source: str | None = Query(default=None, description=f"one of {SOURCES}"),
    status: StatusQuery = None,
    posted_after: str | None = Query(default=None, description="UTC ISO-8601"),
    country: str | None = Query(default=None, description="ISO 3166-1 alpha-2, e.g. CH"),
    region: str | None = Query(default=None, description=f"one of {REGIONS}"),
    include_closed: bool = Query(
        default=False, description="also show postings the board has taken down"
    ),
    only_closed: bool = Query(default=False, description="show only those"),
    sort: str = "posted_at",
    descending: bool = True,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> PostingPage:
    """List postings, filtered and paginated.

    The grid is virtualised client-side, so the default limit is generous: one
    request for the whole working set beats paging at this corpus size.
    """
    known = (*STATUSES, "untriaged", "tracked")
    for value in status or ():
        if value not in known:
            raise HTTPException(422, f"Unknown status {value!r}")
    if region is not None and region not in REGIONS:
        raise HTTPException(422, f"Unknown region {region!r}")
    if country is not None and country.upper() not in COUNTRIES:
        raise HTTPException(422, f"Unknown country {country!r}")

    filters = PostingFilters(
        q=q,
        company=company,
        level=level,
        location=location,
        remote=remote,
        source=source,
        statuses=tuple(status or ()),
        posted_after=posted_after,
        country=country,
        region=region,
        include_closed=include_closed,
        only_closed=only_closed,
    )
    rows, total = list_postings(
        conn, filters, limit=limit, offset=offset, sort=sort, descending=descending
    )
    # One query for every row's places rather than one per row: the grid asks
    # for five hundred at a time.
    places = places_for(conn, [row["id"] for row in rows])
    return PostingPage(
        items=[
            PostingSummary(
                **dict(row),
                places=[Place(**place) for place in places.get(row["id"], ())],
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/postings", response_model=PostingDetail, status_code=201)
def post_posting(conn: Conn, draft: ManualPostingBody) -> PostingDetail:
    """Add a posting you found somewhere with no public feed.

    LinkedIn, a company's own careers page, a forwarded email. Without this the
    application cannot be tracked at all, so `/stats` understates the pipeline
    and a reply from that company has no posting to be matched against.

    It becomes an ordinary posting: chunked and embedded on the next
    `cli embed`, and searchable alongside the boards.
    """
    try:
        posting = create_manual(conn, ManualPosting(**draft.model_dump()))
    except ManualPostingError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _detail(posting.id)


@router.put("/postings/{posting_id:path}", response_model=PostingDetail)
def put_posting(conn: Conn, posting_id: str, draft: ManualPostingBody) -> PostingDetail:
    """Edit a manual posting.

    Only manual ones. A board posting's text is owned upstream, so an edit here
    would be silently undone by the next ingest.
    """
    try:
        update_manual(conn, posting_id, ManualPosting(**draft.model_dump()))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ManualPostingError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _detail(posting_id)


@router.delete("/postings/{posting_id:path}", status_code=204)
def remove_posting(conn: Conn, posting_id: str) -> None:
    """Delete a manual posting and everything hanging off it.

    Only manual ones, and only on an explicit request. A board posting that is
    gone is *closed*, not deleted -- its application and history are the reason
    the row stays.
    """
    try:
        delete_manual(conn, posting_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ManualPostingError as exc:
        raise HTTPException(422, str(exc)) from exc


def _detail(posting_id: str) -> PostingDetail:
    """Shared tail of every route that returns one posting in full."""
    data = tools.get_posting(posting_id)
    data["id"] = data.pop("posting_id")
    return PostingDetail(**data)


@router.get("/postings/{posting_id:path}", response_model=PostingDetail)
def get_posting(posting_id: str) -> PostingDetail:
    """One posting in full, with its application state and history.

    `tools.get_posting` speaks the agent's vocabulary and the API speaks the
    frontend's; `_detail` is where the two are translated.
    """
    try:
        return _detail(posting_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


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


@router.delete("/applications/{posting_id:path}", response_model=ApplicationState)
def delete_application(posting_id: str) -> ApplicationState:
    """Put a posting back in the pool, with no status at all.

    Not the same as any status: untriaged is the absence of an application row,
    and it is what makes a posting eligible to be surfaced by a search again.
    Deleting one that does not exist is a success, because the caller asked for
    a state the posting is already in.
    """
    try:
        result = tools.reset_status(posting_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return ApplicationState(**result)


@router.patch("/applications", response_model=BulkStatusResult)
def patch_applications(update: BulkStatusUpdate) -> BulkStatusResult:
    """Set one status on many postings.

    A posting that cannot be updated is reported by id rather than failing the
    whole request: selecting thirty and having one stale id undo the other
    twenty-nine would be worse than partial success the caller can see.
    """
    updated: list[ApplicationState] = []
    failed: dict[str, str] = {}
    for posting_id in update.posting_ids:
        try:
            updated.append(
                ApplicationState(**tools.update_status(posting_id, update.status, update.note))
            )
        except (KeyError, ValueError) as exc:
            failed[posting_id] = str(exc)
    return BulkStatusResult(updated=updated, failed=failed)


@router.get("/stats", response_model=Stats)
def get_stats(conn: Conn, recent_days: int = Query(default=30, ge=1, le=365)) -> Stats:
    """Counts by status, company, source, level and recency."""
    return Stats(**stats(conn, recent_days=recent_days))


@router.get("/filters", response_model=FilterOptions)
def get_filters(conn: Conn) -> FilterOptions:
    """The values the left rail can offer, derived from the data present."""
    regions, countries = place_options(conn)
    return FilterOptions(
        companies=distinct_values(conn, "company"),
        regions=[
            RegionOption(id=code, name=REGION_LABELS[code], count=count)
            for code, count in regions
            if code in REGION_LABELS
        ],
        countries=[
            CountryOption(
                code=code, name=COUNTRIES[code][0], region=COUNTRIES[code][1], count=count
            )
            for code, count in countries
            if code in COUNTRIES
        ],
    )
