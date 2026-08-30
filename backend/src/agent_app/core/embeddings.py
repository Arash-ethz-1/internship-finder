"""Turning text into vectors, and keeping vectors.npy in step with the database.

Category A, all of it. This module never chunks and never searches: it takes
whatever rows are sitting in ``chunks`` without a ``vector_row`` and gives them
one.

Three things make it cheap to re-run during development, which matters because
you will re-run it constantly while tuning chunking:

* an on-disk cache keyed by ``sha256(model + text)``, so re-embedding text you
  have already embedded costs nothing
* batching, so 5,000 chunks is ~80 requests rather than 5,000
* a sidecar recording which model wrote ``vectors.npy``, so switching provider
  fails loudly instead of silently mixing two vector spaces
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
import numpy as np

from ..config import Settings, get_settings

log = logging.getLogger(__name__)

BATCH_SIZE = 64
MAX_ATTEMPTS = 5
VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"


class EmbeddingError(RuntimeError):
    """Embedding could not be completed."""


class EmbeddingProvider(Protocol):
    """Anything that can turn text into vectors.

    Kept deliberately small so a local model can be swapped in later without
    touching anything else.
    """

    model: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return shape ``(len(texts), dim)``, float32, in the same order."""
        ...


class EmbeddingCache:
    """Vectors already paid for, keyed by model and text.

    One file per vector, sharded into 256 directories by the first byte of the
    hash — a single directory holding 100,000 files is slow on every OS.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.hits = 0
        self.misses = 0

    def key(self, model: str, text: str) -> str:
        return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()

    def path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.npy"

    def get(self, model: str, text: str) -> np.ndarray | None:
        path = self.path(self.key(model, text))
        if not path.exists():
            self.misses += 1
            return None
        try:
            vector = np.load(path)
        except (ValueError, OSError):
            # A half-written file from an interrupted run is not worth crashing
            # over; treat it as a miss and overwrite it.
            log.warning("discarding unreadable cache entry %s", path.name)
            self.misses += 1
            return None
        self.hits += 1
        return vector.astype(np.float32, copy=False)

    def put(self, model: str, text: str, vector: np.ndarray) -> None:
        path = self.path(self.key(model, text))
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write beside and rename, so an interrupted run cannot leave a
        # truncated file that later loads as a wrong vector.
        temporary = path.with_suffix(".tmp.npy")
        np.save(temporary, vector.astype(np.float32, copy=False))
        temporary.replace(path)


@dataclass
class VoyageProvider:
    """Voyage AI embeddings over plain HTTP.

    No vendor SDK: PLAN.md asks for batching and backoff to be written here,
    and a client that already does both would make this module a wrapper around
    something invisible.
    """

    api_key: str
    model: str = "voyage-3.5"
    dim: int = 1024
    timeout: float = 60.0
    transport: httpx.BaseTransport | None = None
    sleep: Any = time.sleep

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)

        with httpx.Client(
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
            transport=self.transport,
        ) as client:
            vectors: list[np.ndarray] = []
            for start in range(0, len(texts), BATCH_SIZE):
                vectors.append(self._embed_batch(client, texts[start : start + BATCH_SIZE]))

        matrix = np.vstack(vectors).astype(np.float32, copy=False)
        if matrix.shape != (len(texts), self.dim):
            raise EmbeddingError(
                f"Expected {(len(texts), self.dim)} from {self.model}, got {matrix.shape}"
            )
        return matrix

    def _embed_batch(self, client: httpx.Client, batch: list[str]) -> np.ndarray:
        payload = {"input": batch, "model": self.model, "input_type": "document"}
        last_error = "unknown error"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = client.post(VOYAGE_URL, json=payload)
            except httpx.RequestError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    return self._parse(response.json(), len(batch))
                if response.status_code in (429, 500, 502, 503, 504):
                    last_error = f"HTTP {response.status_code}"
                else:
                    # 401 and 400 will not fix themselves by waiting.
                    raise EmbeddingError(
                        f"HTTP {response.status_code} from Voyage: {response.text[:200]}"
                    )

            if attempt < MAX_ATTEMPTS:
                backoff = 2 ** (attempt - 1)
                log.warning(
                    "embedding attempt %d/%d failed (%s); retrying in %ds",
                    attempt,
                    MAX_ATTEMPTS,
                    last_error,
                    backoff,
                )
                self.sleep(backoff)

        raise EmbeddingError(f"Voyage failed after {MAX_ATTEMPTS} attempts ({last_error})")

    def _parse(self, payload: Any, expected: int) -> np.ndarray:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != expected:
            raise EmbeddingError(f"Voyage returned {len(data or [])} vectors, expected {expected}")
        # The API documents that `index` mirrors input order, but sorting by it
        # is free insurance against a silent misalignment.
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        return np.array([item["embedding"] for item in ordered], dtype=np.float32)


def build_provider(settings: Settings | None = None) -> EmbeddingProvider:
    """The configured provider. The only place a concrete one is chosen."""
    settings = settings or get_settings()
    return VoyageProvider(
        api_key=settings.require_voyage_key(),
        model=settings.embedding_model,
        dim=settings.embedding_dim,
    )


@dataclass(frozen=True)
class VectorMeta:
    """What wrote vectors.npy.

    Not in PLAN.md, but without it swapping the embedding provider corrupts
    search silently instead of failing.
    """

    model: str
    dim: int
    rows: int

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "dim": self.dim, "rows": self.rows}


def read_meta(path: Path) -> VectorMeta | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return VectorMeta(model=raw["model"], dim=int(raw["dim"]), rows=int(raw["rows"]))
    except (ValueError, KeyError, OSError):
        return None


def write_meta(path: Path, meta: VectorMeta) -> None:
    path.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")


def load_vectors(settings: Settings | None = None) -> np.ndarray:
    """Read vectors.npy, refusing to load one written by a different model."""
    settings = settings or get_settings()
    if not settings.vectors_path.exists():
        return np.zeros((0, settings.embedding_dim), dtype=np.float32)

    meta = read_meta(settings.vectors_meta_path)
    if meta and (meta.model != settings.embedding_model or meta.dim != settings.embedding_dim):
        raise EmbeddingError(
            f"{settings.vectors_path.name} was written by {meta.model} (dim {meta.dim}) but the "
            f"configured model is {settings.embedding_model} (dim {settings.embedding_dim}). "
            "Vectors from two models cannot be compared. Delete data/vectors.npy and "
            "data/vectors.meta.json, then re-run `cli embed`."
        )

    return np.load(settings.vectors_path).astype(np.float32, copy=False)


def save_vectors(matrix: np.ndarray, settings: Settings | None = None) -> None:
    """Write vectors.npy and its sidecar together."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    np.save(settings.vectors_path, matrix.astype(np.float32, copy=False))
    write_meta(
        settings.vectors_meta_path,
        VectorMeta(
            model=settings.embedding_model,
            dim=settings.embedding_dim,
            rows=int(matrix.shape[0]),
        ),
    )


def embed_texts(
    provider: EmbeddingProvider,
    texts: list[str],
    cache: EmbeddingCache,
) -> np.ndarray:
    """Embed texts, consulting the cache first and only paying for the rest."""
    if not texts:
        return np.zeros((0, provider.dim), dtype=np.float32)

    vectors: list[np.ndarray | None] = []
    missing: list[str] = []
    missing_at: list[int] = []

    for index, text in enumerate(texts):
        cached = cache.get(provider.model, text)
        vectors.append(cached)
        if cached is None:
            missing.append(text)
            missing_at.append(index)

    if missing:
        log.info("embedding %d text(s), %d served from cache", len(missing), cache.hits)
        fresh = provider.embed(missing)
        for offset, index in enumerate(missing_at):
            vector = fresh[offset]
            vectors[index] = vector
            cache.put(provider.model, texts[index], vector)

    return np.vstack([v for v in vectors if v is not None]).astype(np.float32, copy=False)


@dataclass
class EmbedReport:
    """What one ``cli embed`` run did."""

    pending: int = 0
    embedded: int = 0
    cache_hits: int = 0
    total_rows: int = 0

    def format(self) -> str:
        if self.pending == 0:
            return f"0 pending. {self.total_rows:,} vector(s) already stored."
        return (
            f"{self.embedded:,} chunk(s) embedded "
            f"({self.cache_hits:,} from cache). "
            f"{self.total_rows:,} vector(s) stored."
        )


def embed_all_pending(
    conn: sqlite3.Connection,
    provider: EmbeddingProvider | None = None,
    settings: Settings | None = None,
    *,
    batch_rows: int = 512,
) -> EmbedReport:
    """Give every chunk without a ``vector_row`` one.

    Vectors are appended to ``vectors.npy`` and the row indices written back in
    one transaction. The array is saved *before* the database is updated: an
    interrupted run then leaves unreferenced rows in the array, which
    :func:`rebuild_vectors` cleans up, rather than database rows pointing at
    vectors that were never written.
    """
    settings = settings or get_settings()
    settings.ensure_dirs()

    rows = conn.execute(
        "SELECT id, text FROM chunks WHERE vector_row IS NULL ORDER BY id"
    ).fetchall()

    matrix = load_vectors(settings)
    report = EmbedReport(pending=len(rows), total_rows=int(matrix.shape[0]))
    if not rows:
        return report

    provider = provider or build_provider(settings)
    cache = EmbeddingCache(settings.embed_cache_dir)

    for start in range(0, len(rows), batch_rows):
        window = rows[start : start + batch_rows]
        vectors = embed_texts(provider, [row["text"] for row in window], cache)

        first_row = int(matrix.shape[0])
        matrix = np.vstack([matrix, vectors]) if matrix.size else vectors
        save_vectors(matrix, settings)

        with conn:
            for offset, row in enumerate(window):
                conn.execute(
                    "UPDATE chunks SET vector_row = ? WHERE id = ?",
                    (first_row + offset, row["id"]),
                )

        report.embedded += len(window)
        log.info("embedded %d/%d", report.embedded, len(rows))

    report.cache_hits = cache.hits
    report.total_rows = int(matrix.shape[0])
    return report


def rebuild_vectors(
    conn: sqlite3.Connection,
    settings: Settings | None = None,
) -> tuple[int, int]:
    """Compact ``vectors.npy`` and reassign ``vector_row`` after deletions.

    Deleting a posting's chunks leaves its vectors stranded in the array. This
    rewrites the array with only the rows still referenced, in chunk id order,
    and renumbers the database to match. Returns ``(before, after)``.
    """
    settings = settings or get_settings()
    matrix = load_vectors(settings)
    before = int(matrix.shape[0])

    rows = conn.execute(
        "SELECT id, vector_row FROM chunks WHERE vector_row IS NOT NULL ORDER BY id"
    ).fetchall()

    keep = [row["vector_row"] for row in rows]
    if any(index >= before for index in keep):
        raise EmbeddingError(
            "The database references vector rows that do not exist in "
            f"{settings.vectors_path.name}. Delete it and its sidecar, then re-run `cli embed`."
        )

    compacted = matrix[keep] if keep else np.zeros((0, settings.embedding_dim), dtype=np.float32)
    save_vectors(compacted, settings)

    with conn:
        # Cleared first: vector_row has a unique index, so renumbering in place
        # would collide with a row that has not moved yet.
        conn.execute("UPDATE chunks SET vector_row = NULL WHERE vector_row IS NOT NULL")
        for new_index, row in enumerate(rows):
            conn.execute("UPDATE chunks SET vector_row = ? WHERE id = ?", (new_index, row["id"]))

    return (before, int(compacted.shape[0]))
