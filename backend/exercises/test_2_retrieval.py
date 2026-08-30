"""Problems 3-6: dense_scores, bm25_scores, fuse and search."""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from agent_app.core.retrieval import (
    COMPONENT_BM25,
    COMPONENT_DENSE,
    SearchFilters,
    bm25_scores,
    dense_scores,
    fuse,
    search,
    tokenize,
)

# --- problem 3: dense_scores -----------------------------------------------


def test_dense_shape() -> None:
    scores = dense_scores(np.array([1.0, 0.0, 0.0]), np.eye(3, dtype=np.float32))
    assert scores.shape == (3,)


def test_dense_identical_vectors_score_one() -> None:
    vector = np.array([0.3, 0.5, 0.8], dtype=np.float32)
    assert dense_scores(vector, vector.reshape(1, -1))[0] == pytest.approx(1.0, abs=1e-5)


def test_dense_ignores_magnitude() -> None:
    # The test that separates cosine similarity from a plain dot product: a
    # document is not more relevant for being longer.
    query = np.array([1.0, 1.0], dtype=np.float32)
    matrix = np.array([[1.0, 1.0], [10.0, 10.0]], dtype=np.float32)
    scores = dense_scores(query, matrix)
    assert scores[0] == pytest.approx(scores[1], abs=1e-5)


def test_dense_orthogonal_and_opposite() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)
    matrix = np.array([[0.0, 1.0], [-1.0, 0.0]], dtype=np.float32)
    scores = dense_scores(query, matrix)
    assert scores[0] == pytest.approx(0.0, abs=1e-5)
    assert scores[1] == pytest.approx(-1.0, abs=1e-5)


def test_dense_ranks_the_closer_vector_higher() -> None:
    query = np.array([1.0, 0.0], dtype=np.float32)
    matrix = np.array([[0.9, 0.1], [0.1, 0.9]], dtype=np.float32)
    scores = dense_scores(query, matrix)
    assert scores[0] > scores[1]


def test_dense_empty_matrix() -> None:
    scores = dense_scores(np.array([1.0, 0.0]), np.zeros((0, 2), dtype=np.float32))
    assert scores.shape == (0,)


def test_dense_zero_vector_is_not_nan() -> None:
    # Dividing by a zero magnitude is the obvious way to produce nan, and a
    # single nan poisons the whole ranking.
    scores = dense_scores(np.zeros(3), np.zeros((2, 3), dtype=np.float32))
    assert np.all(np.isfinite(scores))


# --- problem 4: bm25_scores ------------------------------------------------

CORPUS = [
    tokenize("machine learning internship with pytorch"),
    tokenize("backend engineering role in go and kubernetes"),
    tokenize("pytorch pytorch pytorch deep learning research"),
]


def test_bm25_shape() -> None:
    assert bm25_scores("pytorch", CORPUS).shape == (3,)


def test_bm25_finds_the_term() -> None:
    scores = bm25_scores("pytorch", CORPUS)
    assert scores[0] > scores[1]
    assert scores[2] > scores[1]


def test_a_document_with_the_term_never_loses_to_one_without() -> None:
    scores = bm25_scores("pytorch", CORPUS)
    assert min(scores[0], scores[2]) >= scores[1]


def test_bm25_term_frequency_saturates() -> None:
    # Three occurrences beat one, but nowhere near three times as much: that
    # damping is what k1 is for.
    corpus = [tokenize("pytorch"), tokenize("pytorch pytorch pytorch")]
    scores = bm25_scores("pytorch", corpus)
    assert scores[1] > scores[0]
    assert scores[1] < 3 * scores[0]


def test_bm25_rewards_the_rarer_term() -> None:
    corpus = [
        tokenize("common rare"),
        tokenize("common word here"),
        tokenize("common word there"),
        tokenize("common word everywhere"),
    ]
    rare = bm25_scores("rare", corpus)[0]
    common = bm25_scores("common", corpus)[0]
    assert rare > common


def test_bm25_no_match_returns_zeros() -> None:
    scores = bm25_scores("kubernetes", [tokenize("machine learning")])
    assert np.all(scores == 0)


def test_bm25_empty_corpus() -> None:
    assert bm25_scores("anything", []).shape == (0,)


def test_bm25_term_in_every_document_is_finite() -> None:
    # The textbook IDF goes negative when n(t) == N. Whatever you do about it,
    # the result must not be nan, inf, or rank a matching document last.
    corpus = [tokenize("python role"), tokenize("python job"), tokenize("python work")]
    scores = bm25_scores("python", corpus)
    assert np.all(np.isfinite(scores))


def test_bm25_multi_term_query() -> None:
    scores = bm25_scores("pytorch learning", CORPUS)
    assert np.all(np.isfinite(scores))
    assert scores[0] > scores[1]


# --- problem 5: fuse -------------------------------------------------------


def test_fuse_shape() -> None:
    a = np.array([3.0, 2.0, 1.0])
    b = np.array([1.0, 2.0, 3.0])
    assert fuse([a, b]).shape == (3,)


def test_fuse_rewards_agreement() -> None:
    # Candidate 0 is first in both lists; candidate 2 is first in only one.
    a = np.array([9.0, 5.0, 1.0])
    b = np.array([9.0, 1.0, 5.0])
    fused = fuse([a, b])
    assert fused[0] > fused[1]
    assert fused[0] > fused[2]


def test_fuse_ignores_the_scale_of_each_list() -> None:
    # The whole point: BM25's larger numbers must not drown out cosine.
    small = np.array([0.9, 0.5, 0.1])
    huge = np.array([90.0, 50.0, 10.0])
    assert fuse([small, huge]) == pytest.approx(fuse([small, small]))


def test_fuse_with_one_list_preserves_its_order() -> None:
    fused = fuse([np.array([1.0, 3.0, 2.0])])
    assert fused[1] > fused[2] > fused[0]


def test_fuse_handles_ties() -> None:
    fused = fuse([np.array([1.0, 1.0, 1.0]), np.array([2.0, 2.0, 2.0])])
    assert np.all(np.isfinite(fused))


def test_fuse_of_empty_arrays() -> None:
    assert fuse([np.array([]), np.array([])]).shape == (0,)


# --- problem 6: search -----------------------------------------------------


class FakeProvider:
    """Embeds by counting characters. Deterministic and dimension-2."""

    model = "fake"
    dim = 2

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.array([[float(len(t)), 1.0] for t in texts], dtype=np.float32)


@pytest.fixture
def corpus(conn: sqlite3.Connection, settings, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Three embedded posting chunks, wired into runtime."""
    from agent_app import runtime
    from agent_app.core.embeddings import embed_all_pending

    conn.execute(
        "INSERT INTO postings (id, source, company, title, location, remote, url, body,"
        " body_hash, level, first_seen, last_seen) VALUES ('greenhouse:1', 'greenhouse',"
        " 'Acme', 'ML Intern', 'Zurich', 0, 'https://e.com', 'b', 'h', 'intern',"
        " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO postings (id, source, company, title, location, remote, url, body,"
        " body_hash, level, first_seen, last_seen) VALUES ('lever:2', 'lever',"
        " 'Beta', 'Backend Engineer', 'Berlin', 1, 'https://e.com', 'b', 'h', 'unknown',"
        " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    for posting_id, text in [
        ("greenhouse:1", "machine learning internship with pytorch"),
        ("greenhouse:1", "you will train models and write evaluation harnesses"),
        ("lever:2", "backend engineering role in go and kubernetes"),
    ]:
        conn.execute(
            "INSERT INTO chunks (posting_id, ordinal, text) VALUES (?, 0, ?)",
            (posting_id, text),
        )
    conn.commit()

    embed_all_pending(conn, FakeProvider(), settings)
    runtime.reset_vectors()
    monkeypatch.setattr(runtime, "get_provider", FakeProvider)
    return conn


def test_search_returns_hits(corpus) -> None:  # type: ignore[no-untyped-def]
    hits = search("pytorch machine learning", SearchFilters(), k=10)
    assert hits, "expected at least one hit"


def test_search_respects_k(corpus) -> None:  # type: ignore[no-untyped-def]
    assert len(search("pytorch", SearchFilters(), k=2)) <= 2


def test_search_is_sorted_and_ranked(corpus) -> None:  # type: ignore[no-untyped-def]
    hits = search("pytorch", SearchFilters(), k=10)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))


def test_component_scores_sum_to_the_total(corpus) -> None:  # type: ignore[no-untyped-def]
    # The contract the stacked bar in the dashboard depends on. If these do not
    # add up, the chart is decoration rather than a measurement.
    for hit in search("pytorch learning", SearchFilters(), k=10):
        assert set(hit.component_scores) == {COMPONENT_DENSE, COMPONENT_BM25}
        assert sum(hit.component_scores.values()) == pytest.approx(hit.score, rel=1e-6)


def test_search_respects_filters(corpus) -> None:  # type: ignore[no-untyped-def]
    hits = search("engineering", SearchFilters(level="intern"), k=10)
    assert all(h.posting_id == "greenhouse:1" for h in hits)


def test_search_over_profile_chunks(corpus, conn) -> None:  # type: ignore[no-untyped-def]
    from agent_app import runtime
    from agent_app.core.embeddings import embed_all_pending

    conn.execute("INSERT INTO chunks (profile_doc, ordinal, text) VALUES ('pyblio', 0, 'a parser')")
    conn.commit()
    embed_all_pending(conn, FakeProvider(), None)
    runtime.reset_vectors()

    hits = search("parser", SearchFilters(kind="profile"), k=5)
    assert hits
    assert all(h.profile_doc is not None and h.posting_id is None for h in hits)


def test_search_with_no_candidates_returns_empty(corpus) -> None:  # type: ignore[no-untyped-def]
    assert search("anything", SearchFilters(company="Nonexistent"), k=10) == []
