"""Fetching, upserting and reporting for one ingest run.

Splitting this out from the three source modules keeps them to what they are
actually about — one vendor's JSON shape — while the rules that apply to every
board live in one place: be polite, retry a flaky server, skip a dead board
without taking the run down, and never write a duplicate.

(PLAN.md's Phase 1 tree lists four modules under ``ingest/``. This is a fifth.
The orchestration has to live somewhere, and putting it in ``__init__.py`` or
smearing it across the three source modules would both be worse.)
"""

from __future__ import annotations

import inspect
import logging
import sqlite3
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx

from ..db import SOURCES, Posting
from . import ashby, greenhouse, lever, personio
from .normalize import now_iso

log = logging.getLogger(__name__)

# Which module knows how to talk to which board.
PARSERS: dict[str, ModuleType] = {
    greenhouse.SOURCE: greenhouse,
    lever.SOURCE: lever,
    ashby.SOURCE: ashby,
    personio.SOURCE: personio,
}

DEFAULT_MIN_INTERVAL = 1.0  # seconds between requests: one per second
DEFAULT_TIMEOUT = 20.0
MAX_ATTEMPTS = 3  # the first try plus the two retries PLAN.md asks for


class BoardNotFound(Exception):
    """The board returned 404. The token is wrong, or the company moved boards."""


class FetchFailed(Exception):
    """The board could not be reached, even after retrying."""


@dataclass(frozen=True)
class CompanyEntry:
    """One company to fetch, from ``companies.toml`` or the ``companies`` table.

    ``api_host`` pins which host answered last time. Lever runs two, so
    remembering the answer saves a wasted 404 on every subsequent run.
    """

    source: str
    token: str
    name: str
    api_host: str | None = None


@dataclass
class CompanyResult:
    """What happened for one company during a run."""

    entry: CompanyEntry
    fetched: int = 0
    new: int = 0
    updated: int = 0
    rechunked: int = 0
    closed: int = 0  # were on this board last run and are not on it now
    reopened: int = 0  # were closed and the board is listing them again
    api_host: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class IngestReport:
    """What happened across the whole run."""

    results: list[CompanyResult] = field(default_factory=list)

    @property
    def fetched(self) -> int:
        return sum(r.fetched for r in self.results)

    @property
    def new(self) -> int:
        return sum(r.new for r in self.results)

    @property
    def updated(self) -> int:
        return sum(r.updated for r in self.results)

    @property
    def closed(self) -> int:
        return sum(r.closed for r in self.results)

    @property
    def reopened(self) -> int:
        return sum(r.reopened for r in self.results)

    @property
    def failures(self) -> list[CompanyResult]:
        return [r for r in self.results if not r.ok]


def load_companies(path: Path) -> list[CompanyEntry]:
    """Read ``companies.toml`` into a flat list, ignoring unknown sources."""
    if not path.exists():
        raise FileNotFoundError(f"No companies file at {path}")

    with path.open("rb") as handle:
        data = tomllib.load(handle)

    entries: list[CompanyEntry] = []
    for source in SOURCES:
        for raw in data.get(source) or []:
            if not isinstance(raw, dict):
                continue
            token = str(raw.get("token") or "").strip()
            if not token:
                log.warning("skipping %s entry with no token: %r", source, raw)
                continue
            entries.append(
                CompanyEntry(
                    source=source,
                    token=token,
                    name=str(raw.get("name") or token).strip(),
                )
            )

    for unknown in set(data) - set(SOURCES):
        log.warning("ignoring unknown source %r in %s", unknown, path.name)

    return entries


class PoliteClient:
    """An HTTP client that waits its turn and forgives a flaky server.

    One request per second across the whole run, a real User-Agent so an
    administrator can see who is calling, and two retries on 5xx or a dropped
    connection. A 404 is not retried: the token is simply wrong, and trying
    again will not fix it.
    """

    def __init__(
        self,
        user_agent: str,
        *,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self._min_interval = min_interval
        self._sleep = sleep
        self._last_request: float | None = None
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _wait_turn(self) -> None:
        if self._last_request is None:
            return
        elapsed = time.monotonic() - self._last_request
        remaining = self._min_interval - elapsed
        if remaining > 0:
            self._sleep(remaining)

    def get_json(self, url: str, not_found: tuple[int, ...] = (404,)) -> Any:
        """GET a URL and decode JSON, retrying transient failures."""
        response = self.get(url, not_found=not_found)
        try:
            return response.json()
        except ValueError as exc:
            raise FetchFailed(f"Invalid JSON from {url}: {exc}") from exc

    def get_bytes(self, url: str, not_found: tuple[int, ...] = (404,)) -> bytes:
        """GET a URL and return the undecoded body.

        Personio's feed is XML rather than JSON, and its parser wants the raw
        bytes so the XML declaration decides the encoding rather than httpx
        guessing from a header.
        """
        return self.get(url, not_found=not_found).content

    def get_text(self, url: str, params: dict[str, str] | None = None) -> str:
        """GET a URL and return the body as text.

        The Common Crawl index answers in JSON-lines rather than JSON, so it
        needs this rather than :meth:`get_json` — but it wants the same
        politeness and the same retries.
        """
        return self.get(url, params).text

    def get(
        self,
        url: str,
        params: dict[str, str] | None = None,
        *,
        not_found: tuple[int, ...] = (404,),
    ) -> httpx.Response:
        """GET a URL, waiting our turn and retrying transient failures.

        ``not_found`` is which statuses mean "this board does not exist" for
        this vendor. It is 404 nearly everywhere, but Personio answers 429 with
        an HTML page for an unknown token, and retrying that twice before
        reporting a transient failure would make discovery record the company
        as unresolved and check it again forever.
        """
        last_error: str = "unknown error"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait_turn()
            self._last_request = time.monotonic()

            try:
                response = self._client.get(url, params=params)
            except httpx.RequestError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning("attempt %d/%d for %s failed: %s", attempt, MAX_ATTEMPTS, url, exc)
            else:
                if response.status_code in not_found:
                    raise BoardNotFound(f"HTTP {response.status_code} for {url}")

                if response.status_code >= 500 or response.status_code == 429:
                    last_error = f"HTTP {response.status_code}"
                    log.warning(
                        "attempt %d/%d for %s got %s",
                        attempt,
                        MAX_ATTEMPTS,
                        url,
                        response.status_code,
                    )
                elif response.is_error:
                    raise FetchFailed(f"HTTP {response.status_code} for {url}")
                else:
                    return response

            if attempt < MAX_ATTEMPTS:
                backoff = self._min_interval * (2 ** (attempt - 1))
                self._sleep(backoff)

        raise FetchFailed(f"{url} failed after {MAX_ATTEMPTS} attempts ({last_error})")


def hosts_for(entry: CompanyEntry) -> tuple[str, ...]:
    """Which hosts to try for this company, best guess first."""
    module = PARSERS[entry.source]
    if entry.api_host:
        return (entry.api_host,)
    return tuple(module.HOSTS)


def fetch_company(client: PoliteClient, entry: CompanyEntry) -> tuple[list[Posting], str]:
    """Fetch and parse one company's board, returning the host that answered.

    A board is only declared missing once *every* host has 404'd. Lever runs a
    separate EU API, and a company on it is invisible to the US host.
    """
    module = PARSERS[entry.source]
    hosts = hosts_for(entry)
    not_found = getattr(module, "NOT_FOUND_STATUSES", (404,))
    # One source serves XML; the rest serve JSON. The module says which.
    wants_bytes = getattr(module, "FEED_IS_XML", False)

    for index, host in enumerate(hosts):
        url = module.build_url(entry.token, host)
        try:
            payload = (
                client.get_bytes(url, not_found) if wants_bytes else client.get_json(url, not_found)
            )
        except BoardNotFound:
            if index == len(hosts) - 1:
                raise
            log.info("%s not on %s, trying next host", entry.token, host)
            continue
        return parse_with(module, payload, entry, host), host

    raise BoardNotFound(f"no host served {entry.source}:{entry.token}")


def parse_with(module: ModuleType, payload: Any, entry: CompanyEntry, host: str) -> list[Posting]:
    """Call a source module's ``parse``, passing the extras it accepts.

    Three of the four sources get the company name and nothing else, because
    the board's own response carries the job URL. Personio's does not -- the
    URL has to be built from the token and host -- so its ``parse`` takes two
    more arguments. Inspecting the signature keeps that difference inside the
    one module it belongs to.
    """
    parameters = inspect.signature(module.parse).parameters
    extra: dict[str, Any] = {}
    if "token" in parameters:
        extra["token"] = entry.token
    if "host" in parameters:
        extra["host"] = host
    return module.parse(payload, entry.name, **extra)


def upsert_postings(
    conn: sqlite3.Connection,
    postings: list[Posting],
    *,
    seen_at: str | None = None,
) -> tuple[int, int, int]:
    """Write postings idempotently.

    Returns ``(new, updated, rechunked)``. A posting already in the table keeps
    its ``first_seen`` and always has ``last_seen`` refreshed. When the body
    text has actually changed, the posting's chunks are deleted so Phase 4
    re-embeds it; an unchanged body leaves the chunks and their vectors alone,
    which is the whole reason ``body_hash`` exists.

    Postings that vanish from a board are deliberately left in place.
    """
    if not postings:
        return (0, 0, 0)

    seen_at = seen_at or now_iso()
    ids = [p.id for p in postings]

    placeholders = ",".join("?" * len(ids))
    existing = {
        row["id"]: row["body_hash"]
        for row in conn.execute(
            f"SELECT id, body_hash FROM postings WHERE id IN ({placeholders})", ids
        )
    }

    new = updated = rechunked = 0
    stale: list[str] = []

    with conn:
        for posting in postings:
            previous_hash = existing.get(posting.id)
            if previous_hash is None:
                new += 1
            else:
                updated += 1
                if previous_hash != posting.body_hash:
                    stale.append(posting.id)

            conn.execute(
                """
                INSERT INTO postings (
                    id, source, company, title, location, remote, url, body, body_hash,
                    posted_at, deadline, level, first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    company    = excluded.company,
                    title      = excluded.title,
                    location   = excluded.location,
                    remote     = excluded.remote,
                    url        = excluded.url,
                    body       = excluded.body,
                    body_hash  = excluded.body_hash,
                    posted_at  = COALESCE(excluded.posted_at, postings.posted_at),
                    deadline   = COALESCE(excluded.deadline, postings.deadline),
                    level      = excluded.level,
                    last_seen  = excluded.last_seen
                """,
                (
                    posting.id,
                    posting.source,
                    posting.company,
                    posting.title,
                    posting.location,
                    int(posting.remote),
                    posting.url,
                    posting.body,
                    posting.body_hash,
                    posting.posted_at,
                    posting.deadline,
                    posting.level,
                    seen_at,
                    seen_at,
                ),
            )

        if stale:
            marks = ",".join("?" * len(stale))
            cursor = conn.execute(f"DELETE FROM chunks WHERE posting_id IN ({marks})", stale)
            rechunked = cursor.rowcount if cursor.rowcount > 0 else 0
            log.info("body changed for %d posting(s); dropped %d chunk(s)", len(stale), rechunked)

    return (new, updated, rechunked)


def reconcile_closed(
    conn: sqlite3.Connection,
    entry: CompanyEntry,
    postings: list[Posting],
    *,
    seen_at: str | None = None,
) -> tuple[int, int]:
    """Close postings this company's board no longer lists, reopen ones it does.

    Returns ``(closed, reopened)``.

    The signal costs nothing extra: every board here serves a company's *whole*
    list in one response, so anything we hold for that company and did not see
    in it has been taken down. The set difference is the detection.

    Two things this must not do. It must never run on a failed fetch — a 500
    would otherwise close every posting the company has — which is why the
    caller only reaches here after a successful parse, and why an empty
    response is treated as suspect rather than as "everything closed". And it
    must never delete: an application, its letter and its history all point at
    the posting, and one you applied to is the row you least want to lose.

    Reopening is the same comparison in reverse. Boards do relist a posting,
    and a stale `closed_at` would quietly hide something you could still apply
    to.
    """
    seen_at = seen_at or now_iso()
    live = {p.id for p in postings}

    held = {
        row["id"]: row["closed_at"]
        for row in conn.execute(
            "SELECT id, closed_at FROM postings WHERE source = ? AND company = ?",
            (entry.source, entry.name),
        )
    }
    if not held:
        return (0, 0)

    # A board that answers 200 with nothing is far more often broken than it is
    # a company that fired everyone. Never close a whole roster on that.
    if not live and held:
        log.warning(
            "%s returned no postings but %d are held; not closing any", entry.name, len(held)
        )
        return (0, 0)

    to_close = [pid for pid, closed_at in held.items() if pid not in live and closed_at is None]
    to_reopen = [pid for pid, closed_at in held.items() if pid in live and closed_at is not None]

    if to_close:
        marks = ",".join("?" * len(to_close))
        conn.execute(
            f"UPDATE postings SET closed_at = ? WHERE id IN ({marks})", [seen_at, *to_close]
        )
        log.info("%s: %d posting(s) gone from the board", entry.name, len(to_close))
    if to_reopen:
        marks = ",".join("?" * len(to_reopen))
        conn.execute(f"UPDATE postings SET closed_at = NULL WHERE id IN ({marks})", to_reopen)
        log.info("%s: %d posting(s) back on the board", entry.name, len(to_reopen))

    return (len(to_close), len(to_reopen))


def ingest_company(
    conn: sqlite3.Connection,
    client: PoliteClient,
    entry: CompanyEntry,
    *,
    seen_at: str | None = None,
) -> CompanyResult:
    """Run one company end to end, turning any failure into a reported result."""
    result = CompanyResult(entry=entry)
    try:
        postings, host = fetch_company(client, entry)
    except BoardNotFound:
        result.error = "board not found (404) — check the token"
        log.warning("%s: %s", entry.name, result.error)
        return result
    except (FetchFailed, httpx.HTTPError) as exc:
        result.error = str(exc)
        log.warning("%s: %s", entry.name, result.error)
        return result

    result.api_host = host
    result.fetched = len(postings)
    result.new, result.updated, result.rechunked = upsert_postings(conn, postings, seen_at=seen_at)
    result.closed, result.reopened = reconcile_closed(conn, entry, postings, seen_at=seen_at)

    # Remember which host answered so the next run does not re-probe.
    conn.execute(
        "UPDATE companies SET api_host = ?, job_count = ?, last_checked = ? "
        "WHERE source = ? AND token = ?",
        (host, result.fetched, seen_at or now_iso(), entry.source, entry.token),
    )
    conn.commit()
    return result


def run_ingest(
    conn: sqlite3.Connection,
    entries: list[CompanyEntry],
    client: PoliteClient,
) -> IngestReport:
    """Ingest every company, carrying on past any single failure."""
    seen_at = now_iso()
    report = IngestReport()
    for entry in entries:
        log.info("fetching %s (%s:%s)", entry.name, entry.source, entry.token)
        report.results.append(ingest_company(conn, client, entry, seen_at=seen_at))
    return report


def format_summary(report: IngestReport) -> str:
    """Render the per-company table PLAN.md's Phase 2 check asks for."""
    headers = ("company", "source", "fetched", "new", "updated", "closed")
    rows: list[tuple[str, ...]] = []

    for result in report.results:
        if result.ok:
            rows.append(
                (
                    result.entry.name,
                    result.entry.source,
                    str(result.fetched),
                    str(result.new),
                    str(result.updated),
                    str(result.closed) if result.closed else "",
                )
            )
        else:
            rows.append((result.entry.name, result.entry.source, "—", "—", "—", "—"))

    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]

    def line(cells: tuple[str, ...]) -> str:
        left = "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells[:2]))
        right = "  ".join(cell.rjust(widths[i]) for i, cell in enumerate(cells[2:], start=2))
        return f"{left}  {right}"

    out = [line(headers), "  ".join("-" * w for w in widths)]
    out.extend(line(row) for row in rows)
    out.append("  ".join("-" * w for w in widths))
    out.append(
        line(
            (
                "total",
                "",
                str(report.fetched),
                str(report.new),
                str(report.updated),
                str(report.closed),
            )
        ).rstrip()
    )
    if report.reopened:
        out.append(f"  {report.reopened} posting(s) were relisted and are open again")

    for failure in report.failures:
        out.append(f"! {failure.entry.name} ({failure.entry.source}): {failure.error}")

    return "\n".join(out)
