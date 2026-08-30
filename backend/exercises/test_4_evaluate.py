"""Problem 9: recall_at_k and run_eval."""

from __future__ import annotations

import pytest

from agent_app.core.evaluate import EvalQuery, recall_at_k, run_eval
from agent_app.core.retrieval import SearchHit

# --- recall_at_k -----------------------------------------------------------


def test_all_relevant_in_the_top_k() -> None:
    assert recall_at_k(["a", "b", "c"], ["a", "c"], 3) == pytest.approx(1.0)


def test_half_the_relevant_in_the_top_k() -> None:
    assert recall_at_k(["a", "b", "c"], ["a", "c"], 1) == pytest.approx(0.5)


def test_none_retrieved() -> None:
    assert recall_at_k(["x", "y"], ["a", "b"], 2) == pytest.approx(0.0)


def test_k_larger_than_the_result_list() -> None:
    assert recall_at_k(["a"], ["a", "b"], 100) == pytest.approx(0.5)


def test_order_beyond_k_is_ignored() -> None:
    # "b" is retrieved, but at rank 3, so recall@2 must not count it.
    assert recall_at_k(["a", "x", "b"], ["a", "b"], 2) == pytest.approx(0.5)


def test_empty_relevant_does_not_crash() -> None:
    # Division by zero is the trap. 0.0 is a defensible answer; so is 1.0.
    # Crashing is not.
    value = recall_at_k(["a"], [], 5)
    assert isinstance(value, float)
    assert 0.0 <= value <= 1.0


def test_empty_retrieved() -> None:
    assert recall_at_k([], ["a"], 5) == pytest.approx(0.0)


def test_duplicates_do_not_inflate_the_score() -> None:
    # Several chunks of one posting can match, so the same id shows up twice.
    # Recall counts distinct relevant items found, so this is still 0.5.
    assert recall_at_k(["a", "a", "a"], ["a", "b"], 3) == pytest.approx(0.5)


# --- run_eval --------------------------------------------------------------


def hit(posting_id: str, rank: int) -> SearchHit:
    return SearchHit(
        chunk_id=rank,
        posting_id=posting_id,
        profile_doc=None,
        ordinal=0,
        text="text",
        score=1.0 / rank,
        rank=rank,
        component_scores={"dense": 0.5 / rank, "bm25": 0.5 / rank},
    )


@pytest.fixture
def fake_search(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Replace retrieval.search with a scripted result per query."""

    def install(results: dict[str, list[SearchHit]]) -> None:
        def fake(query: str, filters, k: int = 10):  # type: ignore[no-untyped-def]
            return results.get(query, [])[:k]

        monkeypatch.setattr("agent_app.core.retrieval.search", fake)

    return install


def test_run_eval_perfect_retrieval(fake_search) -> None:  # type: ignore[no-untyped-def]
    fake_search({"ml internships": [hit("greenhouse:1", 1), hit("lever:2", 2)]})
    queries = [EvalQuery(query="ml internships", relevant_posting_ids=("greenhouse:1", "lever:2"))]

    result = run_eval(queries, k_values=(1, 5))

    assert result.n_queries == 1
    assert result.recall[5] == pytest.approx(1.0)
    assert result.recall[1] == pytest.approx(0.5)


def test_run_eval_averages_across_queries(fake_search) -> None:  # type: ignore[no-untyped-def]
    fake_search(
        {
            "good": [hit("greenhouse:1", 1)],
            "bad": [hit("lever:9", 1)],
        }
    )
    queries = [
        EvalQuery(query="good", relevant_posting_ids=("greenhouse:1",)),
        EvalQuery(query="bad", relevant_posting_ids=("greenhouse:5",)),
    ]

    result = run_eval(queries, k_values=(5,))
    assert result.recall[5] == pytest.approx(0.5)


def test_run_eval_collapses_chunks_to_postings(fake_search) -> None:  # type: ignore[no-untyped-def]
    # Search returns chunk hits; three chunks of one posting is still one
    # posting, and its rank is the best of them. Without collapsing, a posting
    # that matched three times would push everything else out of the top k.
    fake_search(
        {
            "q": [
                hit("greenhouse:1", 1),
                hit("greenhouse:1", 2),
                hit("greenhouse:1", 3),
                hit("lever:2", 4),
            ]
        }
    )
    queries = [EvalQuery(query="q", relevant_posting_ids=("greenhouse:1", "lever:2"))]

    result = run_eval(queries, k_values=(2,))
    assert result.recall[2] == pytest.approx(1.0), (
        "both postings are in the top 2 once duplicate chunks are collapsed"
    )


def test_run_eval_reports_every_k(fake_search) -> None:  # type: ignore[no-untyped-def]
    fake_search({"q": [hit("greenhouse:1", 1)]})
    result = run_eval([EvalQuery(query="q", relevant_posting_ids=("greenhouse:1",))], (1, 5, 10))
    assert set(result.recall) == {1, 5, 10}


def test_run_eval_of_no_queries(fake_search) -> None:  # type: ignore[no-untyped-def]
    fake_search({})
    result = run_eval([], k_values=(5,))
    assert result.n_queries == 0
    assert "0 queries" in result.format()
