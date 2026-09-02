"""Pydantic models: the contract between the API and the frontend.

Every model here has a mirror in ``frontend/src/api/client.ts``. When one
changes, both change.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..db import LEVELS, SOURCES, STATUSES

StatusLiteral = Literal[
    "found",
    "not_relevant",
    "interested",
    "applied",
    "rejected",
    "interviewing",
    "offer",
    "declined",
]

LevelLiteral = Literal["intern", "newgrad", "unknown"]


class Place(BaseModel):
    """One resolved location on a posting.

    `raw` is always the board's own words, so a place the parser could not
    resolve is still shown rather than silently missing.
    """

    raw: str
    city: str | None = None
    country: str | None = None
    region: str | None = None


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
    # When the board stopped listing it. Null means still open. The row is
    # never deleted, so this is how the grid tells "gone" from "not there".
    closed_at: str | None = None
    status: str = "untriaged"
    places: list[Place] = Field(default_factory=list)


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


class CountryOption(BaseModel):
    """One country the corpus actually contains, with how many postings."""

    code: str
    name: str
    region: str
    count: int


class RegionOption(BaseModel):
    """One region the corpus actually contains."""

    id: str
    name: str
    count: int


class FilterOptions(BaseModel):
    """What the left rail offers, derived from the data actually present."""

    companies: list[str]
    levels: list[str] = Field(default_factory=lambda: list(LEVELS))
    sources: list[str] = Field(default_factory=lambda: list(SOURCES))
    statuses: list[str] = Field(default_factory=lambda: ["tracked", "untriaged", *STATUSES])
    # Places are offered as counted options rather than a bare list: a country
    # holding two postings and one holding nine hundred should not look alike
    # in a filter rail.
    regions: list[RegionOption] = Field(default_factory=list)
    countries: list[CountryOption] = Field(default_factory=list)


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


class BulkStatusUpdate(BaseModel):
    """Set the same status on several postings at once.

    The list view exists to be acted on in groups, and thirty separate PATCH
    requests to do one thing is not that. Each posting still gets its own
    history row, so the log is identical to thirty individual changes.
    """

    posting_ids: list[str] = Field(min_length=1, max_length=200)
    status: StatusLiteral
    note: str = ""


class BulkStatusResult(BaseModel):
    """What a bulk change did, and what it could not do."""

    updated: list[ApplicationState]
    failed: dict[str, str] = Field(default_factory=dict)


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


class LetterRevision(BaseModel):
    """POST body for revising a draft.

    `letter` is the editor's current contents. It is sent rather than read from
    disk because the person may have edited the draft by hand before asking for
    a change, and revising the saved copy would throw that away without saying
    so.
    """

    instruction: str = Field(min_length=1, max_length=2000)
    letter: str | None = None


class LetterResponse(BaseModel):
    """A drafted letter beside the profile chunks it was grounded in."""

    posting_id: str
    text: str
    path: str
    grounding: list[SearchHitModel]
    todos: list[str]


class ManualPostingBody(BaseModel):
    """Creating or editing a posting you entered yourself.

    Only `company`, `title` and `url` are required, for the same reason a board
    posting needs them: without them the row cannot be linked to or read.
    Everything else is optional, and `level` is inferred from the title when it
    is not given.
    """

    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2000)
    body: str = ""
    location: str | None = None
    posted_at: str | None = None
    deadline: str | None = None
    level: LevelLiteral | None = None
    remote: bool | None = None


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


class ProfileSummary(BaseModel):
    """One project write-up, as the list shows it."""

    slug: str
    title: str
    bytes: int
    chunks: int
    # How many of those chunks have a vector. Chunking is free and immediate;
    # embedding is the step that costs, so a freshly saved document is
    # searchable by keyword before it is searchable by meaning. Showing both
    # numbers is how the person can tell which.
    embedded: int
    ingested: bool


class ProfileDoc(ProfileSummary):
    """One write-up with its text. `bytes` is derived, so it is not required."""

    text: str
    bytes: int = 0


class ProfileDocBody(BaseModel):
    """PUT body for saving a write-up."""

    text: str = Field(max_length=200_000)


class ProfileList(BaseModel):
    """Every write-up, plus how much of the corpus is waiting on `cli embed`."""

    documents: list[ProfileSummary]
    pending_embedding: int = 0


class SyncRequest(BaseModel):
    """POST body for starting a mailbox sync."""

    # Lifts the `-from:me` exclusion. That exclusion exists so your own
    # application to a company is never read as that company's answer to it,
    # so this is for testing with a single mailbox and defaults to off.
    include_sent: bool = False
    limit: int | None = Field(default=None, ge=1, le=500)


class SyncStatus(BaseModel):
    """Whether a sync is running, and what the last one did."""

    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    report: dict[str, Any] | None = None
    # Whether there is a stored refresh token at all. Without it the answer to
    # "why did nothing happen" is a setup step, not a failure.
    authorised: bool = False
