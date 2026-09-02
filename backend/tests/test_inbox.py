"""Phase 10: reading application replies out of Gmail.

Offline. Every Gmail response comes from an ``httpx.MockTransport`` and the
classifier is a stub, so OAuth refresh, paging, matching, idempotency and the
accept path are all exercised without a request or an API key.

The test that matters most is :func:`test_sync_changes_no_application`. The
whole design of this phase rests on suggestions never being applied
automatically, and a regression there is the one that would quietly cost the
author an interview.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_app.api.main import create_app
from agent_app.config import Settings
from agent_app.db import SUGGESTED_STATUS, now_iso
from agent_app.inbox import classify as classify_module
from agent_app.inbox import gmail, match, sync
from agent_app.inbox.classify import Classification, parse_response
from agent_app.inbox.gmail import EmailMessage, GmailClient, NotAuthorised, Token
from agent_app.inbox.match import OpenApplication, match_email
from agent_app.inbox.sync import (
    InboxError,
    accept_suggestion,
    build_query,
    dismiss_suggestion,
    list_suggestions,
    pending_count,
    sync_email,
)

# --- fixtures --------------------------------------------------------------


def add_posting(conn: sqlite3.Connection, posting_id: str, company: str, title: str) -> None:
    """Insert a minimal posting."""
    now = now_iso()
    with conn:
        conn.execute(
            "INSERT INTO postings (id, source, company, title, location, remote, url, body, "
            "body_hash, level, first_seen, last_seen) "
            "VALUES (?, 'greenhouse', ?, ?, 'Zurich', 0, 'https://example.test/j', 'body', "
            "'hash', 'intern', ?, ?)",
            (posting_id, company, title, now, now),
        )


def add_application(conn: sqlite3.Connection, posting_id: str, status: str = "applied") -> None:
    """Mark a posting as applied, the way the dashboard would."""
    now = now_iso()
    with conn:
        conn.execute(
            "INSERT INTO applications (posting_id, status, note, updated_at) VALUES (?, ?, '', ?)",
            (posting_id, status, now),
        )
        conn.execute(
            "INSERT INTO status_history (posting_id, from_status, to_status, note, changed_at) "
            "VALUES (?, NULL, ?, '', ?)",
            (posting_id, status, now),
        )


def gmail_message(
    message_id: str,
    *,
    sender: str = "careers@stripe.com",
    sender_name: str = "Stripe Recruiting",
    subject: str = "Your application",
    snippet: str = "Thank you for applying.",
    internal_date: int = 1_756_000_000_000,
) -> dict[str, Any]:
    """One Gmail ``messages.get?format=metadata`` response."""
    return {
        "id": message_id,
        "snippet": snippet,
        "internalDate": str(internal_date),
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": f"{sender_name} <{sender}>"},
                {"name": "Date", "value": "Mon, 24 Aug 2026 09:00:00 +0000"},
            ]
        },
    }


def make_client(messages: list[dict[str, Any]], *, token: Token | None = None) -> GmailClient:
    """A GmailClient whose every response comes from a mock transport."""
    by_id = {m["id"]: m for m in messages}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "at-1", "expires_in": 3600})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": m["id"]} for m in messages]})
        message_id = request.url.path.rsplit("/", 1)[-1]
        if message_id in by_id:
            return httpx.Response(200, json=by_id[message_id])
        return httpx.Response(404, json={"error": "not found"})

    return GmailClient(
        client_id="cid",
        client_secret="secret",
        token=token or Token(refresh_token="rt-1"),
        transport=httpx.MockTransport(handler),
    )


def always(label: str, confidence: float = 0.9):
    """A classifier stub that returns the same verdict for every email."""

    def _classify(_settings: Settings, _message: EmailMessage) -> Classification:
        return Classification(label, confidence, "stub")

    return _classify


# --- gmail: parsing, tokens, refresh ---------------------------------------


def test_parse_message_pulls_out_the_four_fields() -> None:
    message = gmail.parse_message(gmail_message("m1"))
    assert message.message_id == "m1"
    assert message.sender == "careers@stripe.com"
    assert message.sender_name == "Stripe Recruiting"
    assert message.subject == "Your application"
    assert message.snippet == "Thank you for applying."
    assert message.domain == "stripe.com"
    # internalDate wins over the Date header.
    assert message.received_at is not None
    assert message.received_at.endswith("Z")


def test_parse_message_falls_back_to_the_date_header() -> None:
    payload = gmail_message("m1")
    del payload["internalDate"]
    message = gmail.parse_message(payload)
    assert message.received_at == "2026-08-24T09:00:00Z"


def test_parse_message_survives_a_missing_from_header() -> None:
    payload = gmail_message("m1")
    payload["payload"]["headers"] = [{"name": "Subject", "value": "Hi"}]
    message = gmail.parse_message(payload)
    assert message.sender == ""
    assert message.domain == ""


def test_token_round_trips_through_disk(settings: Settings) -> None:
    path = settings.gmail_token_path
    gmail.save_token(path, Token(refresh_token="rt-9", access_token="at", expires_at=1.0))
    loaded = gmail.load_token(path)
    assert loaded is not None
    assert loaded.refresh_token == "rt-9"
    assert not loaded.fresh()  # expired long ago


def test_load_token_returns_none_when_absent(settings: Settings) -> None:
    assert gmail.load_token(settings.gmail_token_path) is None


def test_a_fresh_access_token_is_not_refreshed() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})

    token = Token(refresh_token="rt", access_token="cached", expires_at=time.time() + 9999)
    client = GmailClient("cid", None, token, transport=httpx.MockTransport(handler))
    assert client.access_token() == "cached"
    assert calls == []


def test_a_stale_access_token_is_refreshed_and_persisted(settings: Settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"access_token": "new", "expires_in": 3600, "refresh_token": "rotated"}
        )

    path = settings.gmail_token_path
    token = Token(refresh_token="old", access_token="stale", expires_at=0.0)
    client = GmailClient(
        "cid", None, token, token_path=path, transport=httpx.MockTransport(handler)
    )
    assert client.access_token() == "new"

    # A rotated refresh token must be written back, or the next run has to log in.
    stored = gmail.load_token(path)
    assert stored is not None
    assert stored.refresh_token == "rotated"


def test_a_revoked_token_is_reported_as_needing_login() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        return httpx.Response(401, json={"error": "invalid_credentials"})

    client = GmailClient(
        "cid", None, Token(refresh_token="rt"), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(NotAuthorised, match="--login"):
        client.search("after:2026/01/01")


def test_search_stops_at_the_limit() -> None:
    client = make_client([gmail_message(f"m{i}") for i in range(10)])
    assert len(client.search("q", limit=4)) == 4


def test_build_client_without_a_token_says_to_log_in(settings: Settings, monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    from agent_app.config import get_settings, reset_settings

    reset_settings()
    with pytest.raises(NotAuthorised, match="--login"):
        gmail.build_client(get_settings())


# --- matching --------------------------------------------------------------


APPS = [
    OpenApplication("greenhouse:1", "Stripe", "Software Engineer Intern"),
    OpenApplication("lever:2", "Figma", "Data Science Intern"),
]


def test_sender_domain_matches_the_company() -> None:
    result = match_email("stripe.com", "An update", "Stripe", APPS)
    assert result.posting_id == "greenhouse:1"
    assert result.company_guess == "Stripe"


def test_a_subdomain_still_matches() -> None:
    assert match_email("careers.stripe.com", "Hello", "", APPS).posting_id == "greenhouse:1"


def test_an_ats_relay_domain_is_never_matched_on() -> None:
    # The domain says Greenhouse, which is the vendor, not the employer. The
    # subject carries the real signal.
    result = match_email("no-reply@us.greenhouse-mail.io".split("@")[-1], "Figma update", "", APPS)
    assert result.posting_id == "lever:2"


def test_a_personal_gmail_address_is_not_a_company() -> None:
    assert match_email("gmail.com", "hello there", "A Friend", APPS).posting_id is None


def test_company_name_in_the_subject_matches() -> None:
    result = match_email("mail.recruiting-tool.test", "Your Figma application", "", APPS)
    assert result.posting_id == "lever:2"


def test_a_name_inside_a_longer_word_does_not_match() -> None:
    apps = [OpenApplication("greenhouse:9", "Ramp", "Intern")]
    assert match_email("x.test", "rampant inflation news", "", apps).posting_id is None


def test_two_applications_at_one_company_are_not_guessed_between() -> None:
    apps = [
        OpenApplication("greenhouse:1", "Stripe", "Backend Intern"),
        OpenApplication("greenhouse:2", "Stripe", "ML Intern"),
    ]
    result = match_email("stripe.com", "An update on your application", "", apps)
    assert result.posting_id is None
    # The company is still recorded, so the user can resolve it in one click.
    assert result.company_guess == "Stripe"


def test_the_role_in_the_subject_breaks_the_tie() -> None:
    apps = [
        OpenApplication("greenhouse:1", "Stripe", "Backend Intern"),
        OpenApplication("greenhouse:2", "Stripe", "ML Intern"),
    ]
    result = match_email("stripe.com", "Your ML Intern application", "", apps)
    assert result.posting_id == "greenhouse:2"


def test_no_applications_means_no_match() -> None:
    assert match_email("stripe.com", "hi", "", []).posting_id is None


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("stripe.com", "stripe"),
        ("careers.stripe.com", "stripe"),
        ("stripe.co.uk", "stripe"),
        ("mail.jobs.example.com.au", "example"),
        ("", ""),
    ],
)
def test_domain_root(domain: str, expected: str) -> None:
    assert match.domain_root(domain) == expected


# --- classification --------------------------------------------------------


def test_parse_response_reads_a_clean_verdict() -> None:
    result = parse_response('{"classification": "rejection", "confidence": 0.92, "reason": "no"}')
    assert result.label == "rejection"
    assert result.confidence == pytest.approx(0.92)
    assert result.suggested_status == "rejected"


def test_parse_response_handles_a_fenced_response() -> None:
    raw = 'Here you go:\n```json\n{"classification": "offer", "confidence": 0.8}\n```'
    assert parse_response(raw).label == "offer"


def test_an_unknown_label_degrades_to_other() -> None:
    result = parse_response('{"classification": "ghosted", "confidence": 0.99}')
    assert result.label == "other"
    assert result.suggested_status is None


def test_unparseable_output_degrades_to_other() -> None:
    assert parse_response("I could not tell, sorry").label == "other"


def test_a_low_confidence_verdict_is_downgraded() -> None:
    # 0.2 on a rejection is not something to put in front of the user as a
    # rejection; it becomes `other` with the number kept.
    result = parse_response('{"classification": "rejection", "confidence": 0.2}')
    assert result.label == "other"
    assert result.confidence == pytest.approx(0.2)


def test_classify_survives_a_model_that_raises(settings: Settings, monkeypatch) -> None:
    def boom(*_args: object) -> str:
        raise RuntimeError("network on fire")

    monkeypatch.setattr(classify_module, "call_model", boom)
    result = classify_module.classify(settings, gmail.parse_message(gmail_message("m1")))
    assert result.label == "other"
    assert result.confidence == 0.0


def test_every_classification_maps_to_a_status_or_none() -> None:
    assert SUGGESTED_STATUS["rejection"] == "rejected"
    assert SUGGESTED_STATUS["interview"] == "interviewing"
    assert SUGGESTED_STATUS["offer"] == "offer"
    assert SUGGESTED_STATUS["other"] is None


# --- sync ------------------------------------------------------------------


def test_sync_with_no_applications_does_nothing(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    report = sync_email(conn, settings, make_client([gmail_message("m1")]))
    assert report.skipped_reason is not None
    assert conn.execute("SELECT count(*) FROM email_matches").fetchone()[0] == 0


def test_sync_changes_no_application(conn: sqlite3.Connection, settings: Settings) -> None:
    """The property this whole phase exists to guarantee.

    PLAN.md's check for Phase 10: sync fetches, matches and classifies without
    changing a single ``applications`` row.
    """
    add_posting(conn, "greenhouse:1", "Stripe", "Software Engineer Intern")
    add_application(conn, "greenhouse:1", "applied")

    before_apps = conn.execute("SELECT * FROM applications").fetchall()
    before_history = conn.execute("SELECT count(*) FROM status_history").fetchone()[0]

    client = make_client(
        [
            gmail_message("m1", subject="Your Stripe application"),
            gmail_message("m2", subject="An update on your application"),
        ]
    )
    report = sync_email(conn, settings, client, classify_fn=always("rejection"))

    assert report.fetched == 2
    assert conn.execute("SELECT count(*) FROM email_matches").fetchone()[0] == 2

    after_apps = conn.execute("SELECT * FROM applications").fetchall()
    after_history = conn.execute("SELECT count(*) FROM status_history").fetchone()[0]
    assert [dict(r) for r in after_apps] == [dict(r) for r in before_apps]
    assert after_history == before_history


def test_sync_is_idempotent(conn: sqlite3.Connection, settings: Settings) -> None:
    add_posting(conn, "greenhouse:1", "Stripe", "Intern")
    add_application(conn, "greenhouse:1")
    client = make_client([gmail_message("m1")])

    first = sync_email(conn, settings, client, classify_fn=always("rejection"))
    second = sync_email(conn, settings, client, classify_fn=always("rejection"))

    assert first.fetched == 1
    assert second.fetched == 0
    assert second.already_seen == 1
    assert conn.execute("SELECT count(*) FROM email_matches").fetchone()[0] == 1


def test_an_unrecognised_sender_is_never_sent_to_the_model(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    add_posting(conn, "greenhouse:1", "Stripe", "Intern")
    add_application(conn, "greenhouse:1")

    def explode(*_args: object) -> Classification:
        raise AssertionError("the classifier should not have been called")

    client = make_client(
        [gmail_message("m1", sender="news@unrelated.test", sender_name="Unrelated", subject="Hi")]
    )
    report = sync_email(conn, settings, client, classify_fn=explode)

    assert report.unmatched == 1
    row = conn.execute("SELECT * FROM email_matches").fetchone()
    assert row["posting_id"] is None
    assert row["suggested_status"] is None


def test_an_unmatched_email_is_stored_rather_than_guessed(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    add_posting(conn, "greenhouse:1", "Stripe", "Backend Intern")
    add_posting(conn, "greenhouse:2", "Stripe", "ML Intern")
    add_application(conn, "greenhouse:1")
    add_application(conn, "greenhouse:2")

    client = make_client([gmail_message("m1", subject="An update on your application")])
    sync_email(conn, settings, client, classify_fn=always("rejection"))

    row = conn.execute("SELECT * FROM email_matches").fetchone()
    assert row["posting_id"] is None
    assert row["company_guess"] == "Stripe"
    assert row["suggested_status"] == "rejected"


def test_build_query_looks_back_before_the_first_application() -> None:
    query = build_query("2026-08-20T12:00:00Z", lookback_days=3)
    assert query.startswith("after:2026/08/17 ")
    assert "-in:chats" in query


# --- accepting and dismissing ----------------------------------------------


def prepared(conn: sqlite3.Connection, settings: Settings, label: str = "rejection") -> int:
    """One synced suggestion, matched to greenhouse:1. Returns its id."""
    add_posting(conn, "greenhouse:1", "Stripe", "Software Engineer Intern")
    add_application(conn, "greenhouse:1", "applied")
    client = make_client([gmail_message("m1", subject="Your Stripe application")])
    sync_email(conn, settings, client, classify_fn=always(label))
    return int(conn.execute("SELECT id FROM email_matches").fetchone()["id"])


def test_accepting_moves_the_status_and_names_the_email(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    match_id = prepared(conn, settings)
    result = accept_suggestion(conn, match_id)

    assert result["status"] == "rejected"
    assert result["from_status"] == "applied"

    application = conn.execute("SELECT * FROM applications").fetchone()
    assert application["status"] == "rejected"

    history = conn.execute("SELECT * FROM status_history ORDER BY id DESC LIMIT 1").fetchone()
    assert history["from_status"] == "applied"
    assert history["to_status"] == "rejected"
    # The note has to name the email, or an automated mistake is untraceable.
    assert "gmail:m1" in history["note"]
    assert "Your Stripe application" in history["note"]

    assert conn.execute("SELECT applied FROM email_matches").fetchone()["applied"] == 1


def test_a_suggestion_cannot_be_accepted_twice(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    match_id = prepared(conn, settings)
    accept_suggestion(conn, match_id)
    with pytest.raises(InboxError, match="already been accepted"):
        accept_suggestion(conn, match_id)


def test_accepting_an_other_classification_needs_an_explicit_status(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    match_id = prepared(conn, settings, label="other")
    with pytest.raises(InboxError, match="suggests no status change"):
        accept_suggestion(conn, match_id)

    result = accept_suggestion(conn, match_id, status="interviewing")
    assert result["status"] == "interviewing"


def test_accepting_an_unmatched_suggestion_needs_a_posting(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    add_posting(conn, "greenhouse:1", "Stripe", "Backend Intern")
    add_posting(conn, "greenhouse:2", "Stripe", "ML Intern")
    add_application(conn, "greenhouse:1")
    add_application(conn, "greenhouse:2")
    client = make_client([gmail_message("m1", subject="An update")])
    sync_email(conn, settings, client, classify_fn=always("interview"))
    match_id = int(conn.execute("SELECT id FROM email_matches").fetchone()["id"])

    with pytest.raises(InboxError, match="not matched to a posting"):
        accept_suggestion(conn, match_id)

    result = accept_suggestion(conn, match_id, posting_id="greenhouse:2")
    assert result["posting_id"] == "greenhouse:2"
    assert result["status"] == "interviewing"


def test_an_unknown_status_is_refused(conn: sqlite3.Connection, settings: Settings) -> None:
    match_id = prepared(conn, settings)
    with pytest.raises(ValueError, match="Unknown status"):
        accept_suggestion(conn, match_id, status="pending")


def test_dismissing_writes_nothing_but_the_flag(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    match_id = prepared(conn, settings)
    before = conn.execute("SELECT status FROM applications").fetchone()["status"]

    dismiss_suggestion(conn, match_id)

    assert conn.execute("SELECT status FROM applications").fetchone()["status"] == before
    assert conn.execute("SELECT dismissed FROM email_matches").fetchone()["dismissed"] == 1
    assert list_suggestions(conn, pending_only=True) == []
    assert pending_count(conn) == 0


def test_a_dismissed_suggestion_is_not_offered_again(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    match_id = prepared(conn, settings)
    dismiss_suggestion(conn, match_id)

    client = make_client([gmail_message("m1", subject="Your Stripe application")])
    sync_email(conn, settings, client, classify_fn=always("rejection"))

    assert conn.execute("SELECT count(*) FROM email_matches").fetchone()[0] == 1
    assert conn.execute("SELECT dismissed FROM email_matches").fetchone()["dismissed"] == 1


def test_pending_count_ignores_other_classifications(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    prepared(conn, settings, label="other")
    assert pending_count(conn) == 0


# --- the API ---------------------------------------------------------------


@pytest.fixture
def api() -> TestClient:
    return TestClient(create_app())


def test_inbox_route_lists_pending_suggestions(
    api: TestClient, conn: sqlite3.Connection, settings: Settings
) -> None:
    prepared(conn, settings)
    response = api.get("/api/inbox")
    assert response.status_code == 200
    body = response.json()
    assert body["pending"] == 1
    assert body["items"][0]["subject"] == "Your Stripe application"
    assert body["items"][0]["company"] == "Stripe"
    assert body["items"][0]["suggested_status"] == "rejected"


def test_accept_route_moves_the_status(
    api: TestClient, conn: sqlite3.Connection, settings: Settings
) -> None:
    match_id = prepared(conn, settings)

    response = api.post(f"/api/inbox/{match_id}/accept", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    detail = api.get("/api/postings/greenhouse:1").json()
    assert detail["status"] == "rejected"
    assert any("gmail:m1" in change["note"] for change in detail["history"])

    # And it leaves the queue.
    assert api.get("/api/inbox").json()["pending"] == 0


def test_accept_route_reports_a_missing_suggestion(api: TestClient) -> None:
    assert api.post("/api/inbox/999/accept", json={}).status_code == 404


def test_accept_route_409s_when_it_needs_the_user(
    api: TestClient, conn: sqlite3.Connection, settings: Settings
) -> None:
    match_id = prepared(conn, settings, label="other")
    response = api.post(f"/api/inbox/{match_id}/accept", json={})
    assert response.status_code == 409
    assert "no status change" in response.json()["detail"]


def test_dismiss_route(api: TestClient, conn: sqlite3.Connection, settings: Settings) -> None:
    match_id = prepared(conn, settings)
    response = api.post(f"/api/inbox/{match_id}/dismiss")
    assert response.status_code == 200
    assert response.json()["dismissed"] is True
    assert api.get("/api/inbox").json()["pending"] == 0


def test_inbox_is_empty_before_any_sync(api: TestClient) -> None:
    body = api.get("/api/inbox").json()
    assert body == {"items": [], "pending": 0}


# --- the schema ------------------------------------------------------------


def test_a_row_cannot_be_both_accepted_and_dismissed(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(
            "INSERT INTO email_matches (message_id, applied, dismissed, created_at) "
            "VALUES ('m1', 0, 0, ?)",
            (now_iso(),),
        )
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("UPDATE email_matches SET applied = 1, dismissed = 1")


def test_the_same_message_cannot_be_recorded_twice(conn: sqlite3.Connection) -> None:
    row = ("m1", now_iso())
    with conn:
        conn.execute("INSERT INTO email_matches (message_id, created_at) VALUES (?, ?)", row)
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("INSERT INTO email_matches (message_id, created_at) VALUES (?, ?)", row)


def test_gmail_scope_is_read_only() -> None:
    """A scope creep here would be the most consequential bug in the project."""
    assert gmail.SCOPE == "https://www.googleapis.com/auth/gmail.readonly"
    assert "readonly" in gmail.SCOPE
    source = (gmail.__file__ or "").replace("gmail.py", "gmail.py")
    text = open(source, encoding="utf-8").read()
    for forbidden in ("gmail.send", "gmail.modify", "gmail.compose", "mail.google.com"):
        assert forbidden not in text


def test_metadata_request_never_asks_for_the_body() -> None:
    """`format=metadata` is what makes "we never store a body" structurally true."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        seen.append(request)
        return httpx.Response(200, json=gmail_message("m1"))

    client = GmailClient(
        "cid", None, Token(refresh_token="rt"), transport=httpx.MockTransport(handler)
    )
    client.metadata("m1")

    assert seen[0].url.params["format"] == "metadata"
    assert "full" not in str(seen[0].url)


def test_sync_module_never_writes_to_applications() -> None:
    """A structural check to go with the behavioural one.

    ``sync_email`` and ``record_match`` must not contain a write to
    ``applications`` or ``status_history``; only ``accept_suggestion`` may.
    """
    text = open(sync.__file__ or "", encoding="utf-8").read()
    body = text[text.index("def sync_email") : text.index("def list_suggestions")]
    lowered = body.lower()
    assert "insert into applications" not in lowered
    assert "update applications" not in lowered
    assert "insert into status_history" not in lowered


def test_json_shape_of_a_stored_suggestion(conn: sqlite3.Connection, settings: Settings) -> None:
    """Everything the dashboard needs is on the row, and no body is."""
    prepared(conn, settings)
    row = dict(conn.execute("SELECT * FROM email_matches").fetchone())
    assert set(row) == {
        "id",
        "message_id",
        "posting_id",
        "company_guess",
        "sender",
        "received_at",
        "subject",
        "snippet",
        "classification",
        "confidence",
        "suggested_status",
        "applied",
        "dismissed",
        "created_at",
    }
    assert json.dumps(row)  # serialisable as-is


# --- narrowing the queue ---------------------------------------------------


def _suggestion(
    conn: sqlite3.Connection,
    message_id: str,
    classification: str,
    confidence: float,
    status: str | None = "rejected",
) -> None:
    conn.execute(
        "INSERT INTO email_matches (message_id, received_at, subject, sender, snippet, "
        "classification, confidence, suggested_status, created_at) "
        "VALUES (?, '2026-09-01T00:00:00Z', 's', 'a@b.c', '', ?, ?, ?, '2026-09-01T00:00:00Z')",
        (message_id, classification, confidence, status),
    )
    conn.commit()


def test_min_confidence_drops_the_unsure_ones(conn: sqlite3.Connection) -> None:
    _suggestion(conn, "m1", "rejection", 0.95)
    _suggestion(conn, "m2", "rejection", 0.40)

    kept = list_suggestions(conn, min_confidence=0.7)
    assert [row["message_id"] for row in kept] == ["m1"]
    assert len(list_suggestions(conn)) == 2


def test_a_null_confidence_never_passes_a_threshold(conn: sqlite3.Connection) -> None:
    _suggestion(conn, "m1", "other", 0.9, status=None)
    conn.execute("UPDATE email_matches SET confidence = NULL WHERE message_id = 'm1'")
    conn.commit()

    assert list_suggestions(conn, min_confidence=0.1) == []


def test_classification_narrows_to_one_kind(conn: sqlite3.Connection) -> None:
    _suggestion(conn, "m1", "rejection", 0.9)
    _suggestion(conn, "m2", "interview", 0.9, status="interviewing")

    kept = list_suggestions(conn, classification="rejection")
    assert [row["message_id"] for row in kept] == ["m1"]


def test_the_filters_default_to_showing_everything(conn: sqlite3.Connection) -> None:
    """A queue that hides what the classifier read cannot be learned from."""
    _suggestion(conn, "m1", "other", 0.05, status=None)
    assert len(list_suggestions(conn)) == 1
