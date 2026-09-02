"""Filling ``posting_locations`` from each posting's raw location string.

The counterpart to :mod:`agent_app.ingest.chunks`, for place rather than text.
Ingestion writes ``postings.location`` as the board wrote it;
:mod:`agent_app.core.locations` resolves that into city, country and region;
this puts the result in a table you can filter on.

Idempotence works the same way it does for chunks, and for the same reason: a
posting is pending when it has no location rows *and* has a non-empty raw
string. No bookkeeping column, nothing that can drift.

The one wrinkle chunks does not have: a posting whose raw string resolves to
nothing at all -- ``"Remote"``, ``""`` -- would look pending forever. Those get
a single sentinel row with ``raw`` set and everything else ``NULL``, which is
also what makes an unparsed location visible in the UI instead of silently
absent.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from ..core.locations import ParsedLocation, parse_locations

log = logging.getLogger(__name__)

DEFAULT_BATCH = 1000

# What a posting with no usable location string gets, so it is not re-examined
# on every run. Distinguishable from a real row because every other column is
# NULL and the raw string is empty.
NO_LOCATION = ""


@dataclass
class LocationReport:
    """What one indexing pass did."""

    pending: int = 0
    postings: int = 0
    rows: int = 0
    resolved: int = 0  # rows that got a country or a region
    unresolved: int = 0  # rows that kept only their raw string

    def format(self) -> str:
        if self.pending == 0:
            return "0 postings to locate."
        line = f"{self.postings:,} posting(s) located into {self.rows:,} place(s)"
        if self.rows:
            share = 100.0 * self.resolved / self.rows
            line += f"\n  {self.resolved:,} resolved to a country or region ({share:.1f}%)"
        if self.unresolved:
            line += f"\n  {self.unresolved:,} kept only the board's own words"
        return line


def pending_posting_ids(conn: sqlite3.Connection) -> list[str]:
    """Every posting with no ``posting_locations`` row yet."""
    return [
        row["id"]
        for row in conn.execute(
            "SELECT p.id FROM postings p "
            "LEFT JOIN posting_locations l ON l.posting_id = p.id "
            "WHERE l.id IS NULL ORDER BY p.id"
        )
    ]


def index_posting(
    conn: sqlite3.Connection, posting_id: str, raw: str | None
) -> list[ParsedLocation]:
    """Replace one posting's location rows, returning what was parsed.

    Used both by the batch pass and whenever a single posting is created or
    edited by hand, so a manual posting is filterable the moment it is saved.
    """
    conn.execute("DELETE FROM posting_locations WHERE posting_id = ?", (posting_id,))

    parsed = parse_locations(raw)
    if not parsed:
        conn.execute(
            "INSERT INTO posting_locations (posting_id, raw) VALUES (?, ?)",
            (posting_id, NO_LOCATION),
        )
        return []

    conn.executemany(
        "INSERT OR IGNORE INTO posting_locations (posting_id, raw, city, country, region)"
        " VALUES (?, ?, ?, ?, ?)",
        [(posting_id, p.raw, p.city, p.country, p.region) for p in parsed],
    )
    return parsed


def index_pending_locations(
    conn: sqlite3.Connection,
    *,
    batch: int = DEFAULT_BATCH,
) -> LocationReport:
    """Parse and store locations for every posting that has none yet."""
    ids = pending_posting_ids(conn)
    report = LocationReport(pending=len(ids))
    if not ids:
        return report

    for start in range(0, len(ids), batch):
        window = ids[start : start + batch]
        marks = ",".join("?" * len(window))
        rows = conn.execute(
            f"SELECT id, location FROM postings WHERE id IN ({marks})", window
        ).fetchall()

        with conn:
            for row in rows:
                parsed = index_posting(conn, row["id"], row["location"])
                report.postings += 1
                report.rows += len(parsed)
                report.resolved += sum(1 for p in parsed if p.resolved)
                report.unresolved += sum(1 for p in parsed if not p.resolved)

        log.info("located %d/%d posting(s)", report.postings, len(ids))

    return report


def reindex_all_locations(conn: sqlite3.Connection) -> LocationReport:
    """Throw away every parsed location and do them all again.

    The table is a cache of what :mod:`agent_app.core.locations` currently
    knows, so widening the city table means the existing rows are stale. This
    is how you take the new table to the whole corpus.
    """
    with conn:
        conn.execute("DELETE FROM posting_locations")
    return index_pending_locations(conn)


def coverage(conn: sqlite3.Connection) -> dict[str, int]:
    """How much of the corpus has a usable place on it.

    The number to watch when extending the tables: `unresolved` names postings
    whose location string the parser could not turn into anything.
    """
    row = conn.execute(
        "SELECT"
        " (SELECT count(*) FROM postings) AS postings,"
        " (SELECT count(DISTINCT posting_id) FROM posting_locations"
        "   WHERE country IS NOT NULL) AS with_country,"
        " (SELECT count(DISTINCT posting_id) FROM posting_locations"
        "   WHERE region IS NOT NULL) AS with_region,"
        " (SELECT count(*) FROM posting_locations"
        "   WHERE raw != '' AND country IS NULL AND region IS NULL) AS unresolved"
    ).fetchone()
    return dict(row)


def top_unresolved(conn: sqlite3.Connection, limit: int = 40) -> list[tuple[str, int]]:
    """The most common location strings the parser could not resolve.

    This is the worklist for extending :mod:`agent_app.core.locations`: the
    strings here, in this order, are where adding a city buys the most.
    """
    return [
        (row["raw"], row["n"])
        for row in conn.execute(
            "SELECT raw, count(*) AS n FROM posting_locations"
            " WHERE raw != '' AND country IS NULL AND region IS NULL"
            " GROUP BY lower(raw) ORDER BY n DESC LIMIT ?",
            (limit,),
        )
    ]
