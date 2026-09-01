"""Pydantic models: the contract between the API and the frontend.

Every model here has a mirror in ``frontend/src/api/client.ts``. When one
changes, both change.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..db import LEVELS, SOURCES, STATUSES

StatusLiteral = Literal[
    "interested", "ready_to_submit", "applied", "rejected", "interviewing", "offer", "declined"
]


class PostingSummary(BaseModel):
    """One row of the grid. No body: 5,000 of these cross the wire at once."""

    id: str
    source: str
    company: str
    title: str
    location: str | None
    remote: bool
    url: str
    posted_at: str | None
    deadline: str | None
    level: str
    first_seen: str
    last_seen: str
    status: str = "untriaged"


class StatusChange(BaseModel):
    """One row of status_history."""

    from_status: str | None
    to_status: str
    note: str
    changed_at: str


class PostingDetail(PostingSummary):
    """What the right-hand panel shows: the full posting plus its history."""

    body: str
    note: str = ""
    letter_path: str | None = None
    history: list[StatusChange] = Field(default_factory=list)


class PostingPage(BaseModel):
    """A page of the grid. ``total`` is the count before limit/offset."""

    items: list[PostingSummary]
    total: int
    limit: int
    offset: int


class FilterOptions(BaseModel):
    """What the left rail offers, derived from the data actually present."""

    companies: list[str]
    levels: list[str] = Field(default_factory=lambda: list(LEVELS))
    sources: list[str] = Field(default_factory=lambda: list(SOURCES))
    statuses: list[str] = Field(default_factory=lambda: ["untriaged", *STATUSES])


class ApplicationUpdate(BaseModel):
    """PATCH body for setting a status."""

    status: StatusLiteral
    note: str = ""


class ApplicationState(BaseModel):
    """What a status change produced."""

    posting_id: str
    from_status: str | None
    status: str
    note: str
    updated_at: str


class CompanyCount(BaseModel):
    company: str
    count: int
    intern: int


class DayCount(BaseModel):
    date: str
    count: int


class Stats(BaseModel):
    """The pipeline view."""

    total: int
    by_status: dict[str, int]
    by_company: list[CompanyCount]
    by_source: dict[str, int]
    by_level: dict[str, int]
    recent: list[DayCount]


class SearchHitModel(BaseModel):
    """A retrieval hit.

    ``component_scores`` must survive the wire intact and its values must sum
    to ``score`` — the frontend draws one stacked bar segment per key, and the
    bar is only honest if the parts add up.
    """

    chunk_id: int
    posting_id: str | None
    profile_doc: str | None
    ordinal: int
    text: str
    score: float
    rank: int
    component_scores: dict[str, float]


class LetterResponse(BaseModel):
    """A drafted letter beside the profile chunks it was grounded in."""

    posting_id: str
    text: str
    path: str
    grounding: list[SearchHitModel]
    todos: list[str]


class InboxSuggestion(BaseModel):
    """One row of the review queue.

    ``posting_id`` is null when the matcher declined to guess. ``company``,
    ``title`` and ``current_status`` come from the join and are null with it.
    """

    id: int
    message_id: str
    posting_id: str | None
    company_guess: str | None
    sender: str
    received_at: str | None
    subject: str
    snippet: str
    classification: str | None
    confidence: float | None
    suggested_status: str | None
    applied: bool
    dismissed: bool
    created_at: str
    company: str | None = None
    title: str | None = None
    url: str | None = None
    current_status: str | None = None


class InboxPage(BaseModel):
    """The review queue plus the counts the dashboard shows beside it."""

    items: list[InboxSuggestion]
    pending: int


class InboxAccept(BaseModel):
    """POST body for accepting a suggestion. Both fields are overrides.

    ``posting_id`` attaches an unmatched email to a posting the user picked;
    ``status`` overrides what the classifier suggested.
    """

    posting_id: str | None = None
    status: StatusLiteral | None = None


class ChatRequest(BaseModel):
    """One agent turn. History is client-held; there is no conversations table."""

    message: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    max_iters: int = 12


class Health(BaseModel):
    status: str
    version: str
