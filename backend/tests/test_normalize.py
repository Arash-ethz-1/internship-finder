"""Phase 2: the pure normalisation rules, checked without a network or a DB."""

from __future__ import annotations

import pytest

from agent_app.ingest.normalize import (
    body_hash,
    build_posting,
    infer_level,
    infer_remote,
    iso_from_epoch_ms,
    iso_from_string,
    strip_html,
)


def test_strip_html_unescapes_greenhouse_double_encoding() -> None:
    raw = "&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;"
    assert strip_html(raw) == "Hello & welcome"


def test_strip_html_keeps_list_shape() -> None:
    text = strip_html("<p>Requirements</p><ul><li>Python</li><li>Go</li></ul>")
    assert text == "Requirements\n\n- Python\n- Go"


def test_strip_html_collapses_whitespace_and_blank_lines() -> None:
    assert strip_html("<p>a</p><p></p><p></p><p>b   c</p>") == "a\n\nb c"


def test_strip_html_handles_empty_input() -> None:
    assert strip_html(None) == ""
    assert strip_html("") == ""


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Software Engineering Intern", "intern"),
        ("Summer 2027 Internship, Backend", "intern"),
        ("Praktikum Datenanalyse", "intern"),
        ("Working Student, Machine Learning", "intern"),
        ("Master Thesis: Graph Networks", "intern"),
        ("New Grad Software Engineer", "newgrad"),
        ("Entry-level Analyst", "newgrad"),
        ("Staff Engineer", "unknown"),
    ],
)
def test_infer_level_from_title(title: str, expected: str) -> None:
    assert infer_level(title, "") == expected


def test_infer_level_does_not_match_internal_or_international() -> None:
    # The bug this guards against: "intern" as a substring of another word.
    assert infer_level("Internal Tools Engineer", "") == "unknown"
    assert infer_level("International Payments Lead", "") == "unknown"


@pytest.mark.parametrize(
    "body",
    [
        "As an intern you will ship real code.",
        "This internship runs for twelve weeks.",
        "Applications for our Summer 2027 internship are open.",
        "Wir suchen einen Praktikanten.",
        "This is a working student position in Munich.",
        "You will write your master's thesis with us.",
    ],
)
def test_infer_level_accepts_a_body_that_asserts_the_role(body: str) -> None:
    assert infer_level("Software Engineer", body) == "intern"


@pytest.mark.parametrize(
    "body",
    [
        # The exact disclaimer that mislabelled senior Stripe roles as internships.
        "Note: if you are an intern, new grad, or staff applicant, please do not"
        " apply using this link and visit our jobs page instead.",
        "You will mentor our interns and new graduates.",
        "Own the internal tooling platform.",
        "We work with international teams across nine offices.",
        "Our internship alumni often return as full-time engineers.",
    ],
)
def test_infer_level_ignores_a_body_that_merely_mentions_interns(body: str) -> None:
    # A body is long and mentions everything; only the title, or a phrase
    # asserting *this* role, is allowed to set the level.
    assert infer_level("Senior Backend Engineer", body) == "unknown"


def test_infer_level_still_prefers_the_title() -> None:
    body = "You will mentor our interns."
    assert infer_level("New Grad Software Engineer", body) == "newgrad"


def test_infer_level_defaults_to_unknown_rather_than_guessing() -> None:
    assert infer_level("Product Designer", "Design the console.") == "unknown"


@pytest.mark.parametrize(
    ("location", "explicit", "expected"),
    [
        ("Remote - Europe", None, True),
        ("Anywhere", None, True),
        ("Berlin, Germany", None, False),
        (None, None, False),
        ("Berlin, Germany", True, True),
        ("Remote - Europe", False, False),
        ("Non-remote office role", None, False),
    ],
)
def test_infer_remote(location: str | None, explicit: bool | None, expected: bool) -> None:
    assert infer_remote(location, explicit) is expected


def test_iso_from_string_normalises_offsets_to_utc() -> None:
    assert iso_from_string("2026-07-14T09:12:00-04:00") == "2026-07-14T13:12:00Z"
    assert iso_from_string("2026-06-02T11:00:00Z") == "2026-06-02T11:00:00Z"
    assert iso_from_string("2026-08-01T10:00:00.000Z") == "2026-08-01T10:00:00Z"


def test_iso_from_string_rejects_junk() -> None:
    assert iso_from_string(None) is None
    assert iso_from_string("") is None
    assert iso_from_string("last tuesday") is None


def test_iso_from_epoch_ms() -> None:
    assert iso_from_epoch_ms(1782000000000) == "2026-06-21T00:00:00Z"
    assert iso_from_epoch_ms(None) is None
    assert iso_from_epoch_ms(0) is None
    assert iso_from_epoch_ms("nonsense") is None


def test_body_hash_tracks_content_not_identity() -> None:
    assert body_hash("same text") == body_hash("same text")
    assert body_hash("one") != body_hash("two")


def test_build_posting_drops_records_missing_essentials() -> None:
    complete = {
        "source": "greenhouse",
        "external_id": "1",
        "company": "Acme",
        "title": "Intern",
        "location": "Remote",
        "url": "https://example.com/1",
        "body": "body",
        "posted_at": None,
    }
    assert build_posting(**complete) is not None
    assert build_posting(**{**complete, "external_id": ""}) is None
    assert build_posting(**{**complete, "title": "  "}) is None
    assert build_posting(**{**complete, "url": ""}) is None


def test_build_posting_composes_the_primary_key() -> None:
    posting = build_posting(
        source="lever",
        external_id="abc",
        company="Acme",
        title="Intern",
        location=None,
        url="https://example.com/abc",
        body="body",
        posted_at=None,
    )
    assert posting is not None
    assert posting.id == "lever:abc"
