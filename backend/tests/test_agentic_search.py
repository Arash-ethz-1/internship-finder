"""Phase 11: the loop that searches more than once.

Four things are worth holding still here. Fusing several phrasings must not
break the arithmetic the retrieval trace is drawn from; counting must agree
with searching about what a filter means; a decision must stay distinguishable
from a search result; and the search budget must be a real ceiling that still
ends the turn cleanly.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from agent_app import runtime
from agent_app.config import reset_settings
from agent_app.core import retrieval, tools
from agent_app.core.retrieval import SearchFilters
from agent_app.db import now_iso
from agent_app.ingest.chunks import chunk_pending_postings


def add_posting(
    conn: sqlite3.Connection,
    posting_id: str,
    *,
    title: str = "ML Intern",
    company: str = "Acme",
    location: str = "Zurich",
    level: str = "intern",
    body: str = "A paragraph about the role.\n\nAnd a second one.",
) -> None:
    now = now_iso()
    conn.execute(
        "INSERT INTO postings (id, source, company, title, location, remote, url, body, "
        "body_hash, level, first_seen, last_seen) "
        "VALUES (?, 'greenhouse', ?, ?, ?, 0, 'https://x', ?, 'hash', ?, ?, ?)",
        (posting_id, company, title, location, body, level, now, now),
    )
    conn.commit()


# --- fusing several phrasings ----------------------------------------------


def test_search_many_with_one_phrasing_is_plain_search(conn: sqlite3.Connection) -> None:
    """The single-query path must not change. It is the whole existing app."""
    calls: list[str] = []

    def fake_search(query: str, filters: SearchFilters, k: int = 10) -> list[Any]:
        calls.append(query)
        return []

    original = retrieval.search
    retrieval.search = fake_search  # type: ignore[assignment]
    try:
        retrieval.search_many(["one phrasing"], SearchFilters(), 5)
        # Whitespace-only and empty alternates are not phrasings.
        retrieval.search_many(["one phrasing", "  ", ""], SearchFilters(), 5)
        # Nor is a duplicate: two identical strings are one ranking, not two.
        retrieval.search_many(["dup", "dup"], SearchFilters(), 5)
    finally:
        retrieval.search = original  # type: ignore[assignment]

    assert calls == ["one phrasing", "one phrasing", "dup"]


def test_search_many_keeps_the_component_scores_honest(conn: sqlite3.Connection) -> None:
    """``dense + bm25 == score`` however many phrasings were fused.

    The stacked bar in the retrieval trace is drawn straight from these two
    numbers. If fusing six queries produced six components, or components that
    no longer summed to the score, the bar would quietly become decoration.
    """
    for i in range(5):
        add_posting(conn, f"greenhouse:{i}", title=f"Machine Learning Intern {i}")
    chunk_pending_postings(conn)

    hits = retrieval.search_many(
        ["machine learning intern", "deep learning internship", "ML research role"],
        SearchFilters(),
        5,
    )
    assert hits, "the fixture postings should be retrievable"
    for hit in hits:
        assert set(hit.component_scores) == {retrieval.COMPONENT_DENSE, retrieval.COMPONENT_BM25}
        assert sum(hit.component_scores.values()) == pytest.approx(hit.score)
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))


def test_search_many_caps_the_number_of_phrasings(conn: sqlite3.Connection) -> None:
    """A model that hands over twenty phrasings does not get twenty scans."""
    add_posting(conn, "greenhouse:1")
    chunk_pending_postings(conn)

    seen: list[list[str]] = []
    original = runtime.get_provider

    def recording_provider() -> Any:
        provider = original()

        class Recorder:
            dim = provider.dim

            def embed(self, texts: list[str]) -> Any:
                seen.append(list(texts))
                return provider.embed(texts)

        return Recorder()

    runtime.get_provider = recording_provider  # type: ignore[assignment]
    try:
        retrieval.search_many([f"phrasing number {i}" for i in range(20)], SearchFilters(), 3)
    finally:
        runtime.get_provider = original  # type: ignore[assignment]

    assert seen and len(seen[0]) == retrieval.MAX_FUSED_QUERIES
    assert len(seen) == 1, "every phrasing is embedded in one batched request, not one each"


def test_find_postings_forwards_every_phrasing(conn: sqlite3.Connection) -> None:
    add_posting(conn, "greenhouse:1")
    chunk_pending_postings(conn)

    passed: list[list[str]] = []

    def fake_search_many(queries: list[str], filters: SearchFilters, k: int = 10) -> list[Any]:
        passed.append(list(queries))
        return []

    original = retrieval.search_many
    retrieval.search_many = fake_search_many  # type: ignore[assignment]
    try:
        tools.find_postings("primary", queries=["second", "third"])
    finally:
        retrieval.search_many = original  # type: ignore[assignment]

    assert passed == [["primary", "second", "third"]]


# --- counting agrees with searching ----------------------------------------


def test_corpus_stats_counts_what_search_can_reach(conn: sqlite3.Connection) -> None:
    add_posting(conn, "greenhouse:1", company="Acme", location="Zurich")
    add_posting(conn, "greenhouse:2", company="Acme", location="Zurich")
    add_posting(conn, "greenhouse:3", company="Globex", location="Berlin")
    add_posting(conn, "greenhouse:4", company="Globex", location="Berlin", level="newgrad")
    add_posting(conn, "greenhouse:5", company="Hooli", location="Zurich")
    chunk_pending_postings(conn)
    # No chunks, so no search can ever return it, so it is not in the ceiling.
    conn.execute("DELETE FROM chunks WHERE posting_id = 'greenhouse:5'")
    conn.commit()

    stats = tools.corpus_stats(level="intern")
    assert stats["postings"] == 3
    assert stats["companies"] == 2
    assert stats["undecided"] == 3
    assert {c["name"]: c["postings"] for c in stats["top_companies"]} == {"Acme": 2, "Globex": 1}

    zurich = tools.corpus_stats(level="intern", location="Zurich")
    assert zurich["postings"] == 2, "greenhouse:5 has no chunks and is unreachable"


def test_corpus_stats_undecided_shrinks_as_you_triage(conn: sqlite3.Connection) -> None:
    """The number that says whether a search has anything left to offer."""
    add_posting(conn, "greenhouse:1")
    add_posting(conn, "greenhouse:2")
    chunk_pending_postings(conn)

    assert tools.corpus_stats()["undecided"] == 2
    tools.update_status("greenhouse:1", "not_relevant")
    assert tools.corpus_stats()["undecided"] == 1
    assert tools.corpus_stats()["postings"] == 2, "triage does not remove it from the corpus"


def test_corpus_stats_ignores_a_stray_query(conn: sqlite3.Connection) -> None:
    """Observed live: the model carries find_postings' argument shape across.

    Erroring cost a whole round trip and taught the model nothing, because a
    count over constraints has no use for query text either way.
    """
    add_posting(conn, "greenhouse:1")
    chunk_pending_postings(conn)
    assert tools.corpus_stats(query="anything at all")["postings"] == 1


# --- a decision is not a search result -------------------------------------


def test_past_decisions_never_returns_found(conn: sqlite3.Connection) -> None:
    add_posting(conn, "greenhouse:1")
    add_posting(conn, "greenhouse:2")
    chunk_pending_postings(conn)

    tools.update_status("greenhouse:1", "found", "found by search: ml")
    tools.update_status("greenhouse:2", "not_relevant", "quant, not for me")

    decisions = tools.past_decisions()
    assert [d["posting_id"] for d in decisions] == ["greenhouse:2"]
    assert decisions[0]["status"] == "not_relevant"
    assert decisions[0]["note"] == "quant, not for me"

    with pytest.raises(ValueError, match="not a decision"):
        tools.past_decisions(status="found")
    with pytest.raises(ValueError, match="Unknown status"):
        tools.past_decisions(status="pending")


def test_search_profile_only_reads_the_profile(conn: sqlite3.Connection) -> None:
    add_posting(conn, "greenhouse:1", body="Machine learning internship in Zurich.")
    chunk_pending_postings(conn)
    conn.execute(
        "INSERT INTO chunks (posting_id, profile_doc, ordinal, text) "
        "VALUES (NULL, 'gnn-maze-solver.md', 0, 'I built a graph neural network maze solver.')"
    )
    conn.commit()

    hits = tools.search_profile("graph neural network")
    assert hits, "the profile chunk should be findable"
    assert all(h["document"] == "gnn-maze-solver.md" for h in hits)
    # The full hit shape, so the trace panel draws these with score bars --
    # but never a posting. A job leaking into "here is your own background"
    # is how a letter ends up grounded in someone else's words.
    assert all(h["posting_id"] is None for h in hits)
    assert all("component_scores" in h and "rank" in h for h in hits)


# --- the search budget -----------------------------------------------------


class _Block:
    """Stands in for one SDK content block, which is a plain attribute bag."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


def _reply(*blocks: _Block) -> Any:
    return _Block(content=list(blocks))


def _tool_use(call_id: str, name: str, payload: dict[str, Any]) -> _Block:
    return _Block(type="tool_use", id=call_id, name=name, input=payload)


def _text(text: str) -> _Block:
    return _Block(type="text", text=text)


class _FakeAnthropic:
    """Replays a script of replies, so the loop is tested and not the model."""

    def __init__(self, script: list[Any]) -> None:
        self._script = script
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        return self._script.pop(0) if self._script else _reply(_text("done"))


@pytest.fixture
def anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # The screen calls the same SDK these tests replace wholesale, so with it
    # on it eats replies scripted for the agent and the budget assertions
    # count the wrong calls. What the screen does is tested in
    # test_screening.py; here it would only be noise.
    monkeypatch.setenv("SCREEN_RESULTS", "0")
    reset_settings()


def test_run_agent_refuses_searches_past_the_budget(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, anthropic_key: None
) -> None:
    """The ceiling that keeps "search again" from meaning "search forever".

    A model convinced the corpus holds something it does not will rephrase
    until it runs out of iterations. The refusal has to reach it as a readable
    tool result, and the turn still has to end with exactly one DoneEvent.
    """
    import anthropic

    from agent_app.core import agent

    add_posting(conn, "greenhouse:1")
    chunk_pending_postings(conn)

    script = [
        _reply(_tool_use(f"call{i}", "find_postings", {"query": f"attempt {i}"})) for i in range(5)
    ]
    script.append(_reply(_text("I could not find it.")))
    monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: _FakeAnthropic(script))

    events = list(agent.run_agent("find me something", [], max_iters=10, max_searches=2))

    done = [e for e in events if isinstance(e, agent.DoneEvent)]
    assert len(done) == 1 and events[-1] is done[0]

    results = [
        e for e in events if isinstance(e, agent.ToolResultEvent) and e.name == "find_postings"
    ]
    refused = [e for e in results if isinstance(e.output, str) and "budget spent" in e.output]
    assert len(results) == 5, "every attempt is announced, so the trace stays honest"
    assert len(refused) == 3, "only the first two searches actually ran"
    assert done[0].result.text.endswith("I could not find it.")


def test_run_agent_leaves_unbudgeted_tools_alone(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, anthropic_key: None
) -> None:
    """corpus_stats is a local count. Budgeting it would punish the honest path."""
    import anthropic

    from agent_app.core import agent

    add_posting(conn, "greenhouse:1")
    chunk_pending_postings(conn)

    script = [_reply(_tool_use(f"call{i}", "corpus_stats", {"level": "intern"})) for i in range(4)]
    script.append(_reply(_text("there are not many.")))
    monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: _FakeAnthropic(script))

    events = list(agent.run_agent("how many?", [], max_iters=10, max_searches=1))
    results = [
        e for e in events if isinstance(e, agent.ToolResultEvent) and e.name == "corpus_stats"
    ]
    assert len(results) == 4
    assert all(isinstance(e.output, dict) for e in results), "none of them was refused"
