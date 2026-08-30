"""Phase 2.5: candidate sources, slug derivation, verification and caching.

Offline. Every board response comes from an ``httpx.MockTransport``, so the
retry, multi-host and 404-caching behaviour is exercised without a single real
request.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_app.ingest import ashby, discovery, greenhouse, lever
from agent_app.ingest.candidates import (
    Candidate,
    from_file,
    slug_candidates,
    token_from_url,
)
from agent_app.ingest.discovery import (
    DiscoveryReport,
    company_counts,
    load_verified,
    record,
    run_discovery,
    seed_from_toml,
)
from agent_app.ingest.runner import CompanyEntry, PoliteClient


@pytest.fixture(autouse=True)
def _clear_discovery_caches() -> None:
    discovery.reset_caches()


def make_client(handler: Any) -> PoliteClient:
    return PoliteClient(
        "test-agent",
        min_interval=0.0,
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )


# --- slug derivation -------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # Both the full and suffix-stripped forms are kept: Match Group's real
        # Lever token is `matchgroup`, so stripping "Group" alone would lose it.
        ("Match Group", ["matchgroup", "match", "match-group"]),
        ("Stripe", ["stripe"]),
        ("Veeva Systems", ["veevasystems", "veeva-systems", "veeva"]),
        ("Acme GmbH", ["acmegmbh", "acme", "acme-gmbh"]),
        ("Example Inc.", ["exampleinc", "example", "example-inc"]),
        # A suffix only counts as a standalone trailing word, so this survives.
        ("Formlabs", ["formlabs"]),
        ("Hugging Face", ["huggingface", "hugging-face", "hugging"]),
    ],
)
def test_slug_candidates(name: str, expected: list[str]) -> None:
    assert slug_candidates(name) == expected


def test_slug_candidates_strips_punctuation() -> None:
    # The apostrophe is removed, not turned into a word break.
    assert slug_candidates("Ben & Jerry's") == ["benjerrys", "ben-jerrys", "ben"]


def test_slug_candidates_of_junk_is_empty() -> None:
    assert slug_candidates("   ") == []
    assert slug_candidates("!!!") == []


# --- crawl URL parsing -----------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/stripe", "stripe"),
        ("https://boards.greenhouse.io/100Thieves", "100Thieves"),
        ("https://boards.greenhouse.io/0x/jobs/4769557002?utm_source=x", "0x"),
        ("https://jobs.ashbyhq.com/openai/abc-def?source=y", "openai"),
        ("https://jobs.eu.lever.co/mobileye/uuid", "mobileye"),
        # Not companies.
        ("https://jobs.lever.co/robots.txt", None),
        ("https://boards.greenhouse.io/", None),
        ("https://jobs.ashbyhq.com/embed/x", None),
    ],
)
def test_token_from_url(url: str, expected: str | None) -> None:
    assert token_from_url(url) == expected


# --- file source -----------------------------------------------------------


def test_from_file_skips_blanks_and_comments(tmp_path: Path) -> None:
    path = tmp_path / "names.txt"
    path.write_text("# my targets\nANYbotics\n\n  Sevensense  \n", encoding="utf-8")
    candidates = from_file(path)
    assert [c.name for c in candidates] == ["ANYbotics", "Sevensense"]
    assert all(c.origin == "file" for c in candidates)


def test_from_file_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        from_file(tmp_path / "nope.txt")


# --- verification endpoints ------------------------------------------------


def test_verify_urls_are_the_cheap_ones() -> None:
    # Greenhouse: metadata, not every job with its full description.
    assert greenhouse.verify_url("acme") == "https://boards-api.greenhouse.io/v1/boards/acme"
    assert "content=true" not in greenhouse.verify_url("acme")
    assert lever.verify_url("acme") == "https://api.lever.co/v0/postings/acme?mode=json"
    assert ashby.verify_url("acme") == "https://api.ashbyhq.com/posting-api/job-board/acme"


def test_greenhouse_verification_yields_the_authoritative_name() -> None:
    assert greenhouse.parse_verification({"name": "Stripe", "content": ""}) == ("Stripe", None)
    assert greenhouse.parse_verification({}) == (None, None)


def test_lever_and_ashby_verification_yield_job_counts() -> None:
    assert lever.parse_verification([{}, {}, {}]) == (None, 3)
    assert ashby.parse_verification({"jobs": [{"isListed": True}, {"isListed": False}]}) == (
        None,
        1,
    )


# --- the lever EU host -----------------------------------------------------


def test_lever_knows_both_api_hosts() -> None:
    assert lever.HOSTS == ("api.lever.co", "api.eu.lever.co")


def test_probe_falls_back_to_the_eu_host() -> None:
    # The exact shape of the bug: 404 on the US host, 200 on the EU host.
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == "api.eu.lever.co":
            return httpx.Response(200, json=[{"id": "1"}, {"id": "2"}])
        return httpx.Response(404)

    with make_client(handler) as client:
        found, name, job_count, host = discovery.probe(client, "lever", "mobileye")

    assert found is True
    assert host == "api.eu.lever.co"
    assert job_count == 2
    assert seen == ["api.lever.co", "api.eu.lever.co"]


def test_probe_reports_missing_only_after_every_host(conn: sqlite3.Connection) -> None:
    with make_client(lambda _r: httpx.Response(404)) as client:
        found, _name, _count, host = discovery.probe(client, "lever", "nobody")
    assert found is False
    assert host is None


def test_fetch_company_tries_the_eu_host_before_giving_up() -> None:
    from agent_app.ingest.runner import fetch_company

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.eu.lever.co":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    entry = CompanyEntry(source="lever", token="seb", name="SEB")
    with make_client(handler) as client:
        postings, host = fetch_company(client, entry)

    assert postings == []
    assert host == "api.eu.lever.co"


def test_fetch_company_uses_a_pinned_host_directly() -> None:
    from agent_app.ingest.runner import fetch_company

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, json=[])

    entry = CompanyEntry(source="lever", token="seb", name="SEB", api_host="api.eu.lever.co")
    with make_client(handler) as client:
        fetch_company(client, entry)

    # Remembering the host means no wasted 404 on the US one.
    assert seen == ["api.eu.lever.co"]


# --- verifying candidates --------------------------------------------------


def test_a_crawl_candidate_is_verified_with_one_request(conn: sqlite3.Connection) -> None:
    report = DiscoveryReport()
    with make_client(lambda _r: httpx.Response(200, json={"name": "Stripe"})) as client:
        discovery.verify_candidate(
            conn, client, Candidate(origin="crawl", token="stripe", source="greenhouse"), report
        )

    assert (report.verified, report.requests) == (1, 1)
    row = conn.execute("SELECT * FROM companies WHERE token = 'stripe'").fetchone()
    assert row["status"] == "verified"
    assert row["name"] == "Stripe"  # from the board, not guessed
    assert row["discovered_by"] == "crawl"
    assert row["first_verified"] is not None


def test_a_dead_token_is_recorded_not_forgotten(conn: sqlite3.Connection) -> None:
    report = DiscoveryReport()
    with make_client(lambda _r: httpx.Response(404)) as client:
        discovery.verify_candidate(
            conn, client, Candidate(origin="crawl", token="gone", source="greenhouse"), report
        )

    assert report.dead == 1
    assert conn.execute("SELECT status FROM companies WHERE token='gone'").fetchone()[0] == "dead"


def test_a_known_candidate_is_not_rechecked(conn: sqlite3.Connection) -> None:
    calls = 0

    def handler(_r: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    candidates = [Candidate(origin="crawl", token="gone", source="greenhouse")]
    with make_client(handler) as client:
        first = run_discovery(conn, client, candidates)
        second = run_discovery(conn, client, candidates)

    assert first.checked == 1
    assert second.checked == 0
    assert second.skipped == 1
    # This is the point of the whole cache: one request, ever.
    assert calls == 1


def test_a_name_candidate_searches_slugs_across_boards(conn: sqlite3.Connection) -> None:
    # "Match Group" is on Lever as `matchgroup`, and the model guessed wrong
    # about which board — exactly the stale-ATS failure mode.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.lever.co" and "matchgroup" in str(request.url):
            return httpx.Response(200, json=[{"id": "1"}])
        return httpx.Response(404)

    report = DiscoveryReport()
    with make_client(handler) as client:
        discovery.verify_candidate(
            conn,
            client,
            Candidate(origin="llm", name="Match Group", source="greenhouse"),
            report,
        )

    assert report.verified == 1
    row = conn.execute("SELECT * FROM companies WHERE status='verified'").fetchone()
    assert (row["source"], row["token"]) == ("lever", "matchgroup")
    assert row["name"] == "Match Group"
    assert row["discovered_by"] == "llm"


def test_an_unfindable_name_is_recorded_as_unresolved(conn: sqlite3.Connection) -> None:
    report = DiscoveryReport()
    with make_client(lambda _r: httpx.Response(404)) as client:
        discovery.verify_candidate(
            conn, client, Candidate(origin="llm", name="Nonexistent Startup"), report
        )

    assert report.unresolved == 1
    statuses = {r["status"] for r in conn.execute("SELECT status FROM companies")}
    assert "unresolved" in statuses

    # And asking again costs nothing.
    second = DiscoveryReport()
    with make_client(lambda _r: httpx.Response(404)) as client:
        discovery.verify_candidate(
            conn, client, Candidate(origin="llm", name="Nonexistent Startup"), second
        )
    assert second.skipped == 1
    assert second.requests == 0


def test_run_discovery_respects_the_limit(conn: sqlite3.Connection) -> None:
    candidates = [Candidate(origin="crawl", token=f"t{i}", source="greenhouse") for i in range(10)]
    with make_client(lambda _r: httpx.Response(404)) as client:
        report = run_discovery(conn, client, candidates, limit=3)
    assert report.checked == 3


# --- the companies table ---------------------------------------------------


def test_seed_from_toml_is_idempotent(conn: sqlite3.Connection) -> None:
    entries = [
        CompanyEntry(source="greenhouse", token="stripe", name="Stripe"),
        CompanyEntry(source="lever", token="palantir", name="Palantir"),
    ]
    assert seed_from_toml(conn, entries) == 2
    assert seed_from_toml(conn, entries) == 0
    assert company_counts(conn) == {"verified": 2}


def test_load_verified_returns_ingest_entries(conn: sqlite3.Connection) -> None:
    record(conn, source="greenhouse", token="stripe", status="verified", name="Stripe")
    record(conn, source="lever", token="seb", status="verified", api_host="api.eu.lever.co")
    record(conn, source="ashby", token="gone", status="dead")

    entries = load_verified(conn)
    assert [(e.source, e.token) for e in entries] == [
        ("greenhouse", "stripe"),
        ("lever", "seb"),
    ]
    assert entries[1].api_host == "api.eu.lever.co"
    assert entries[1].name == "seb"  # falls back to the token

    assert [e.token for e in load_verified(conn, "greenhouse")] == ["stripe"]


def test_record_preserves_first_verified(conn: sqlite3.Connection) -> None:
    record(conn, source="greenhouse", token="acme", status="verified", name="Acme")
    first = conn.execute("SELECT first_verified FROM companies").fetchone()[0]

    record(conn, source="greenhouse", token="acme", status="verified", job_count=12)
    row = conn.execute("SELECT * FROM companies").fetchone()

    assert row["first_verified"] == first
    assert row["job_count"] == 12
    assert row["name"] == "Acme"  # not clobbered by the update


def test_crawl_candidates_parse_from_an_index_response() -> None:
    # The shape Common Crawl actually returns: one JSON object per line.
    lines = [
        json.dumps({"url": "https://boards.greenhouse.io/spacex/jobs/1"}),
        json.dumps({"url": "https://boards.greenhouse.io/cloudflare"}),
        json.dumps({"url": "https://boards.greenhouse.io/robots.txt"}),
        "not json",
    ]
    tokens = [token_from_url(json.loads(line)["url"]) for line in lines[:3]]
    assert tokens == ["spacex", "cloudflare", None]
