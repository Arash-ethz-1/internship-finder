"""SQLite schema and connection helpers.

Stdlib ``sqlite3`` only, no ORM. The schema below is the one in PLAN.md, plus
a few additions that the plan implies but does not spell out:

* ``postings.body_hash`` — lets re-ingestion tell a changed posting from an
  unchanged one, so chunks are only thrown away and rebuilt when the text
  actually changed.
* a partial unique index on ``chunks.vector_row`` — two chunks pointing at the
  same row in ``vectors.npy`` is a corruption bug, so the database refuses it.
* ``email_matches.sender``, ``.snippet`` and ``.dismissed`` — a review queue
  that cannot show who an email is from is not reviewable, the classifier
  needs the snippet, and "the user rejects this suggestion" needs somewhere
  to be recorded or the same email is offered forever.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    """Current time as UTC ISO-8601, e.g. ``2026-08-30T12:34:56Z``.

    Every timestamp written to this database goes through here, so
    ``first_seen``, ``updated_at`` and ``changed_at`` are all directly
    comparable as strings.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _optional(row: sqlite3.Row, column: str) -> Any:
    """Read a column that a narrower SELECT may not have projected.

    ``sqlite3.Row`` raises rather than returning ``None`` for a missing column,
    and several queries here project a subset of ``postings``.
    """
    try:
        return row[column]
    except (IndexError, KeyError):
        return None


# The full status set from PLAN.md's data model, plus `found` (added
# 2026-09-01). A posting with no `applications` row at all is untriaged, which
# is a distinct state and not the same as any of these.
#
# `found` is the entry point and the only status nobody chooses: the agent's
# `find_postings` writes it when a search surfaces a posting, so results
# persist and a later search never returns the same posting twice. It records
# provenance, not a decision -- the person has not judged the posting yet.
STATUSES: tuple[str, ...] = (
    "found",
    "not_relevant",
    "interested",
    "applied",
    "rejected",
    "interviewing",
    "offer",
    "declined",
)

# `ready_to_submit` was removed on 2026-09-02. It described a state of your
# intent rather than a state of the world, and "interested but not yet sent"
# already covers it. The one real thing it encoded -- the letter is written,
# it just needs sending -- is `letter_path IS NOT NULL AND status =
# 'interested'`, which is a filter rather than a status. `migrate()` moves any
# surviving row to `interested` and writes a history entry saying so.
RETIRED_STATUSES: dict[str, str] = {"ready_to_submit": "interested"}

# `not_relevant` is you passing on a posting; `rejected` is a company passing
# on you. Conflating them was a real bug: triaging a search result as "not for
# me" wrote `rejected`, which made the pipeline read as forty rejections you
# never received, and — worse — made those postings candidates for matching a
# real rejection email.

# The statuses that mean the person has an application a company could answer.
# `found` and `not_relevant` are excluded on purpose, and the exclusion is
# load-bearing: the email matcher resolves a message against this set, so a
# posting a search merely surfaced, or one you dismissed without applying to,
# must never be a candidate for "your application was rejected". Anything that
# means "my pipeline" filters on this, not STATUSES.
TRACKED_STATUSES: tuple[str, ...] = tuple(s for s in STATUSES if s not in ("found", "not_relevant"))

# Boards `cli ingest` fetches. Every one of these serves a whole company's
# jobs from one public JSON endpoint, which is what makes closed-posting
# detection possible: whatever the board omits has been taken down.
#
# The first three are the US-startup ATSes PLAN.md started with. `personio`
# was added on 2026-09-02 to reach European postings, which the other three
# barely carry: it is the default ATS for German, Austrian and Swiss employers,
# and `Praktikum` and `Werkstudent` listings live there.
#
# Probed on 2026-09-02 and deliberately not added: Recruitee (every token
# tried 404s, so the documented host is wrong or gone), SmartRecruiters
# (answers 200 with an empty list for *any* token, so a board cannot be
# verified and a typo would look alive), Workable (the widget endpoint returns
# the account but zero jobs for every board tried), Join (422 on the
# documented path). Each needs its own investigation before it earns a module.
BOARD_SOURCES: tuple[str, ...] = ("greenhouse", "lever", "ashby", "personio")

# `manual` is not a board. It is a posting you entered by hand -- LinkedIn, a
# company's own site, an email from a friend -- and it exists so applications
# made outside the three ATSes are still tracked, still searchable, and still
# matched against your mail. Ingest never touches a manual posting, which is
# also why it can never be marked closed by a board that has never heard of it.
MANUAL_SOURCE = "manual"

SOURCES: tuple[str, ...] = (*BOARD_SOURCES, MANUAL_SOURCE)

LEVELS: tuple[str, ...] = ("intern", "newgrad", "unknown")


@dataclass(frozen=True)
class Posting:
    """One row of the ``postings`` table.

    Lives here rather than in ``ingest/`` because both halves of the app use
    it: ingestion builds one to write, and ``core/`` reads one back out.
    ``first_seen`` and ``last_seen`` are unset until the row is written, which
    is why they default to ``None``.
    """

    id: str
    source: str
    company: str
    title: str
    location: str | None
    remote: bool
    url: str
    body: str
    body_hash: str
    posted_at: str | None = None
    deadline: str | None = None
    level: str = "unknown"
    first_seen: str | None = None
    last_seen: str | None = None
    closed_at: str | None = None

    @property
    def is_closed(self) -> bool:
        """Has the board stopped listing this posting."""
        return self.closed_at is not None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Posting:
        """Rebuild a Posting from a ``SELECT * FROM postings`` row."""
        return cls(
            id=row["id"],
            source=row["source"],
            company=row["company"],
            title=row["title"],
            location=row["location"],
            remote=bool(row["remote"]),
            url=row["url"],
            body=row["body"],
            body_hash=row["body_hash"],
            posted_at=row["posted_at"],
            deadline=row["deadline"],
            level=row["level"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            closed_at=_optional(row, "closed_at"),
        )


def get_posting(conn: sqlite3.Connection, posting_id: str) -> Posting | None:
    """Fetch one posting by id, or ``None`` if it is not there."""
    row = conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
    return Posting.from_row(row) if row is not None else None


SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id          TEXT PRIMARY KEY,          -- "{source}:{external_id}"
    source      TEXT NOT NULL,
    company     TEXT NOT NULL,
    title       TEXT NOT NULL,
    location    TEXT,
    remote      INTEGER NOT NULL DEFAULT 0,
    url         TEXT NOT NULL,
    body        TEXT NOT NULL,             -- plain text, HTML stripped
    body_hash   TEXT NOT NULL,             -- sha256 of body; drives re-chunking
    posted_at   TEXT,                      -- UTC ISO-8601
    deadline    TEXT,
    level       TEXT NOT NULL DEFAULT 'unknown',
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    -- When the board stopped listing this posting. NULL means still open.
    -- The row is never deleted: an application, its letter and its history all
    -- point here, and a posting you applied to is the one you least want to
    -- lose. Closed means "do not show me this as a candidate", not "forget it".
    closed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_postings_company ON postings(company);
CREATE INDEX IF NOT EXISTS idx_postings_level   ON postings(level);
CREATE INDEX IF NOT EXISTS idx_postings_source  ON postings(source);
CREATE INDEX IF NOT EXISTS idx_postings_seen    ON postings(last_seen);
CREATE INDEX IF NOT EXISTS idx_postings_closed  ON postings(closed_at);

-- One row per place a posting is offered in. A table rather than columns on
-- `postings` because multi-location postings are ordinary -- "Zurich; London",
-- "Remote - EMEA" -- and flattening them to one city silently loses the rest.
-- `raw` keeps the board's own string so a bad parse is always visible.
CREATE TABLE IF NOT EXISTS posting_locations (
    id         INTEGER PRIMARY KEY,
    posting_id TEXT NOT NULL REFERENCES postings(id) ON DELETE CASCADE,
    raw        TEXT NOT NULL,
    city       TEXT,
    country    TEXT,                      -- ISO 3166-1 alpha-2, e.g. "CH"
    region     TEXT,                      -- "europe", "north_america", ...
    UNIQUE (posting_id, raw)
);

CREATE INDEX IF NOT EXISTS idx_posting_locations_posting ON posting_locations(posting_id);
CREATE INDEX IF NOT EXISTS idx_posting_locations_country ON posting_locations(country);
CREATE INDEX IF NOT EXISTS idx_posting_locations_region  ON posting_locations(region);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY,
    posting_id  TEXT REFERENCES postings(id) ON DELETE CASCADE,
    profile_doc TEXT,                      -- markdown filename slug
    ordinal     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    vector_row  INTEGER,                   -- row index into vectors.npy
    -- Exactly one of posting_id / profile_doc is set.
    CHECK ((posting_id IS NULL) != (profile_doc IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_chunks_posting ON chunks(posting_id);
CREATE INDEX IF NOT EXISTS idx_chunks_profile ON chunks(profile_doc);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_vector_row
    ON chunks(vector_row) WHERE vector_row IS NOT NULL;

CREATE TABLE IF NOT EXISTS applications (
    posting_id  TEXT PRIMARY KEY REFERENCES postings(id) ON DELETE CASCADE,
    status      TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    letter_path TEXT,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);

CREATE TABLE IF NOT EXISTS status_history (
    id          INTEGER PRIMARY KEY,
    posting_id  TEXT NOT NULL REFERENCES postings(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    changed_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_status_history_posting ON status_history(posting_id);

CREATE TABLE IF NOT EXISTS companies (
    source         TEXT NOT NULL,          -- greenhouse | lever | ashby
    token          TEXT NOT NULL,
    name           TEXT,
    status         TEXT NOT NULL,          -- verified | dead | unresolved
    job_count      INTEGER,
    api_host       TEXT,                   -- which host answered; Lever has two
    discovered_by  TEXT NOT NULL,          -- seed | crawl | llm | file
    first_verified TEXT,
    last_checked   TEXT NOT NULL,
    PRIMARY KEY (source, token)
);

CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(status);

CREATE TABLE IF NOT EXISTS email_matches (
    id               INTEGER PRIMARY KEY,
    message_id       TEXT NOT NULL UNIQUE,   -- Gmail's id; makes re-runs idempotent
    posting_id       TEXT REFERENCES postings(id) ON DELETE CASCADE,
    company_guess    TEXT,
    sender           TEXT NOT NULL DEFAULT '',
    received_at      TEXT,
    subject          TEXT NOT NULL DEFAULT '',
    snippet          TEXT NOT NULL DEFAULT '',
    classification   TEXT,                   -- rejection | interview | offer | other
    confidence       REAL,
    suggested_status TEXT,
    applied          INTEGER NOT NULL DEFAULT 0,  -- has the user accepted this
    dismissed        INTEGER NOT NULL DEFAULT 0,  -- has the user rejected it
    created_at       TEXT NOT NULL,
    -- A suggestion is pending, accepted or dismissed. Never two of those.
    CHECK (NOT (applied = 1 AND dismissed = 1))
);

CREATE INDEX IF NOT EXISTS idx_email_matches_posting ON email_matches(posting_id);
CREATE INDEX IF NOT EXISTS idx_email_matches_pending
    ON email_matches(applied, dismissed);
"""

# What the classifier is allowed to return, and the status each one suggests.
# `other` is the escape hatch and deliberately suggests nothing: a newsletter
# from a company you applied to is a real email about no application at all.
CLASSIFICATIONS: tuple[str, ...] = ("rejection", "interview", "offer", "other")

SUGGESTED_STATUS: dict[str, str | None] = {
    "rejection": "rejected",
    "interview": "interviewing",
    "offer": "offer",
    "other": None,
}

# A company row is one of these. `unresolved` means we had a name, tried every
# derived token on every board, and found nothing — recorded so the same
# candidate is never checked twice.
COMPANY_STATUSES: tuple[str, ...] = ("verified", "dead", "unresolved")


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a connection with the pragmas this app relies on.

    ``check_same_thread=False`` is safe here because :mod:`agent_app.runtime`
    hands out one connection per thread rather than sharing a single one.
    """
    path = Path(db_path)
    if path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets the dashboard read while an ingest run writes.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create every table and index if it does not already exist, then migrate.

    Columns are added *before* the schema script runs, not after. ``CREATE
    TABLE IF NOT EXISTS`` does nothing to a table that already exists, so an
    index over a newly added column would be created against a table that does
    not have it yet. On a fresh database the column pass finds no tables and
    does nothing, which is exactly right.
    """
    _add_missing_columns(conn)
    with conn:
        conn.executescript(SCHEMA)
    _migrate_retired_statuses(conn)


# Columns added to existing tables after the first release. `CREATE TABLE IF
# NOT EXISTS` does nothing for a table that already exists, so these need an
# explicit ALTER. Adding a nullable column to SQLite is instant regardless of
# table size.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (("postings", "closed_at", "TEXT"),)


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Bring an existing database up to the current schema.

    Runs on every connection, does nothing when there is nothing to do, and
    returns a line per change so the caller can log what happened. Kept
    deliberately small: this project has one database on one machine, so a
    migration framework would be more moving parts than the problem has.
    """
    return [*_add_missing_columns(conn), *_migrate_retired_statuses(conn)]


def _add_missing_columns(conn: sqlite3.Connection) -> list[str]:
    """``ALTER TABLE ... ADD COLUMN`` for anything the schema gained later."""
    applied: list[str] = []

    for table, column, decl in _ADDED_COLUMNS:
        present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not present:  # table does not exist yet; SCHEMA will have made it
            continue
        if column not in present:
            with conn:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            applied.append(f"{table}.{column} added")

    return applied


def _migrate_retired_statuses(conn: sqlite3.Connection) -> list[str]:
    """Move applications off a status that no longer exists.

    Every move writes a ``status_history`` row. A status disappearing from the
    app must not make the change to your pipeline invisible -- the same rule
    that was applied when `not_relevant` was introduced.
    """
    applied: list[str] = []
    for old, new in RETIRED_STATUSES.items():
        rows = conn.execute(
            "SELECT posting_id FROM applications WHERE status = ?", (old,)
        ).fetchall()
        if not rows:
            continue

        changed_at = now_iso()
        note = f"status '{old}' was retired; moved to '{new}'"
        with conn:
            conn.executemany(
                "INSERT INTO status_history (posting_id, from_status, to_status, note, changed_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [(row["posting_id"], old, new, note, changed_at) for row in rows],
            )
            conn.execute(
                "UPDATE applications SET status = ?, updated_at = ? WHERE status = ?",
                (new, changed_at, old),
            )
        applied.append(f"{len(rows)} application(s) moved from '{old}' to '{new}'")
    return applied


@contextmanager
def transaction(conn: sqlite3.Connection) -> Generator[sqlite3.Connection, None, None]:
    """Run a block in one transaction, rolling back if it raises."""
    try:
        with conn:
            yield conn
    except Exception:
        conn.rollback()
        raise


@dataclass(frozen=True)
class PostingFilters:
    """What the dashboard's left rail can narrow the grid by."""

    q: str | None = None  # free text over title and company
    company: str | None = None
    level: str | None = None
    location: str | None = None
    remote: bool | None = None
    source: str | None = None
    # Any number of statuses, OR-ed. "untriaged" and "tracked" are pseudo-
    # statuses: no application row, and any application row. Empty means no
    # constraint, which is not the same as "every status" -- `untriaged` rows
    # have no status at all to be in a list.
    statuses: tuple[str, ...] = ()
    posted_after: str | None = None
    # Normalised place, from `posting_locations`. `country` is ISO alpha-2 and
    # `region` one of core.locations.REGIONS. Both match if *any* of a
    # posting's locations match, because a Zurich-and-London posting is in
    # Europe once, not twice.
    country: str | None = None
    region: str | None = None
    # Closed postings are hidden by default: the grid is a list of things you
    # could still apply to. They are never deleted, and this is how you look.
    include_closed: bool = False
    only_closed: bool = False


SORTABLE = {
    "posted_at": "p.posted_at",
    "company": "p.company",
    "title": "p.title",
    "level": "p.level",
    "first_seen": "p.first_seen",
}


def _posting_where(filters: PostingFilters) -> tuple[str, list[Any]]:
    """Build the shared WHERE clause for listing and counting."""
    where: list[str] = []
    params: list[Any] = []

    if filters.q:
        where.append("(p.title LIKE ? OR p.company LIKE ?)")
        params.extend([f"%{filters.q}%", f"%{filters.q}%"])
    if filters.company:
        where.append("p.company = ?")
        params.append(filters.company)
    if filters.level:
        where.append("p.level = ?")
        params.append(filters.level)
    if filters.location:
        where.append("p.location LIKE ?")
        params.append(f"%{filters.location}%")
    if filters.remote is not None:
        where.append("p.remote = ?")
        params.append(int(filters.remote))
    if filters.source:
        where.append("p.source = ?")
        params.append(filters.source)
    if filters.posted_after:
        where.append("p.posted_at >= ?")
        params.append(filters.posted_after)
    if filters.country:
        where.append(
            "EXISTS (SELECT 1 FROM posting_locations l WHERE l.posting_id = p.id AND l.country = ?)"
        )
        params.append(filters.country.upper())
    if filters.region:
        where.append(
            "EXISTS (SELECT 1 FROM posting_locations l WHERE l.posting_id = p.id AND l.region = ?)"
        )
        params.append(filters.region)
    if filters.only_closed:
        where.append("p.closed_at IS NOT NULL")
    elif not filters.include_closed:
        where.append("p.closed_at IS NULL")
    if filters.statuses:
        alternatives: list[str] = []
        if "untriaged" in filters.statuses:
            alternatives.append("a.posting_id IS NULL")
        if "tracked" in filters.statuses:
            # Everything the person has touched, whatever they decided.
            alternatives.append("a.posting_id IS NOT NULL")
        concrete = [s for s in filters.statuses if s in STATUSES]
        if concrete:
            marks = ",".join("?" * len(concrete))
            alternatives.append(f"a.status IN ({marks})")
            params.extend(concrete)
        # Every named status was unrecognised: match nothing rather than
        # silently widening to everything.
        where.append("(" + " OR ".join(alternatives) + ")" if alternatives else "0")

    return (" WHERE " + " AND ".join(where) if where else "", params)


def list_postings(
    conn: sqlite3.Connection,
    filters: PostingFilters,
    *,
    limit: int = 500,
    offset: int = 0,
    sort: str = "posted_at",
    descending: bool = True,
) -> tuple[list[sqlite3.Row], int]:
    """Return one page of postings and the total matching count.

    The status comes back with each row via a LEFT JOIN, so the grid never has
    to issue a second query per row. A posting with no application row reports
    ``untriaged`` rather than NULL.
    """
    clause, params = _posting_where(filters)
    base = "FROM postings p LEFT JOIN applications a ON a.posting_id = p.id" + clause

    total = conn.execute(f"SELECT count(*) {base}", params).fetchone()[0]

    column = SORTABLE.get(sort, SORTABLE["posted_at"])
    direction = "DESC" if descending else "ASC"
    rows = conn.execute(
        f"SELECT p.*, COALESCE(a.status, 'untriaged') AS status, a.note, a.letter_path "
        f"{base} ORDER BY {column} {direction} NULLS LAST, p.id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return (rows, total)


def stats(conn: sqlite3.Connection, recent_days: int = 30) -> dict[str, Any]:
    """Counts for the pipeline view: by status, company, source and recency."""
    by_status = {
        row["status"]: row["n"]
        for row in conn.execute(
            "SELECT COALESCE(a.status, 'untriaged') AS status, count(*) AS n "
            "FROM postings p LEFT JOIN applications a ON a.posting_id = p.id "
            "GROUP BY status ORDER BY n DESC"
        )
    }
    by_company = [
        {"company": row["company"], "count": row["n"], "intern": row["interns"]}
        for row in conn.execute(
            "SELECT company, count(*) AS n, "
            "sum(CASE WHEN level = 'intern' THEN 1 ELSE 0 END) AS interns "
            "FROM postings GROUP BY company ORDER BY n DESC"
        )
    ]
    by_source = {
        row["source"]: row["n"]
        for row in conn.execute("SELECT source, count(*) AS n FROM postings GROUP BY source")
    }
    by_level = {
        row["level"]: row["n"]
        for row in conn.execute("SELECT level, count(*) AS n FROM postings GROUP BY level")
    }
    recent = [
        {"date": row["day"], "count": row["n"]}
        for row in conn.execute(
            "SELECT substr(posted_at, 1, 10) AS day, count(*) AS n FROM postings "
            "WHERE posted_at IS NOT NULL GROUP BY day ORDER BY day DESC LIMIT ?",
            (recent_days,),
        )
    ]
    return {
        "total": conn.execute("SELECT count(*) FROM postings").fetchone()[0],
        "by_status": by_status,
        "by_company": by_company,
        "by_source": by_source,
        "by_level": by_level,
        "recent": list(reversed(recent)),
    }


def distinct_values(conn: sqlite3.Connection, column: str) -> list[str]:
    """Distinct non-null values of one filterable column, for the left rail."""
    if column not in {"company", "level", "source", "location"}:
        raise ValueError(f"{column!r} is not a filterable column")
    return [
        row[0]
        for row in conn.execute(
            f"SELECT DISTINCT {column} FROM postings WHERE {column} IS NOT NULL ORDER BY {column}"
        )
    ]


def table_names(conn: sqlite3.Connection) -> list[str]:
    """Return the user tables present, sorted. Used by tests and ``cli init-db``."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]
