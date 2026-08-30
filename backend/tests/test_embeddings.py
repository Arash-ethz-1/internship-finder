"""Phase 4: embedding plumbing, offline.

No API key and no network. The Voyage client is exercised through an
``httpx.MockTransport``, and everything above it through a fake provider that
returns deterministic vectors.
"""

from __future__ import annotations

import sqlite3

import httpx
import numpy as np
import pytest

from agent_app import runtime
from agent_app.config import Settings
from agent_app.core.embeddings import (
    BATCH_SIZE,
    EmbeddingCache,
    EmbeddingError,
    EmbedReport,
    VectorMeta,
    VoyageProvider,
    embed_all_pending,
    embed_texts,
    load_vectors,
    rebuild_vectors,
    save_vectors,
    write_meta,
)


class FakeProvider:
    """Deterministic vectors, and a record of what it was asked to embed."""

    model = "fake-model"
    dim = 4

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        return np.array([[float(len(t)), 1.0, 2.0, 3.0] for t in texts], dtype=np.float32)


def add_chunk(conn: sqlite3.Connection, text: str, doc: str = "doc") -> int:
    cursor = conn.execute(
        "INSERT INTO chunks (profile_doc, ordinal, text) VALUES (?, 0, ?)", (doc, text)
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


# --- cache -----------------------------------------------------------------


def test_cache_round_trips(settings: Settings) -> None:
    settings.ensure_dirs()
    cache = EmbeddingCache(settings.embed_cache_dir)
    vector = np.array([1.0, 2.0, 3.0], dtype=np.float32)

    assert cache.get("m", "hello") is None
    cache.put("m", "hello", vector)
    assert np.array_equal(cache.get("m", "hello"), vector)
    assert cache.hits == 1
    assert cache.misses == 1


def test_cache_is_keyed_by_model_as_well_as_text(settings: Settings) -> None:
    settings.ensure_dirs()
    cache = EmbeddingCache(settings.embed_cache_dir)
    cache.put("model-a", "hello", np.array([1.0], dtype=np.float32))
    # The same text under a different model is a different vector space.
    assert cache.get("model-b", "hello") is None


def test_cache_shards_by_hash_prefix(settings: Settings) -> None:
    settings.ensure_dirs()
    cache = EmbeddingCache(settings.embed_cache_dir)
    cache.put("m", "hello", np.array([1.0], dtype=np.float32))
    written = list(settings.embed_cache_dir.rglob("*.npy"))
    assert len(written) == 1
    # One directory holding 100k files is slow on every OS.
    assert len(written[0].parent.name) == 2


def test_cache_treats_a_corrupt_entry_as_a_miss(settings: Settings) -> None:
    settings.ensure_dirs()
    cache = EmbeddingCache(settings.embed_cache_dir)
    path = cache.path(cache.key("m", "hello"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a numpy file")
    assert cache.get("m", "hello") is None


def test_embed_texts_only_pays_for_misses(settings: Settings) -> None:
    settings.ensure_dirs()
    cache = EmbeddingCache(settings.embed_cache_dir)
    provider = FakeProvider()

    first = embed_texts(provider, ["a", "bb", "ccc"], cache)
    assert first.shape == (3, 4)
    assert provider.calls == [["a", "bb", "ccc"]]

    second = embed_texts(provider, ["a", "bb", "ccc"], cache)
    assert np.array_equal(first, second)
    # Nothing new was requested the second time.
    assert len(provider.calls) == 1


def test_embed_texts_preserves_order_when_some_are_cached(settings: Settings) -> None:
    settings.ensure_dirs()
    cache = EmbeddingCache(settings.embed_cache_dir)
    provider = FakeProvider()

    embed_texts(provider, ["bb"], cache)
    result = embed_texts(provider, ["a", "bb", "ccc"], cache)

    # Row i must still correspond to texts[i] after the cache/miss interleave.
    assert [row[0] for row in result] == [1.0, 2.0, 3.0]
    assert provider.calls[-1] == ["a", "ccc"]


def test_embed_texts_of_nothing(settings: Settings) -> None:
    settings.ensure_dirs()
    cache = EmbeddingCache(settings.embed_cache_dir)
    assert embed_texts(FakeProvider(), [], cache).shape == (0, 4)


# --- the voyage client -----------------------------------------------------


def voyage(handler, **kwargs) -> VoyageProvider:  # type: ignore[no-untyped-def]
    return VoyageProvider(
        api_key="test-key",
        model="voyage-3.5",
        dim=3,
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        **kwargs,
    )


def reply(texts: list[str]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": [
                {"object": "embedding", "index": i, "embedding": [float(i), 1.0, 2.0]}
                for i in range(len(texts))
            ]
        },
    )


def test_voyage_sends_the_key_and_model() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        seen.append(body)
        return reply(body["input"])

    result = voyage(handler).embed(["a", "b"])
    assert result.shape == (2, 3)
    assert seen[0]["model"] == "voyage-3.5"
    assert seen[0]["input"] == ["a", "b"]


def test_voyage_batches_at_64() -> None:
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        texts = json.loads(request.content)["input"]
        sizes.append(len(texts))
        return reply(texts)

    voyage(handler).embed([f"t{i}" for i in range(150)])
    assert sizes == [BATCH_SIZE, BATCH_SIZE, 150 - 2 * BATCH_SIZE]


def test_voyage_retries_a_rate_limit_then_succeeds() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        import json

        calls += 1
        if calls < 3:
            return httpx.Response(429)
        return reply(json.loads(request.content)["input"])

    assert voyage(handler).embed(["a"]).shape == (1, 3)
    assert calls == 3


def test_voyage_does_not_retry_a_bad_key() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, text="invalid api key")

    with pytest.raises(EmbeddingError, match="401"):
        voyage(handler).embed(["a"])
    # Waiting will not make a wrong key right.
    assert calls == 1


def test_voyage_reorders_by_index() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [9.0, 9.0, 9.0]},
                    {"index": 0, "embedding": [1.0, 1.0, 1.0]},
                ]
            },
        )

    result = voyage(handler).embed(["first", "second"])
    assert list(result[0]) == [1.0, 1.0, 1.0]


def test_voyage_rejects_a_short_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 1.0, 1.0]}]})

    with pytest.raises(EmbeddingError, match="expected 2"):
        voyage(handler).embed(["a", "b"])


# --- the vector store ------------------------------------------------------


def test_load_vectors_of_a_missing_file_is_empty(settings: Settings) -> None:
    matrix = load_vectors(settings)
    assert matrix.shape == (0, settings.embedding_dim)


def test_save_and_load_round_trip(settings: Settings) -> None:
    matrix = np.arange(8, dtype=np.float32).reshape(2, 4)
    save_vectors(matrix, settings)
    assert np.array_equal(load_vectors(settings), matrix)
    assert settings.vectors_meta_path.exists()


def test_loading_vectors_from_another_model_is_refused(settings: Settings) -> None:
    save_vectors(np.zeros((2, settings.embedding_dim), dtype=np.float32), settings)
    write_meta(
        settings.vectors_meta_path,
        VectorMeta(model="some-other-model", dim=settings.embedding_dim, rows=2),
    )
    # Silently mixing two vector spaces would make every search subtly wrong.
    with pytest.raises(EmbeddingError, match="cannot be compared"):
        load_vectors(settings)


# --- embed_all_pending -----------------------------------------------------


def test_embed_all_pending(conn: sqlite3.Connection, settings: Settings) -> None:
    for text in ("alpha", "beta", "gamma"):
        add_chunk(conn, text)

    provider = FakeProvider()
    report = embed_all_pending(conn, provider, settings)

    assert (report.pending, report.embedded, report.total_rows) == (3, 3, 3)
    assert load_vectors(settings).shape == (3, 4)

    rows = conn.execute("SELECT id, vector_row FROM chunks ORDER BY id").fetchall()
    assert [r["vector_row"] for r in rows] == [0, 1, 2]


def test_second_embed_run_is_a_no_op(conn: sqlite3.Connection, settings: Settings) -> None:
    add_chunk(conn, "alpha")
    provider = FakeProvider()
    embed_all_pending(conn, provider, settings)

    report = embed_all_pending(conn, provider, settings)

    assert report.pending == 0
    assert "0 pending" in report.format()
    assert len(provider.calls) == 1


def test_embed_appends_rather_than_overwriting(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    add_chunk(conn, "alpha")
    provider = FakeProvider()
    embed_all_pending(conn, provider, settings)

    add_chunk(conn, "beta")
    embed_all_pending(conn, provider, settings)

    assert load_vectors(settings).shape == (2, 4)
    rows = conn.execute("SELECT vector_row FROM chunks ORDER BY id").fetchall()
    assert [r["vector_row"] for r in rows] == [0, 1]


def test_a_chunks_vector_is_the_one_it_points_at(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    # The invariant everything downstream depends on.
    ids = [add_chunk(conn, text) for text in ("a", "bb", "ccc")]
    embed_all_pending(conn, FakeProvider(), settings)
    matrix = load_vectors(settings)

    for chunk_id, expected_length in zip(ids, [1.0, 2.0, 3.0], strict=True):
        row = conn.execute("SELECT vector_row FROM chunks WHERE id = ?", (chunk_id,)).fetchone()[
            "vector_row"
        ]
        assert matrix[row][0] == expected_length


def test_embed_processes_in_windows(conn: sqlite3.Connection, settings: Settings) -> None:
    for i in range(5):
        add_chunk(conn, f"text-{i}")
    provider = FakeProvider()

    embed_all_pending(conn, provider, settings, batch_rows=2)

    assert [len(call) for call in provider.calls] == [2, 2, 1]
    assert load_vectors(settings).shape == (5, 4)


def test_embed_report_formats() -> None:
    assert "0 pending" in EmbedReport(pending=0, total_rows=7).format()
    assert (
        "3 chunk(s) embedded"
        in EmbedReport(pending=3, embedded=3, cache_hits=1, total_rows=3).format()
    )


# --- rebuild_vectors -------------------------------------------------------


def test_rebuild_vectors_compacts_after_deletion(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    ids = [add_chunk(conn, text) for text in ("a", "bb", "ccc")]
    embed_all_pending(conn, FakeProvider(), settings)

    conn.execute("DELETE FROM chunks WHERE id = ?", (ids[1],))
    conn.commit()

    before, after = rebuild_vectors(conn, settings)
    assert (before, after) == (3, 2)

    matrix = load_vectors(settings)
    assert matrix.shape == (2, 4)
    # Renumbered contiguously, and still pointing at the right vectors.
    rows = conn.execute("SELECT id, vector_row FROM chunks ORDER BY id").fetchall()
    assert [r["vector_row"] for r in rows] == [0, 1]
    assert [matrix[r["vector_row"]][0] for r in rows] == [1.0, 3.0]


def test_rebuild_vectors_with_nothing_left(conn: sqlite3.Connection, settings: Settings) -> None:
    add_chunk(conn, "alpha")
    embed_all_pending(conn, FakeProvider(), settings)
    conn.execute("DELETE FROM chunks")
    conn.commit()

    before, after = rebuild_vectors(conn, settings)
    assert (before, after) == (1, 0)
    assert load_vectors(settings).shape == (0, settings.embedding_dim)


def test_rebuild_refuses_a_dangling_reference(conn: sqlite3.Connection, settings: Settings) -> None:
    add_chunk(conn, "alpha")
    conn.execute("UPDATE chunks SET vector_row = 99")
    conn.commit()
    with pytest.raises(EmbeddingError, match="do not exist"):
        rebuild_vectors(conn, settings)


# --- runtime wiring --------------------------------------------------------


def test_get_vectors_loads_and_caches(conn: sqlite3.Connection, settings: Settings) -> None:
    add_chunk(conn, "alpha")
    embed_all_pending(conn, FakeProvider(), settings)
    runtime.reset_vectors()

    first = runtime.get_vectors()
    assert first.shape == (1, 4)
    # Same object back: the matrix is cached, not re-read per search.
    assert runtime.get_vectors() is first


def test_get_provider_needs_a_key(settings: Settings) -> None:
    from agent_app.config import ConfigError

    with pytest.raises(ConfigError, match="VOYAGE_API_KEY"):
        runtime.get_provider()
