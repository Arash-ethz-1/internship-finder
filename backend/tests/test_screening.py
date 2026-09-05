"""The screen: reading a result list back before anyone is shown it.

Two properties carry the whole feature. A row the screen removed must not
reach the result list, and it must still be there tomorrow -- a screened-out
posting is never recorded as ``found``, so the next search offers it again.
The second is the one worth guarding: a filter that quietly buries jobs is
worse than no filter, and its cost is invisible by construction.

Everything else here is about failing safe. Every way the model can misbehave
-- no key, a timeout, prose instead of JSON, an index for a row it was never
shown -- has to end with the person seeing the unscreened list rather than an
error or a silently shortened one.
"""

from __future__ import annotations

import sqlite3

import pytest

from agent_app.config import reset_settings
from agent_app.core import retrieval, screen, tools
from agent_app.db import now_iso
from agent_app.ingest.chunks import chunk_pending_postings


def add_posting(
    conn: sqlite3.Connection,
    posting_id: str,
    company: str,
    title: str,
    body: str = "",
) -> None:
    now = now_iso()
    conn.execute(
        "INSERT INTO postings (id, source, company, title, location, remote, url, body, "
        "body_hash, level, first_seen, last_seen) "
        "VALUES (?, 'greenhouse', ?, ?, 'Zurich', 0, 'https://x', ?, ?, 'intern', ?, ?)",
        (
            posting_id,
            company,
            title,
            body or f"{title} at {company}.\n\nA second paragraph.",
            f"hash-{posting_id}",
            now,
            now,
        ),
    )
    conn.commit()


def fake_search_over(conn: sqlite3.Connection):
    """Return every undecided chunk in insertion order, so ranking is not what is tested.

    The status filter is the one part of the real query worth reproducing:
    these tests are about what a *second* search offers, and `undecided`
    deliberately includes `found`, so a posting an earlier search surfaced and
    nobody judged comes back.
    """

    def search(query, filters, k=10):
        assert filters.status == "undecided"
        rows = conn.execute(
            "SELECT c.id, c.posting_id, c.text FROM chunks c "
            "LEFT JOIN applications a ON a.posting_id = c.posting_id "
            "WHERE a.posting_id IS NULL OR a.status = 'found' ORDER BY c.id"
        ).fetchall()
        return [
            retrieval.SearchHit(
                chunk_id=r["id"],
                posting_id=r["posting_id"],
                profile_doc=None,
                ordinal=0,
                text=r["text"],
                score=0.03,
                rank=i + 1,
                component_scores={"dense": 0.02, "bm25": 0.01},
            )
            for i, r in enumerate(rows)
        ]

    return search


@pytest.fixture
def with_key(monkeypatch: pytest.MonkeyPatch):
    """A configured key, so the screen actually runs. The call itself is faked."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    reset_settings()
    yield
    reset_settings()


#
# Parsing. Every malformed answer has to fall towards keeping a posting.


def test_parse_reads_the_drop_list() -> None:
    verdict = screen.parse_response('{"drop": [{"n": 2, "why": "quant trading desk"}]}', 5)
    assert verdict.ran and verdict.dropped == {1: "quant trading desk"}


def test_parse_keeps_everything_when_the_drop_list_is_empty() -> None:
    verdict = screen.parse_response('{"drop": []}', 5)
    # Ran and found nothing wrong is a real answer, and a different one from
    # never having run. The trace says "screened 40" only in the first case.
    assert verdict.ran and verdict.dropped == {}


def test_parse_treats_prose_as_a_screen_that_did_not_happen() -> None:
    verdict = screen.parse_response("I looked at the list and it seems fine!", 5)
    assert not verdict.ran and verdict.dropped == {}


def test_parse_ignores_a_row_the_model_was_never_shown() -> None:
    # An index past the end is a hallucinated row. Dropping the modulo of it,
    # or the last real one, would remove a posting nobody judged.
    verdict = screen.parse_response('{"drop": [{"n": 99, "why": "x"}, {"n": 1, "why": "y"}]}', 3)
    assert verdict.dropped == {0: "y"}


def test_parse_survives_junk_inside_the_drop_list() -> None:
    raw = '{"drop": ["nonsense", {"why": "no index"}, {"n": "two", "why": "text"}]}'
    assert screen.parse_response(raw, 5).dropped == {}


def test_parse_gives_a_reasonless_drop_something_to_show() -> None:
    # The reason is rendered in the trace, so an empty one would print a row
    # that was removed for no stated cause.
    assert screen.parse_response('{"drop": [{"n": 1}]}', 3).dropped == {0: "off-target"}


#
# Failing safe. None of these may raise, and none may drop anything.


def test_screen_does_not_run_without_a_key() -> None:
    verdict = screen.screen("ml research", [{"company": "Acme", "title": "ML Intern"}])
    assert not verdict.ran


def test_screen_can_be_switched_off(monkeypatch: pytest.MonkeyPatch, with_key) -> None:
    monkeypatch.setenv("SCREEN_RESULTS", "0")
    reset_settings()

    def explode(settings, prompt):
        raise AssertionError("the screen must not call the model when it is off")

    monkeypatch.setattr(screen, "call_model", explode)
    assert not screen.screen("ml research", [{"company": "Acme", "title": "ML Intern"}]).ran


def test_screen_degrades_when_the_model_call_fails(
    monkeypatch: pytest.MonkeyPatch, with_key
) -> None:
    def explode(settings, prompt):
        raise TimeoutError("the screening model timed out")

    monkeypatch.setattr(screen, "call_model", explode)
    # A search that returns a slightly noisy list is useful. A search that
    # raises because a secondary model was slow is not.
    assert not screen.screen("ml research", [{"company": "Acme", "title": "ML Intern"}]).ran


def test_the_prompt_carries_titles_and_excerpts(with_key) -> None:
    rendered = screen.render_candidates(
        [
            {"company": "Jane Street", "title": "Quant Research Intern", "excerpt": "x" * 500},
            {"company": "Acme", "title": "ML Intern"},
        ]
    )
    assert rendered.startswith("1. Jane Street - Quant Research Intern")
    assert "2. Acme - ML Intern" in rendered
    # Excerpts are the matching chunk, not a summary, so a long one is mostly
    # boilerplate and pays for itself only up to a point.
    assert "x" * screen.EXCERPT_CHARS in rendered
    assert "x" * (screen.EXCERPT_CHARS + 1) not in rendered


#
# The whole path, through `find_postings`.


def test_a_screened_out_posting_leaves_the_result_list(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, with_key
) -> None:
    add_posting(conn, "greenhouse:1", "DeepMind", "Machine Learning Research Intern")
    add_posting(conn, "greenhouse:2", "Jane Street", "Quantitative Research Intern")
    chunk_pending_postings(conn)
    monkeypatch.setattr(retrieval, "search", fake_search_over(conn))
    monkeypatch.setattr(
        screen,
        "call_model",
        lambda settings, prompt: '{"drop": [{"n": 2, "why": "quant trading, not ML"}]}',
    )

    out = tools.find_postings("machine learning research internship", limit=30)

    results = [row for row in out if not row.get("screened_out")]
    dropped = [row for row in out if row.get("screened_out")]
    assert [r["posting_id"] for r in results] == ["greenhouse:1"]
    assert [d["posting_id"] for d in dropped] == ["greenhouse:2"]
    assert dropped[0]["screen_reason"] == "quant trading, not ML"
    # Ranks are the list the person sees, so they close up over the gap
    # rather than recording where the removed row used to be.
    assert results[0]["rank"] == 1


def test_a_screened_out_posting_is_not_gone_forever(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, with_key
) -> None:
    add_posting(conn, "greenhouse:1", "DeepMind", "Machine Learning Research Intern")
    add_posting(conn, "greenhouse:2", "Jane Street", "Quantitative Research Intern")
    chunk_pending_postings(conn)
    monkeypatch.setattr(retrieval, "search", fake_search_over(conn))
    monkeypatch.setattr(
        screen,
        "call_model",
        lambda settings, prompt: '{"drop": [{"n": 2, "why": "quant trading, not ML"}]}',
    )

    tools.find_postings("machine learning research internship", limit=30)

    # `found` is what makes a search stop offering a posting. Writing it for a
    # row the screen removed would bury the job for good on the word of one
    # cheap model reading a title -- which is exactly what must not happen.
    statuses = dict(conn.execute("SELECT posting_id, status FROM applications"))
    assert statuses == {"greenhouse:1": "found"}

    # And the proof of that: with the screen quiet, the same posting is still
    # there to be found. (So is greenhouse:1, because `found` is not a
    # decision either -- walking past a result does not settle it.)
    monkeypatch.setattr(screen, "call_model", lambda settings, prompt: '{"drop": []}')
    again = [row["posting_id"] for row in tools.find_postings("quant research", limit=30)]
    assert "greenhouse:2" in again


def test_screened_out_rows_cannot_be_mistaken_for_results(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, with_key
) -> None:
    add_posting(conn, "greenhouse:1", "DeepMind", "Machine Learning Research Intern")
    add_posting(conn, "greenhouse:2", "Jane Street", "Quantitative Research Intern")
    chunk_pending_postings(conn)
    monkeypatch.setattr(retrieval, "search", fake_search_over(conn))
    monkeypatch.setattr(
        screen,
        "call_model",
        lambda settings, prompt: '{"drop": [{"n": 2, "why": "quant trading, not ML"}]}',
    )

    dropped = [
        row
        for row in tools.find_postings("machine learning research internship", limit=30)
        if row.get("screened_out")
    ]

    # The frontend picks its result-list renderer off `excerpt`, and its score
    # bars off `component_scores`. A screened-out row carrying either would
    # arrive in the list of postings to triage in bulk.
    assert "excerpt" not in dropped[0]
    assert "component_scores" not in dropped[0]
    assert "status" not in dropped[0]


def test_the_screen_sees_every_phrasing(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, with_key
) -> None:
    add_posting(conn, "greenhouse:1", "DeepMind", "Machine Learning Research Intern")
    chunk_pending_postings(conn)
    monkeypatch.setattr(retrieval, "search", fake_search_over(conn))
    seen: list[str] = []

    def capture(settings, prompt):
        seen.append(prompt)
        return '{"drop": []}'

    monkeypatch.setattr(screen, "call_model", capture)
    tools.find_postings(
        "machine learning research internship",
        queries=["deep learning PhD internship"],
        limit=30,
    )

    # Each alternate is part of how this search read the request. Screening
    # against only the first one judges results the other phrasings earned.
    assert len(seen) == 1
    assert "machine learning research internship" in seen[0]
    assert "deep learning PhD internship" in seen[0]


def test_with_the_screen_off_the_list_is_what_it_always_was(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCREEN_RESULTS", "0")
    reset_settings()
    add_posting(conn, "greenhouse:1", "DeepMind", "Machine Learning Research Intern")
    add_posting(conn, "greenhouse:2", "Jane Street", "Quantitative Research Intern")
    chunk_pending_postings(conn)
    monkeypatch.setattr(retrieval, "search", fake_search_over(conn))

    out = tools.find_postings("machine learning research internship", limit=30)

    assert [row["posting_id"] for row in out] == ["greenhouse:1", "greenhouse:2"]
    assert all("screened_out" not in row for row in out)
    statuses = dict(conn.execute("SELECT posting_id, status FROM applications"))
    assert statuses == {"greenhouse:1": "found", "greenhouse:2": "found"}
