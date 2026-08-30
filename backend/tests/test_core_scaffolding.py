"""Phase 3: the Category A parts of core/ that the Category B work sits on."""

from __future__ import annotations

import sqlite3

import pytest

from agent_app.core import tools
from agent_app.core.agent import (
    AgentResult,
    DoneEvent,
    TextEvent,
    ToolCall,
    ToolCallEvent,
    collect_result,
)
from agent_app.core.evaluate import EvalResult, load_eval_set
from agent_app.core.retrieval import (
    SearchFilters,
    SearchHit,
    candidate_sql,
    load_candidates,
    tokenize,
)


def _insert_posting(
    conn: sqlite3.Connection, posting_id: str = "greenhouse:1", **over: object
) -> str:
    fields = {
        "source": "greenhouse",
        "company": "Acme",
        "title": "Software Engineering Intern",
        "location": "Zurich, Switzerland",
        "remote": 0,
        "level": "intern",
        "posted_at": "2026-01-01T00:00:00Z",
    }
    fields.update(over)
    conn.execute(
        "INSERT INTO postings (id, source, company, title, location, remote, url, body,"
        " body_hash, posted_at, level, first_seen, last_seen)"
        " VALUES (?, ?, ?, ?, ?, ?, 'https://example.com', 'body text', 'h', ?, ?,"
        " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (
            posting_id,
            fields["source"],
            fields["company"],
            fields["title"],
            fields["location"],
            fields["remote"],
            fields["posted_at"],
            fields["level"],
        ),
    )
    conn.commit()
    return posting_id


# --- tokenizer -------------------------------------------------------------


def test_tokenize_lowercases_and_splits() -> None:
    assert tokenize("Machine Learning, Zurich!") == ["machine", "learning", "zurich"]


def test_tokenize_keeps_c_plus_plus_and_c_sharp() -> None:
    assert tokenize("C++ and C# and Python3") == ["c++", "and", "c#", "and", "python3"]


def test_tokenize_of_nothing_is_empty() -> None:
    assert tokenize("") == []


# --- filters ---------------------------------------------------------------


def test_search_filters_default_to_postings() -> None:
    assert SearchFilters().kind == "posting"


def test_search_filters_reject_nonsense() -> None:
    with pytest.raises(ValueError, match="kind"):
        SearchFilters(kind="everything")
    with pytest.raises(ValueError, match="level"):
        SearchFilters(level="senior")
    with pytest.raises(ValueError, match="status"):
        SearchFilters(status="pending")


def test_search_filters_from_dict_drops_invented_keys() -> None:
    # A model will hallucinate a filter name; one bad key should not fail the
    # whole tool call.
    filters = SearchFilters.from_dict({"level": "intern", "salary_min": 90000})
    assert filters.level == "intern"


def test_search_filters_from_dict_handles_none() -> None:
    assert SearchFilters.from_dict(None) == SearchFilters()


def test_untriaged_is_an_allowed_status_filter() -> None:
    assert SearchFilters(status="untriaged").status == "untriaged"


# --- candidate selection ---------------------------------------------------


def test_candidate_sql_filters_by_kind() -> None:
    posting_sql, _ = candidate_sql(SearchFilters(kind="posting"))
    profile_sql, _ = candidate_sql(SearchFilters(kind="profile"))
    any_sql, _ = candidate_sql(SearchFilters(kind="any"))

    assert "c.posting_id IS NOT NULL" in posting_sql
    assert "c.profile_doc IS NOT NULL" in profile_sql
    assert "IS NOT NULL" not in any_sql


def test_load_candidates_respects_filters(conn: sqlite3.Connection) -> None:
    _insert_posting(conn, "greenhouse:1", level="intern", company="Acme")
    _insert_posting(conn, "greenhouse:2", level="unknown", company="Beta")
    conn.executescript(
        "INSERT INTO chunks (posting_id, ordinal, text) VALUES ('greenhouse:1', 0, 'alpha');"
        "INSERT INTO chunks (posting_id, ordinal, text) VALUES ('greenhouse:2', 0, 'beta');"
        "INSERT INTO chunks (profile_doc, ordinal, text) VALUES ('my-project', 0, 'gamma');"
    )
    conn.commit()

    assert [c.text for c in load_candidates(conn, SearchFilters(kind="posting"))] == [
        "alpha",
        "beta",
    ]
    assert [c.text for c in load_candidates(conn, SearchFilters(kind="profile"))] == ["gamma"]
    assert len(load_candidates(conn, SearchFilters(kind="any"))) == 3
    assert [c.text for c in load_candidates(conn, SearchFilters(level="intern"))] == ["alpha"]
    assert [c.text for c in load_candidates(conn, SearchFilters(company="Beta"))] == ["beta"]
    assert [c.text for c in load_candidates(conn, SearchFilters(location="Zurich"))] == [
        "alpha",
        "beta",
    ]


def test_load_candidates_can_find_untriaged_postings(conn: sqlite3.Connection) -> None:
    _insert_posting(conn, "greenhouse:1")
    _insert_posting(conn, "greenhouse:2")
    conn.executescript(
        "INSERT INTO chunks (posting_id, ordinal, text) VALUES ('greenhouse:1', 0, 'alpha');"
        "INSERT INTO chunks (posting_id, ordinal, text) VALUES ('greenhouse:2', 0, 'beta');"
    )
    conn.commit()
    tools.update_status("greenhouse:1", "applied")

    untriaged = load_candidates(conn, SearchFilters(status="untriaged"))
    assert [c.posting_id for c in untriaged] == ["greenhouse:2"]

    applied = load_candidates(conn, SearchFilters(status="applied"))
    assert [c.posting_id for c in applied] == ["greenhouse:1"]


# --- hits ------------------------------------------------------------------


def test_search_hit_serialises_component_scores() -> None:
    hit = SearchHit(
        chunk_id=1,
        posting_id="greenhouse:1",
        profile_doc=None,
        ordinal=0,
        text="alpha",
        score=0.5,
        rank=1,
        component_scores={"dense": 0.3, "bm25": 0.2},
    )
    data = hit.to_dict()
    assert data["component_scores"] == {"dense": 0.3, "bm25": 0.2}
    # The contract the stacked bar depends on.
    assert sum(data["component_scores"].values()) == pytest.approx(data["score"])


# --- agent types -----------------------------------------------------------


def test_collect_result_returns_the_done_payload() -> None:
    result = AgentResult(text="done", history=[], trace=[ToolCall("t", {}, None, 1)], iters=2)
    events = iter([TextEvent("do"), TextEvent("ne"), DoneEvent(result)])
    assert collect_result(events) is result


def test_collect_result_rejects_a_stream_with_no_done_event() -> None:
    with pytest.raises(RuntimeError, match="DoneEvent"):
        collect_result(iter([TextEvent("hello")]))


def test_events_carry_their_sse_names() -> None:
    assert ToolCallEvent("search_postings", {}).event_name == "tool_call"
    assert TextEvent("x").event_name == "text"
    assert ToolCallEvent("search_postings", {"query": "ml"}).to_dict() == {
        "name": "search_postings",
        "input": {"query": "ml"},
    }


# --- tools -----------------------------------------------------------------


def test_update_status_writes_history(conn: sqlite3.Connection) -> None:
    _insert_posting(conn)

    first = tools.update_status("greenhouse:1", "interested", "looks good")
    assert first["from_status"] is None
    second = tools.update_status("greenhouse:1", "applied")
    assert second["from_status"] == "interested"

    history = conn.execute(
        "SELECT from_status, to_status FROM status_history ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in history] == [(None, "interested"), ("interested", "applied")]

    # One application row, updated in place.
    assert conn.execute("SELECT count(*) FROM applications").fetchone()[0] == 1


def test_update_status_rejects_an_invented_status(conn: sqlite3.Connection) -> None:
    _insert_posting(conn)
    with pytest.raises(ValueError, match="Unknown status"):
        tools.update_status("greenhouse:1", "pending")


def test_update_status_rejects_an_unknown_posting(conn: sqlite3.Connection) -> None:
    with pytest.raises(KeyError):
        tools.update_status("greenhouse:missing", "applied")


def test_get_posting_reports_untriaged(conn: sqlite3.Connection) -> None:
    _insert_posting(conn)
    data = tools.get_posting("greenhouse:1")
    assert data["status"] == "untriaged"
    assert data["history"] == []
    assert data["body"] == "body text"


def test_get_posting_includes_status_and_history(conn: sqlite3.Connection) -> None:
    _insert_posting(conn)
    tools.update_status("greenhouse:1", "interviewing", "call on friday")
    data = tools.get_posting("greenhouse:1")
    assert data["status"] == "interviewing"
    assert data["note"] == "call on friday"
    assert len(data["history"]) == 1


def test_get_posting_raises_for_a_missing_id(conn: sqlite3.Connection) -> None:
    with pytest.raises(KeyError):
        tools.get_posting("nope:1")


def test_list_shortlist(conn: sqlite3.Connection) -> None:
    _insert_posting(conn, "greenhouse:1")
    _insert_posting(conn, "greenhouse:2")
    tools.update_status("greenhouse:1", "applied")
    tools.update_status("greenhouse:2", "rejected")

    assert len(tools.list_shortlist()) == 2
    applied = tools.list_shortlist("applied")
    assert [p["posting_id"] for p in applied] == ["greenhouse:1"]

    with pytest.raises(ValueError, match="Unknown status"):
        tools.list_shortlist("pending")


def test_tool_functions_match_the_schemas() -> None:
    schema_names = {s["name"] for s in tools.TOOL_SCHEMAS}
    assert schema_names == set(tools.TOOL_FUNCTIONS)
    assert len(schema_names) == 4


def test_search_postings_raises_until_retrieval_exists(conn: sqlite3.Connection) -> None:
    # The tool itself is Category A and complete; it fails only because the
    # Category B search behind it is not written. That is correct behaviour.
    with pytest.raises(NotImplementedError):
        tools.search_postings("machine learning")


# --- eval set --------------------------------------------------------------


def test_load_eval_set(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "queries.jsonl"
    path.write_text(
        "# a comment line\n"
        '{"query": "remote ml internships", "relevant_posting_ids": ["greenhouse:1"]}\n'
        "\n"
        '{"query": "zurich robotics", "relevant_posting_ids": [], "note": "none yet"}\n',
        encoding="utf-8",
    )
    queries = load_eval_set(path)
    assert len(queries) == 2
    assert queries[0].relevant_posting_ids == ("greenhouse:1",)
    assert queries[1].note == "none yet"


def test_load_eval_set_explains_a_missing_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(FileNotFoundError, match="relevant_posting_ids"):
        load_eval_set(tmp_path / "nope.jsonl")


def test_load_eval_set_names_the_bad_line(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "queries.jsonl"
    path.write_text('{"query": "ok"}\nnot json at all\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        load_eval_set(path)


def test_eval_result_formats() -> None:
    result = EvalResult(n_queries=3, recall={1: 0.5, 5: 0.75}, per_query={})
    text = result.format()
    assert "3 queries" in text
    assert "recall@1" in text
