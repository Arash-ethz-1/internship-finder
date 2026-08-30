"""Phase 2: parsers, the polite HTTP client, and idempotent upserts.

Every test here runs offline. The three source parsers work from saved fixture
files, and the client tests use an ``httpx.MockTransport`` so retry and 404
behaviour is exercised without touching a real job board.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_app.ingest import ashby, greenhouse, lever
from agent_app.ingest.runner import (
    BoardNotFound,
    CompanyEntry,
    CompanyResult,
    FetchFailed,
    IngestReport,
    PoliteClient,
    format_summary,
    ingest_company,
    load_companies,
    upsert_postings,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# --- parsers ---------------------------------------------------------------


def test_greenhouse_parse() -> None:
    postings = greenhouse.parse(load_fixture("greenhouse"), "Acme")

    # The record with a null id is dropped rather than written broken.
    assert len(postings) == 3

    intern = postings[0]
    assert intern.id == "greenhouse:4012345"
    assert intern.company == "Acme"
    assert intern.level == "intern"
    assert intern.remote is True
    assert intern.location == "Remote - Europe"
    # first_published wins over updated_at, converted from -04:00 to UTC.
    assert intern.posted_at == "2026-07-01T12:00:00Z"
    # Double-escaped HTML came out as readable text with the list intact.
    assert "Python & Go" in intern.body
    assert "- Distributed systems" in intern.body
    assert "<p>" not in intern.body

    assert postings[1].level == "unknown"  # "Internal Tools" is not an internship
    assert postings[1].remote is False
    assert postings[2].level == "newgrad"


def test_lever_parse() -> None:
    postings = lever.parse(load_fixture("lever"), "Acme")
    assert len(postings) == 2

    student = postings[0]
    assert student.id == "lever:b1f2c3d4-0000-4444-8888-aaaabbbbcccc"
    assert student.level == "intern"  # "Working Student"
    assert student.remote is False  # workplaceType: onsite
    assert student.posted_at == "2026-06-21T00:00:00Z"
    # The body is stitched from descriptionPlain + lists + additionalPlain.
    assert "Support the ML platform team" in student.body
    assert "What you'll do" in student.body
    assert "- Train models" in student.body
    assert "rolling basis" in student.body

    senior = postings[1]
    assert senior.level == "unknown"
    assert senior.remote is True  # workplaceType: remote


def test_ashby_parse() -> None:
    postings = ashby.parse(load_fixture("ashby"), "Acme")

    # The unlisted job is skipped.
    assert len(postings) == 2

    intern = postings[0]
    assert intern.id == "ashby:9f8e7d6c-2222-4444-8888-111122223333"
    assert intern.level == "intern"
    assert intern.remote is False  # isRemote wins over the location string
    assert intern.posted_at == "2026-08-01T10:00:00Z"

    designer = postings[1]
    assert designer.remote is True
    assert designer.body == "Design the console.\n\n- Figma"


@pytest.mark.parametrize("module", [greenhouse, lever, ashby])
def test_parsers_survive_a_garbage_payload(module: Any) -> None:
    assert module.parse(None, "Acme") == []
    assert module.parse({"jobs": [None, 7, "x"]}, "Acme") == []


def test_build_url_matches_the_documented_endpoints() -> None:
    assert greenhouse.build_url("acme") == (
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    )
    assert lever.build_url("acme") == "https://api.lever.co/v0/postings/acme?mode=json"
    assert ashby.build_url("acme") == (
        "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
    )


# --- companies.toml --------------------------------------------------------


def test_load_companies(tmp_path: Path) -> None:
    path = tmp_path / "companies.toml"
    path.write_text(
        """
[[greenhouse]]
token = "acme"
name = "Acme"

[[lever]]
token = "beta"

[[ashby]]
name = "No token here"

[[unknown_board]]
token = "ignored"
""",
        encoding="utf-8",
    )

    entries = load_companies(path)
    assert [(e.source, e.token, e.name) for e in entries] == [
        ("greenhouse", "acme", "Acme"),
        # Falls back to the token when no display name is given.
        ("lever", "beta", "beta"),
    ]


def test_load_companies_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_companies(tmp_path / "nope.toml")


def test_the_real_companies_file_is_seeded() -> None:
    from agent_app.config import BACKEND_DIR

    entries = load_companies(BACKEND_DIR / "companies.toml")
    by_source: dict[str, int] = {}
    for entry in entries:
        by_source[entry.source] = by_source.get(entry.source, 0) + 1
    assert by_source == {"greenhouse": 5, "lever": 5, "ashby": 5}


# --- the polite client -----------------------------------------------------


def make_client(handler: Any, **kwargs: Any) -> PoliteClient:
    """A client wired to a fake transport, with sleeping stubbed out."""
    return PoliteClient(
        "test-agent",
        min_interval=0.0,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
        **kwargs,
    )


def test_client_returns_json_on_success() -> None:
    with make_client(lambda _req: httpx.Response(200, json={"ok": True})) as client:
        assert client.get_json("https://example.com") == {"ok": True}


def test_client_sends_the_configured_user_agent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, json=[])

    with make_client(handler) as client:
        client.get_json("https://example.com")

    assert seen == ["test-agent"]


def test_client_does_not_retry_a_404() -> None:
    calls = 0

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    with make_client(handler) as client:
        with pytest.raises(BoardNotFound):
            client.get_json("https://example.com")

    assert calls == 1


def test_client_retries_a_5xx_twice_then_succeeds() -> None:
    calls = 0

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"jobs": []})

    with make_client(handler) as client:
        assert client.get_json("https://example.com") == {"jobs": []}

    assert calls == 3


def test_client_gives_up_after_three_attempts() -> None:
    calls = 0

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    with make_client(handler) as client:
        with pytest.raises(FetchFailed):
            client.get_json("https://example.com")

    assert calls == 3


def test_client_retries_a_dropped_connection() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json=[])

    with make_client(handler) as client:
        assert client.get_json("https://example.com") == []

    assert calls == 2


def test_client_rate_limits_between_requests() -> None:
    slept: list[float] = []
    client = PoliteClient(
        "test-agent",
        min_interval=1.0,
        transport=httpx.MockTransport(lambda _req: httpx.Response(200, json=[])),
        sleep=slept.append,
    )
    with client:
        client.get_json("https://example.com/1")
        client.get_json("https://example.com/2")

    # The first request goes straight out; the second waits its turn.
    assert len(slept) == 1
    assert 0 < slept[0] <= 1.0


# --- upserting -------------------------------------------------------------


def test_ingest_is_idempotent(conn: sqlite3.Connection) -> None:
    postings = greenhouse.parse(load_fixture("greenhouse"), "Acme")

    new, updated, rechunked = upsert_postings(conn, postings, seen_at="2026-08-01T00:00:00Z")
    assert (new, updated, rechunked) == (3, 0, 0)

    new, updated, rechunked = upsert_postings(conn, postings, seen_at="2026-08-02T00:00:00Z")
    assert (new, updated, rechunked) == (0, 3, 0)

    # The row count is unchanged, first_seen is preserved, last_seen moved on.
    assert conn.execute("SELECT count(*) FROM postings").fetchone()[0] == 3
    row = conn.execute("SELECT * FROM postings WHERE id = 'greenhouse:4012345'").fetchone()
    assert row["first_seen"] == "2026-08-01T00:00:00Z"
    assert row["last_seen"] == "2026-08-02T00:00:00Z"


def test_postings_that_vanish_from_a_board_are_kept(conn: sqlite3.Connection) -> None:
    postings = greenhouse.parse(load_fixture("greenhouse"), "Acme")
    upsert_postings(conn, postings)

    # The board now returns only the first job.
    upsert_postings(conn, postings[:1])

    assert conn.execute("SELECT count(*) FROM postings").fetchone()[0] == 3


def test_an_unchanged_body_leaves_chunks_alone(conn: sqlite3.Connection) -> None:
    postings = greenhouse.parse(load_fixture("greenhouse"), "Acme")
    upsert_postings(conn, postings)
    conn.execute(
        "INSERT INTO chunks (posting_id, ordinal, text) VALUES ('greenhouse:4012345', 0, 'x')"
    )
    conn.commit()

    _new, _updated, rechunked = upsert_postings(conn, postings)

    assert rechunked == 0
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 1


def test_a_changed_body_drops_the_stale_chunks(conn: sqlite3.Connection) -> None:
    postings = greenhouse.parse(load_fixture("greenhouse"), "Acme")
    upsert_postings(conn, postings)
    conn.execute(
        "INSERT INTO chunks (posting_id, ordinal, text) VALUES ('greenhouse:4012345', 0, 'x')"
    )
    conn.commit()

    payload = load_fixture("greenhouse")
    payload["jobs"][0]["content"] = "&lt;p&gt;Rewritten description.&lt;/p&gt;"
    edited = greenhouse.parse(payload, "Acme")

    _new, _updated, rechunked = upsert_postings(conn, edited)

    assert rechunked == 1
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
    body = conn.execute("SELECT body FROM postings WHERE id = 'greenhouse:4012345'").fetchone()[
        "body"
    ]
    assert body == "Rewritten description."


def test_upsert_of_nothing_is_a_no_op(conn: sqlite3.Connection) -> None:
    assert upsert_postings(conn, []) == (0, 0, 0)


# --- one company end to end ------------------------------------------------


def test_ingest_company_records_a_404_without_raising(conn: sqlite3.Connection) -> None:
    entry = CompanyEntry(source="greenhouse", token="gone", name="Gone Inc")
    with make_client(lambda _req: httpx.Response(404)) as client:
        result = ingest_company(conn, client, entry)

    assert result.ok is False
    assert "404" in (result.error or "")
    assert result.fetched == 0


def test_ingest_company_happy_path(conn: sqlite3.Connection) -> None:
    payload = load_fixture("ashby")
    entry = CompanyEntry(source="ashby", token="acme", name="Acme")
    with make_client(lambda _req: httpx.Response(200, json=payload)) as client:
        result = ingest_company(conn, client, entry)

    assert result.ok
    assert (result.fetched, result.new, result.updated) == (2, 2, 0)


# --- the summary table -----------------------------------------------------


def test_format_summary_has_a_row_per_company_and_a_total() -> None:
    report = IngestReport(
        results=[
            CompanyResult(
                entry=CompanyEntry("greenhouse", "acme", "Acme"), fetched=3, new=2, updated=1
            ),
            CompanyResult(
                entry=CompanyEntry("lever", "gone", "Gone Inc"), error="board not found (404)"
            ),
        ]
    )

    summary = format_summary(report)

    assert "company" in summary
    assert "Acme" in summary
    assert "total" in summary
    assert "! Gone Inc (lever): board not found (404)" in summary
