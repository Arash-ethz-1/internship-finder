"""The inverted index has to be an optimisation, not a second opinion.

`retrieval.bm25_scores` is the definition of a BM25 score in this project, and
it is one of the author's exercises. `bm25_index` exists only to compute the
same thing without re-reading the corpus, so the test that matters is that the
two agree to the last bit — not that the fast one looks plausible.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from agent_app import runtime
from agent_app.config import Settings
from agent_app.core.bm25_index import (
    INDEX_VERSION,
    build_index,
    get_or_build,
    load_index,
    save_index,
)
from agent_app.core.retrieval import (
    SearchFilters,
    bm25_scores,
    load_candidate_keys,
    load_texts,
    tokenize,
)

CORPUS = [
    "PyTorch internship in Zurich, deep learning research",
    "Praktikum Maschinelles Lernen, PyTorch und Python",
    "Frontend engineer, React and TypeScript, no machine learning here",
    "Data engineering internship: Python, Spark, Airflow",
    "PyTorch PyTorch PyTorch, a document that repeats itself",
]

QUERIES = [
    "pytorch",
    "python internship",
    "machine learning zurich",
    "kubernetes",  # in nothing
    "pytorch python react spark",
]


def fill(conn: sqlite3.Connection, texts: list[str]) -> list[int]:
    ids = []
    for ordinal, text in enumerate(texts):
        cursor = conn.execute(
            "INSERT INTO chunks (profile_doc, ordinal, text) VALUES ('doc', ?, ?)",
            (ordinal, text),
        )
        ids.append(int(cursor.lastrowid or 0))
    conn.commit()
    return ids


@pytest.mark.parametrize("query", QUERIES)
def test_the_index_reproduces_bm25_scores_exactly(conn: sqlite3.Connection, query: str) -> None:
    ids = fill(conn, CORPUS)

    reference = bm25_scores(query, [tokenize(text) for text in CORPUS])
    fast = build_index(conn).scores(query, ids)

    assert np.array_equal(fast, reference)


def test_a_chunk_the_index_has_never_seen_scores_zero(conn: sqlite3.Connection) -> None:
    ids = fill(conn, CORPUS)
    index = build_index(conn)

    later = fill(conn, ["a posting ingested after the last rebuild"])
    scores = index.scores("pytorch", [*ids, *later])

    assert scores.shape == (len(ids) + 1,)
    assert scores[-1] == 0.0


def test_scores_come_back_in_the_order_asked_for(conn: sqlite3.Connection) -> None:
    ids = fill(conn, CORPUS)
    index = build_index(conn)

    forward = index.scores("pytorch", ids)
    backward = index.scores("pytorch", list(reversed(ids)))

    assert np.array_equal(backward, forward[::-1])


def test_an_empty_corpus_scores_nothing(conn: sqlite3.Connection) -> None:
    assert build_index(conn).scores("pytorch", []).shape == (0,)


# --- the file it lives in --------------------------------------------------


def test_the_index_round_trips_through_disk(conn: sqlite3.Connection, settings: Settings) -> None:
    ids = fill(conn, CORPUS)
    original = build_index(conn)
    save_index(original, settings.bm25_index_path)

    restored = load_index(settings.bm25_index_path)
    assert restored is not None
    assert restored.terms == original.terms
    assert np.array_equal(
        restored.scores("pytorch python", ids), original.scores("pytorch python", ids)
    )


def test_a_missing_file_is_not_an_error(settings: Settings) -> None:
    assert load_index(settings.bm25_index_path) is None


def test_a_corrupt_file_is_rebuilt_rather_than_read(settings: Settings) -> None:
    settings.ensure_dirs()
    settings.bm25_index_path.write_bytes(b"not an npz")
    assert load_index(settings.bm25_index_path) is None


def test_an_index_from_an_older_layout_is_discarded(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(conn, CORPUS)
    save_index(build_index(conn), settings.bm25_index_path)

    monkeypatch.setattr("agent_app.core.bm25_index.INDEX_VERSION", INDEX_VERSION + 1)
    assert load_index(settings.bm25_index_path) is None


# --- staleness -------------------------------------------------------------


def test_a_current_index_is_read_rather_than_rebuilt(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(conn, CORPUS)
    get_or_build(conn, settings)

    def explode(_conn: sqlite3.Connection) -> None:
        raise AssertionError("rebuilt an index that was still current")

    monkeypatch.setattr("agent_app.core.bm25_index.build_index", explode)
    assert get_or_build(conn, settings).n_docs == len(CORPUS)


def test_new_chunks_make_the_index_stale(conn: sqlite3.Connection, settings: Settings) -> None:
    fill(conn, CORPUS)
    assert get_or_build(conn, settings).n_docs == len(CORPUS)

    fill(conn, ["one more chunk"])
    assert get_or_build(conn, settings).n_docs == len(CORPUS) + 1


def test_force_rebuilds_a_current_index(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(conn, CORPUS)
    get_or_build(conn, settings)

    built = False

    def spy(connection: sqlite3.Connection):  # noqa: ANN202
        nonlocal built
        built = True
        return build_index(connection)

    monkeypatch.setattr("agent_app.core.bm25_index.build_index", spy)
    get_or_build(conn, settings, force=True)
    assert built


def test_runtime_caches_the_index(conn: sqlite3.Connection) -> None:
    fill(conn, CORPUS)
    first = runtime.get_bm25_index()
    assert runtime.get_bm25_index() is first

    runtime.reset_bm25_index()
    assert runtime.get_bm25_index() is not first


# --- the loaders search actually uses --------------------------------------


def test_candidate_keys_carry_everything_but_the_text(conn: sqlite3.Connection) -> None:
    fill(conn, CORPUS)
    keys = load_candidate_keys(conn, SearchFilters(kind="any"))

    assert len(keys) == len(CORPUS)
    assert not hasattr(keys[0], "text")
    assert [k.ordinal for k in keys] == list(range(len(CORPUS)))


def test_texts_are_fetched_only_for_the_ids_asked_for(conn: sqlite3.Connection) -> None:
    ids = fill(conn, CORPUS)
    texts = load_texts(conn, ids[1:3])

    assert set(texts) == set(ids[1:3])
    assert texts[ids[1]] == CORPUS[1]
    assert load_texts(conn, []) == {}
