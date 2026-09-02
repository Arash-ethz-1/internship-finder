"""Phase 4: embedding plumbing, offline.

No API key and no network. The Voyage client is exercised through an
``httpx.MockTransport``, and everything above it through a fake provider that
returns deterministic vectors.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx
import numpy as np
import pytest

from agent_app import runtime
from agent_app.config import PROVIDER_DEFAULTS, ConfigError, Settings, reset_settings
from agent_app.core.embeddings import (
    BATCH_SIZE,
    LOCAL_MODEL_PREFIXES,
    EmbeddingCache,
    EmbeddingError,
    EmbedReport,
    LocalProvider,
    VectorMeta,
    VoyageProvider,
    build_provider,
    embed_all_pending,
    embed_texts,
    export_pending,
    import_vectors,
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


def test_get_provider_needs_a_key_only_for_voyage(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default provider is local, so this is about the paid path only."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    reset_settings()
    runtime.reset()
    with pytest.raises(ConfigError, match="VOYAGE_API_KEY"):
        runtime.get_provider()


# --- choosing a provider ---------------------------------------------------


class FakeEmbedder:
    """Stands in for fastembed's TextEmbedding, and records what it saw."""

    def __init__(self, dim: int = 384) -> None:
        self.seen: list[str] = []
        self.dim = dim

    def embed(self, texts, batch_size: int = 0):  # noqa: ANN001, ANN202
        for text in texts:
            self.seen.append(text)
            yield np.full(self.dim, 0.5, dtype=np.float32)


def test_local_is_the_default_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh clone with no keys at all must still be able to embed."""
    reset_settings()
    provider = build_provider()
    assert isinstance(provider, LocalProvider)
    assert (provider.model, provider.dim) == PROVIDER_DEFAULTS["local"]


def test_voyage_is_chosen_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    reset_settings()
    provider = build_provider()
    assert isinstance(provider, VoyageProvider)
    assert (provider.model, provider.dim) == PROVIDER_DEFAULTS["voyage"]


def test_voyage_without_a_key_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    reset_settings()
    with pytest.raises(ConfigError, match="VOYAGE_API_KEY"):
        build_provider()


def test_an_unknown_provider_is_rejected_at_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    reset_settings()
    with pytest.raises(ConfigError, match="local, voyage"):
        build_provider()


# --- the local provider ----------------------------------------------------


def test_local_embeds_nothing_without_loading_the_model() -> None:
    """Importing and calling with no texts must not download 200 MB."""
    provider = LocalProvider()
    assert provider.embed([]).shape == (0, provider.dim)
    assert provider._embedder is None


def test_local_returns_one_row_per_text() -> None:
    provider = LocalProvider(_embedder=FakeEmbedder())
    assert provider.embed(["a", "b", "c"]).shape == (3, 384)


def test_local_rejects_a_dimension_mismatch() -> None:
    provider = LocalProvider(dim=1024, _embedder=FakeEmbedder(dim=384))
    with pytest.raises(EmbeddingError, match="EMBEDDING_DIM=384"):
        provider.embed(["a"])


def test_local_prefixes_the_models_that_need_it() -> None:
    model = next(iter(LOCAL_MODEL_PREFIXES))
    embedder = FakeEmbedder(dim=1024)
    LocalProvider(model=model, dim=1024, _embedder=embedder).embed(["cheese"])
    assert embedder.seen == [LOCAL_MODEL_PREFIXES[model] + "cheese"]


def test_local_leaves_other_models_alone() -> None:
    embedder = FakeEmbedder()
    LocalProvider(_embedder=embedder).embed(["cheese"])
    assert embedder.seen == ["cheese"]


def test_local_names_the_supported_models_when_the_name_is_wrong() -> None:
    """Offline: fastembed rejects an unknown name from its own registry."""
    with pytest.raises(EmbeddingError, match="no ONNX build"):
        LocalProvider(model="intfloat/multilingual-e5-small").embed(["a"])


# --- progress --------------------------------------------------------------


def test_embed_all_pending_reports_progress(conn: sqlite3.Connection) -> None:
    for i in range(5):
        add_chunk(conn, f"chunk {i}")

    seen: list[tuple[int, int]] = []
    embed_all_pending(conn, FakeProvider(), batch_rows=2, progress=lambda d, t: seen.append((d, t)))
    assert seen == [(2, 5), (4, 5), (5, 5)]


# --- embedding on another machine ------------------------------------------


def npz(path: Path, ids: list[int], vectors: np.ndarray, model: str, dim: int) -> Path:
    """Stand in for the file cluster/embed_chunks.py writes."""
    np.savez(path, ids=np.asarray(ids, dtype=np.int64), vectors=vectors, model=model, dim=dim)
    return path


def vectors_for(ids: list[int], dim: int = 384) -> np.ndarray:
    return np.array([[float(i)] * dim for i in ids], dtype=np.float32)


def read_export(path: Path) -> tuple[dict, list[dict]]:
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return lines[0], lines[1:]


def test_export_writes_a_header_then_every_pending_chunk(
    conn: sqlite3.Connection, settings: Settings, tmp_path: Path
) -> None:
    first = add_chunk(conn, "one")
    second = add_chunk(conn, "two")

    report = export_pending(conn, tmp_path / "pending.jsonl")
    assert report.chunks == 2

    header, records = read_export(tmp_path / "pending.jsonl")
    assert header["model"] == settings.embedding_model
    assert header["dim"] == settings.embedding_dim
    assert [r["id"] for r in records] == [first, second]
    assert [r["text"] for r in records] == ["one", "two"]


def test_export_skips_chunks_that_already_have_a_vector(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    add_chunk(conn, "done")
    pending = add_chunk(conn, "pending")
    conn.execute("UPDATE chunks SET vector_row = 0 WHERE text = 'done'")
    conn.commit()

    export_pending(conn, tmp_path / "pending.jsonl")
    _, records = read_export(tmp_path / "pending.jsonl")
    assert [r["id"] for r in records] == [pending]


def test_export_changes_nothing(conn: sqlite3.Connection, tmp_path: Path) -> None:
    add_chunk(conn, "one")
    export_pending(conn, tmp_path / "pending.jsonl")
    assert conn.execute("SELECT count(*) FROM chunks WHERE vector_row IS NULL").fetchone()[0] == 1


def test_import_assigns_rows_and_appends_vectors(
    conn: sqlite3.Connection, settings: Settings, tmp_path: Path
) -> None:
    ids = [add_chunk(conn, "one"), add_chunk(conn, "two")]
    path = npz(
        tmp_path / "v.npz",
        ids,
        vectors_for(ids, settings.embedding_dim),
        settings.embedding_model,
        settings.embedding_dim,
    )

    report = import_vectors(conn, path)
    assert (report.imported, report.total_rows) == (2, 2)

    rows = dict(conn.execute("SELECT id, vector_row FROM chunks"))
    assert sorted(rows.values()) == [0, 1]
    matrix = load_vectors(settings)
    assert matrix[rows[ids[0]]][0] == float(ids[0])


def test_import_is_safe_to_run_twice(
    conn: sqlite3.Connection, settings: Settings, tmp_path: Path
) -> None:
    ids = [add_chunk(conn, "one")]
    path = npz(
        tmp_path / "v.npz",
        ids,
        vectors_for(ids, settings.embedding_dim),
        settings.embedding_model,
        settings.embedding_dim,
    )

    import_vectors(conn, path)
    again = import_vectors(conn, path)
    assert (again.imported, again.already_embedded, again.total_rows) == (0, 1, 1)


def test_import_counts_chunks_it_does_not_recognise(
    conn: sqlite3.Connection, settings: Settings, tmp_path: Path
) -> None:
    ids = [add_chunk(conn, "one")]
    path = npz(
        tmp_path / "v.npz",
        [*ids, 9999],
        vectors_for([*ids, 9999], settings.embedding_dim),
        settings.embedding_model,
        settings.embedding_dim,
    )

    report = import_vectors(conn, path)
    assert (report.imported, report.unknown) == (1, 1)


def test_import_refuses_another_models_vectors(
    conn: sqlite3.Connection, settings: Settings, tmp_path: Path
) -> None:
    ids = [add_chunk(conn, "one")]
    path = npz(tmp_path / "v.npz", ids, vectors_for(ids, 1024), "voyage-3.5", 1024)

    with pytest.raises(EmbeddingError, match="cannot be compared"):
        import_vectors(conn, path)
    assert conn.execute("SELECT vector_row FROM chunks").fetchone()[0] is None


def test_import_refuses_a_truncated_transfer(
    conn: sqlite3.Connection, settings: Settings, tmp_path: Path
) -> None:
    ids = [add_chunk(conn, "one"), add_chunk(conn, "two")]
    path = npz(
        tmp_path / "v.npz",
        ids,
        vectors_for(ids[:1], settings.embedding_dim),
        settings.embedding_model,
        settings.embedding_dim,
    )

    with pytest.raises(EmbeddingError, match="incomplete"):
        import_vectors(conn, path)


def test_import_refuses_nan(conn: sqlite3.Connection, settings: Settings, tmp_path: Path) -> None:
    ids = [add_chunk(conn, "one")]
    broken = vectors_for(ids, settings.embedding_dim)
    broken[0][0] = np.nan
    path = npz(tmp_path / "v.npz", ids, broken, settings.embedding_model, settings.embedding_dim)

    with pytest.raises(EmbeddingError, match="NaN"):
        import_vectors(conn, path)


def test_import_names_what_the_file_is_missing(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "v.npz"
    np.savez(path, ids=np.zeros(0, dtype=np.int64))

    with pytest.raises(EmbeddingError, match="missing dim, model, vectors"):
        import_vectors(conn, path)


def test_export_then_import_round_trips_through_the_cluster_script(
    conn: sqlite3.Connection, settings: Settings, tmp_path: Path
) -> None:
    """The two halves have to agree on the file format, so exercise both."""
    ids = [add_chunk(conn, "one"), add_chunk(conn, "two")]
    export = tmp_path / "pending.jsonl"
    export_pending(conn, export)

    header, records = read_export(export)
    out = npz(
        tmp_path / "v.npz",
        [r["id"] for r in records],
        vectors_for([r["id"] for r in records], header["dim"]),
        header["model"],
        header["dim"],
    )

    assert import_vectors(conn, out).imported == 2
    assert export_pending(conn, tmp_path / "again.jsonl").chunks == 0
    assert sorted(dict(conn.execute("SELECT id, vector_row FROM chunks")).keys()) == sorted(ids)
