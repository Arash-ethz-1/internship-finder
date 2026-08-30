"""SQLite schema and connection helpers.

Stdlib ``sqlite3`` only, no ORM. The schema below is the one in PLAN.md, plus
two additions that the plan implies but does not spell out:

* ``postings.body_hash`` — lets re-ingestion tell a changed posting from an
  unchanged one, so chunks are only thrown away and rebuilt when the text
  actually changed.
* a partial unique index on ``chunks.vector_row`` — two chunks pointing at the
  same row in ``vectors.npy`` is a corruption bug, so the database refuses it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def now_iso() -> str:
    """Current time as UTC ISO-8601, e.g. ``2026-08-30T12:34:56Z``.

    Every timestamp written to this database goes through here, so
    ``first_seen``, ``updated_at`` and ``changed_at`` are all directly
    comparable as strings.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# The full status set from PLAN.md's data model. `interested` is the entry
# point; a posting with no `applications` row at all is untriaged, which is a
# distinct state and not the same as `interested`.
STATUSES: tuple[str, ...] = (
    "interested",
    "ready_to_submit",
    "applied",
    "rejected",
    "interviewing",
    "offer",
    "declined",
)

SOURCES: tuple[str, ...] = ("greenhouse", "lever", "ashby")

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
    last_seen   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_postings_company ON postings(company);
CREATE INDEX IF NOT EXISTS idx_postings_level   ON postings(level);
CREATE INDEX IF NOT EXISTS idx_postings_source  ON postings(source);
CREATE INDEX IF NOT EXISTS idx_postings_seen    ON postings(last_seen);

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
"""


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
    """Create every table and index if it does not already exist."""
    with conn:
        conn.executescript(SCHEMA)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Generator[sqlite3.Connection, None, None]:
    """Run a block in one transaction, rolling back if it raises."""
    try:
        with conn:
            yield conn
    except Exception:
        conn.rollback()
        raise


def table_names(conn: sqlite3.Connection) -> list[str]:
    """Return the user tables present, sorted. Used by tests and ``cli init-db``."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]
