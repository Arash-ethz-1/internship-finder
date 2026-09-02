"""Turning candidate companies into verified ones.

The rule the whole phase rests on: **the model proposes, HTTP disposes.** A
token is real when a board returns 200 and never because something asserted it.

The second rule matters just as much: **failures are remembered.** A candidate
that came back 404 on every host of every board is written down as ``dead`` or
``unresolved``, so the next discovery run skips it instead of paying for the
same requests again.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

from ..db import BOARD_SOURCES, now_iso
from . import ashby, greenhouse, lever, personio
from .candidates import Candidate, slug_candidates
from .runner import BoardNotFound, CompanyEntry, FetchFailed, PoliteClient

log = logging.getLogger(__name__)

# Only boards can be discovered. `manual` is in `SOURCES` but is a posting
# you typed in yourself, so there is nothing to probe.
MODULES = {
    greenhouse.SOURCE: greenhouse,
    lever.SOURCE: lever,
    ashby.SOURCE: ashby,
    personio.SOURCE: personio,
}


@dataclass
class DiscoveryReport:
    """What one discovery run did."""

    checked: int = 0
    verified: int = 0
    dead: int = 0
    unresolved: int = 0
    skipped: int = 0  # already known, not re-checked
    requests: int = 0
    found: list[tuple[str, str, str]] = field(default_factory=list)  # source, token, name

    def format(self) -> str:
        lines = [
            f"candidates checked : {self.checked}",
            f"already known      : {self.skipped}",
            f"verified           : {self.verified}",
            f"dead (404)         : {self.dead}",
            f"unresolved         : {self.unresolved}",
            f"http requests      : {self.requests}",
        ]
        if self.found:
            lines.append("")
            lines.append("newly verified:")
            for source, token, name in self.found[:40]:
                lines.append(f"  {source:11} {token:28} {name}")
            if len(self.found) > 40:
                lines.append(f"  ... and {len(self.found) - 40} more")
        return "\n".join(lines)


def known_pairs(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """Every ``(source, token)`` already recorded, whatever its status.

    This is what stops a second run re-probing the same 3,500 tokens.
    """
    return {
        (row["source"], row["token"]) for row in conn.execute("SELECT source, token FROM companies")
    }


def known_names(conn: sqlite3.Connection) -> set[str]:
    """Company names already resolved or ruled out, case-folded."""
    return {
        row["name"].casefold()
        for row in conn.execute("SELECT name FROM companies WHERE name IS NOT NULL")
    }


def record(
    conn: sqlite3.Connection,
    *,
    source: str,
    token: str,
    status: str,
    name: str | None = None,
    job_count: int | None = None,
    api_host: str | None = None,
    discovered_by: str = "crawl",
) -> None:
    """Upsert one company row, preserving ``first_verified`` once it is set."""
    now = now_iso()
    first_verified = now if status == "verified" else None
    with conn:
        conn.execute(
            """
            INSERT INTO companies (
                source, token, name, status, job_count, api_host,
                discovered_by, first_verified, last_checked
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, token) DO UPDATE SET
                name           = COALESCE(excluded.name, companies.name),
                status         = excluded.status,
                job_count      = COALESCE(excluded.job_count, companies.job_count),
                api_host       = COALESCE(excluded.api_host, companies.api_host),
                first_verified = COALESCE(companies.first_verified, excluded.first_verified),
                last_checked   = excluded.last_checked
            """,
            (source, token, name, status, job_count, api_host, discovered_by, first_verified, now),
        )


def probe(
    client: PoliteClient, source: str, token: str
) -> tuple[bool, str | None, int | None, str | None]:
    """Try one token on one board, across every host that board runs.

    Returns ``(found, name, job_count, host)``. Uses each board's cheapest
    verification endpoint — for Greenhouse that is the metadata document, which
    is a few bytes and carries the authoritative company name, rather than
    every job with its full description.
    """
    module = MODULES[source]
    not_found = getattr(module, "NOT_FOUND_STATUSES", (404,))
    for host in module.HOSTS:
        url = module.verify_url(token, host)
        try:
            payload = client.get_json(url, not_found)
        except BoardNotFound:
            continue
        except FetchFailed as exc:
            log.warning("%s:%s on %s failed: %s", source, token, host, exc)
            continue
        name, job_count = module.parse_verification(payload)
        return (True, name, job_count, host)
    return (False, None, None, None)


def verify_candidate(
    conn: sqlite3.Connection,
    client: PoliteClient,
    candidate: Candidate,
    report: DiscoveryReport,
    *,
    sources: tuple[str, ...] = BOARD_SOURCES,
) -> None:
    """Resolve one candidate to a real board, or record that it is not one."""
    # Crawl already knows the exact source and token: one probe settles it.
    if candidate.token and candidate.source:
        if (candidate.source, candidate.token) in _cache(conn):
            report.skipped += 1
            return
        report.checked += 1
        report.requests += 1
        found, name, job_count, host = probe(client, candidate.source, candidate.token)
        if found:
            report.verified += 1
            display = name or candidate.name or _prettify(candidate.token)
            report.found.append((candidate.source, candidate.token, display))
            record(
                conn,
                source=candidate.source,
                token=candidate.token,
                status="verified",
                name=display,
                job_count=job_count,
                api_host=host,
                discovered_by=candidate.origin,
            )
        else:
            report.dead += 1
            record(
                conn,
                source=candidate.source,
                token=candidate.token,
                status="dead",
                discovered_by=candidate.origin,
            )
        _cache(conn).add((candidate.source, candidate.token))
        return

    # A name with no token: derive slugs and try them across every board,
    # because a company's ATS today is not the one a model remembers.
    if not candidate.name:
        return
    if candidate.name.casefold() in _name_cache(conn):
        report.skipped += 1
        return

    report.checked += 1
    slugs = slug_candidates(candidate.name)
    ordered = _order_sources(candidate.source, sources)

    for slug in slugs:
        for source in ordered:
            if (source, slug) in _cache(conn):
                continue
            report.requests += 1
            found, name, job_count, host = probe(client, source, slug)
            _cache(conn).add((source, slug))
            if found:
                report.verified += 1
                display = name or candidate.name
                report.found.append((source, slug, display))
                record(
                    conn,
                    source=source,
                    token=slug,
                    status="verified",
                    name=display,
                    job_count=job_count,
                    api_host=host,
                    discovered_by=candidate.origin,
                )
                _name_cache(conn).add(candidate.name.casefold())
                return
            record(conn, source=source, token=slug, status="dead", discovered_by=candidate.origin)

    # Every slug on every board came back empty. Record it against the most
    # likely board so the name is never tried again.
    report.unresolved += 1
    fallback_token = slugs[0] if slugs else candidate.name.casefold()
    record(
        conn,
        source=ordered[0],
        token=fallback_token,
        status="unresolved",
        name=candidate.name,
        discovered_by=candidate.origin,
    )
    _name_cache(conn).add(candidate.name.casefold())


def _order_sources(preferred: str | None, sources: tuple[str, ...]) -> tuple[str, ...]:
    """Try the model's guessed board first, then the others."""
    if preferred and preferred in sources:
        return (preferred, *(s for s in sources if s != preferred))
    return sources


def _prettify(token: str) -> str:
    """A readable fallback name when no board publishes one."""
    return token.replace("-", " ").replace("_", " ").strip().title()


# Per-connection memo of what has been checked, so one run does not re-query
# SQLite for every candidate.
_CACHES: dict[int, set[tuple[str, str]]] = {}
_NAME_CACHES: dict[int, set[str]] = {}


def _cache(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    key = id(conn)
    if key not in _CACHES:
        _CACHES[key] = known_pairs(conn)
    return _CACHES[key]


def _name_cache(conn: sqlite3.Connection) -> set[str]:
    key = id(conn)
    if key not in _NAME_CACHES:
        _NAME_CACHES[key] = known_names(conn)
    return _NAME_CACHES[key]


def reset_caches() -> None:
    """Drop the memos. Tests call this between databases."""
    _CACHES.clear()
    _NAME_CACHES.clear()


def run_discovery(
    conn: sqlite3.Connection,
    client: PoliteClient,
    candidates: list[Candidate],
    *,
    sources: tuple[str, ...] = BOARD_SOURCES,
    limit: int | None = None,
) -> DiscoveryReport:
    """Verify a batch of candidates and record every outcome."""
    report = DiscoveryReport()
    for candidate in candidates:
        if limit is not None and report.checked >= limit:
            break
        verify_candidate(conn, client, candidate, report, sources=sources)
    return report


# --- reading companies back out --------------------------------------------


def load_verified(conn: sqlite3.Connection, source: str | None = None) -> list[CompanyEntry]:
    """Every verified company, as ingest entries."""
    sql = "SELECT source, token, name, api_host FROM companies WHERE status = 'verified'"
    params: list[str] = []
    if source:
        sql += " AND source = ?"
        params.append(source)
    sql += " ORDER BY source, token"
    return [
        CompanyEntry(
            source=row["source"],
            token=row["token"],
            name=row["name"] or row["token"],
            api_host=row["api_host"],
        )
        for row in conn.execute(sql, params)
    ]


def seed_from_toml(conn: sqlite3.Connection, entries: list[CompanyEntry]) -> int:
    """Import ``companies.toml`` into the table as verified rows.

    The seed file was hand-verified against the live APIs, so its entries start
    as ``verified`` rather than being re-probed. Returns how many were added.
    """
    existing = known_pairs(conn)
    added = 0
    for entry in entries:
        if (entry.source, entry.token) in existing:
            continue
        record(
            conn,
            source=entry.source,
            token=entry.token,
            status="verified",
            name=entry.name,
            api_host=entry.api_host,
            discovered_by="seed",
        )
        added += 1
    reset_caches()
    return added


def company_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """How many companies are in each status."""
    return {
        row["status"]: row["n"]
        for row in conn.execute("SELECT status, count(*) AS n FROM companies GROUP BY status")
    }
