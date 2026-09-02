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
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
import numpy as np

from ..config import PROVIDER_DEFAULTS, Settings, get_settings

log = logging.getLogger(__name__)

BATCH_SIZE = 64
MAX_ATTEMPTS = 5
# Rows embedded between two writes of vectors.npy. See embed_all_pending.
CHECKPOINT_ROWS = 4096
VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"

# How many texts to hand fastembed at once. Larger than the Voyage batch
# because there is no request to time out: this only trades memory for a
# slightly better-packed ONNX call.
LOCAL_BATCH_SIZE = 256

# Some local models were trained with a fixed instruction glued to the front of
# every text and score badly without it. The provider owns that detail so the
# model name stays the only thing anyone configures.
#
# These are the *symmetric* prefixes: retrieval reaches the provider through
# one ``embed`` method that cannot tell a query from a document, so a query and
# a passage get the same prefix. E5's authors document exactly this fallback
# for symmetric use. Asymmetric prefixes would be better and need a second
# method on the Protocol -- see PROGRESS.md.
LOCAL_MODEL_PREFIXES: dict[str, str] = {
    "intfloat/multilingual-e5-large": "query: ",
}


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


@dataclass
class LocalProvider:
    """Embeddings from a model running on this machine. No key, no rate limit.

    The reason this exists is not cost but iteration. Tuning retrieval means
    re-chunking and re-embedding repeatedly, and a corpus this size burns a
    free allowance in a couple of passes. Locally, the tenth experiment costs
    the same as the first: nothing.

    ONNX through ``fastembed`` rather than ``sentence-transformers``, because
    the latter pulls PyTorch (~2.5 GB) to do the same job. Only models
    ``fastembed`` publishes an ONNX build for can be named here;
    :meth:`_load` prints the list when the name is wrong.

    Slower than the API, and how much slower depends entirely on the model:
    the 384-dimension default runs a few hundred chunks a second on a laptop
    CPU, ``intfloat/multilingual-e5-large`` perhaps a dozen.
    ``embed_all_pending`` checkpoints as it goes, so a long run that is
    interrupted resumes where it stopped.
    """

    model: str = PROVIDER_DEFAULTS["local"][0]
    dim: int = PROVIDER_DEFAULTS["local"][1]
    batch_size: int = LOCAL_BATCH_SIZE
    _embedder: Any = field(default=None, repr=False, compare=False)

    @property
    def prefix(self) -> str:
        """The instruction this model expects in front of every text."""
        return LOCAL_MODEL_PREFIXES.get(self.model, "")

    def _load(self) -> Any:
        """Load the model once, on first use.

        Deferred so that importing this module -- which the CLI and the API
        both do at startup -- never pays for loading a model that most
        commands do not need. The first call for a given model downloads it.
        """
        if self._embedder is not None:
            return self._embedder

        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise EmbeddingError(
                "EMBEDDING_PROVIDER=local needs fastembed. Run `uv sync`."
            ) from exc

        log.info("loading %s (the first run downloads it)", self.model)
        try:
            with warnings.catch_warnings():
                # fastembed >= 0.6 warns that it switched this model from CLS
                # to mean pooling. Mean pooling is what sentence-transformers
                # does for it, so the new behaviour is the right one and the
                # warning is noise on every single load.
                warnings.filterwarnings("ignore", message=".*mean pooling.*")
                self._embedder = TextEmbedding(model_name=self.model)
        except ValueError as exc:
            names = sorted(m["model"] for m in TextEmbedding.list_supported_models())
            raise EmbeddingError(
                f"fastembed has no ONNX build of {self.model!r}. "
                "Set EMBEDDING_MODEL to one of: " + ", ".join(names)
            ) from exc
        return self._embedder

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)

        prefix = self.prefix
        prepared = [prefix + text for text in texts] if prefix else texts

        embedder = self._load()
        matrix = np.array(
            list(embedder.embed(prepared, batch_size=self.batch_size)), dtype=np.float32
        )
        if matrix.shape != (len(texts), self.dim):
            raise EmbeddingError(
                f"Expected {(len(texts), self.dim)} from {self.model}, got {matrix.shape}. "
                f"If the model is right, set EMBEDDING_DIM={matrix.shape[1]}."
            )
        return matrix


def build_provider(settings: Settings | None = None) -> EmbeddingProvider:
    """The configured provider. The only place a concrete one is chosen.

    ``EMBEDDING_PROVIDER=local`` runs the model on this machine and needs no
    key; anything else uses Voyage. Switching provider changes the vector
    space, so ``vectors.meta.json`` will refuse to load an array written by
    the other one -- which is the point.
    """
    settings = settings or get_settings()

    if settings.embedding_provider == "local":
        return LocalProvider(model=settings.embedding_model, dim=settings.embedding_dim)

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
    batch_rows: int = CHECKPOINT_ROWS,
    progress: Callable[[int, int], None] | None = None,
) -> EmbedReport:
    """Give every chunk without a ``vector_row`` one.

    Vectors are appended to ``vectors.npy`` and the row indices written back in
    one transaction. The array is saved *before* the database is updated: an
    interrupted run then leaves unreferenced rows in the array, which
    :func:`rebuild_vectors` cleans up, rather than database rows pointing at
    vectors that were never written.

    ``batch_rows`` is the *checkpoint* size, not the request size -- each
    provider batches its own calls. It is a straight trade: every checkpoint
    rewrites the whole of ``vectors.npy``, so a small one means a long run
    resumes closer to where it stopped and a large one means far less disk
    written. At 135,000 chunks the difference between checkpointing every 512
    rows and every 4,096 is tens of gigabytes of writes.

    ``progress`` is called with ``(done, total)`` after each checkpoint, so a
    run that takes twenty minutes can say so.
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
        if progress is not None:
            progress(report.embedded, len(rows))

    report.cache_hits = cache.hits
    report.total_rows = int(matrix.shape[0])
    return report


# --- embedding somewhere else ----------------------------------------------
#
# The laptop this is developed on manages about two chunks a second, which is
# most of a day for the corpus. A borrowed machine does it in minutes. Neither
# the database nor the repo needs to travel for that: what has to cross is the
# chunk text going out and the vectors coming back, so that is exactly what
# these two functions move.
#
# The model travels with the text. A vector is only comparable to another
# vector from the same model, and the surest way to keep two machines agreed
# on which model that is, is to never ask the second one.

EXPORT_VERSION = 1


@dataclass
class ExportReport:
    """What one ``cli embed --export`` wrote."""

    chunks: int
    path: Path
    model: str

    def format(self) -> str:
        if self.chunks == 0:
            return "0 pending. Nothing to export."
        return (
            f"{self.chunks:,} chunk(s) written to {self.path} for {self.model}.\n"
            f"Embed them elsewhere, then: cli embed --import <file>.npz"
        )


@dataclass
class ImportReport:
    """What one ``cli embed --import`` took in."""

    offered: int = 0
    imported: int = 0
    already_embedded: int = 0
    unknown: int = 0
    total_rows: int = 0

    def format(self) -> str:
        parts = [f"{self.imported:,} chunk(s) imported"]
        if self.already_embedded:
            parts.append(f"{self.already_embedded:,} already had a vector")
        if self.unknown:
            parts.append(f"{self.unknown:,} not in the chunks table")
        return ", ".join(parts) + f". {self.total_rows:,} vector(s) stored."


def export_pending(
    conn: sqlite3.Connection,
    path: Path,
    settings: Settings | None = None,
    *,
    limit: int | None = None,
) -> ExportReport:
    """Write every chunk without a ``vector_row`` to a JSONL file.

    The first line is a header naming the model the vectors must come back
    from; every line after it is ``{"id": ..., "text": ...}``. JSONL rather
    than a single JSON document so the far end can stream a file larger than
    its memory, and so a truncated transfer is obvious.

    Nothing in the database changes. The export is a request, not a promise:
    exporting twice and importing once is fine, and so is never importing.
    """
    settings = settings or get_settings()

    sql = "SELECT id, text FROM chunks WHERE vector_row IS NULL ORDER BY id"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        header = {
            "version": EXPORT_VERSION,
            "model": settings.embedding_model,
            "dim": settings.embedding_dim,
        }
        handle.write(json.dumps(header) + "\n")
        for row in conn.execute(sql):
            handle.write(json.dumps({"id": row["id"], "text": row["text"]}) + "\n")
            count += 1

    return ExportReport(chunks=count, path=path, model=settings.embedding_model)


def import_vectors(
    conn: sqlite3.Connection,
    path: Path,
    settings: Settings | None = None,
) -> ImportReport:
    """Append vectors embedded on another machine and give their chunks a row.

    Expects the ``.npz`` written by ``cluster/embed_chunks.py``: ``ids``,
    ``vectors``, ``model`` and ``dim``. Everything about it is checked before a
    single row is written, because a file that has crossed a machine boundary
    is the one place a wrong vector space or a NaN can enter unnoticed --
    neither of which search would report, they would just rank badly.

    Chunks that already have a vector are skipped rather than duplicated, so a
    re-run of a half-applied import finishes the job.
    """
    settings = settings or get_settings()
    settings.ensure_dirs()

    with np.load(path, allow_pickle=False) as payload:
        missing = {"ids", "vectors", "model", "dim"} - set(payload.files)
        if missing:
            raise EmbeddingError(
                f"{path.name} is missing {', '.join(sorted(missing))}. "
                "It should be the .npz written by cluster/embed_chunks.py."
            )
        ids = np.asarray(payload["ids"]).astype(np.int64, copy=False)
        vectors = np.asarray(payload["vectors"]).astype(np.float32, copy=False)
        model = str(payload["model"])
        dim = int(payload["dim"])

    if model != settings.embedding_model or dim != settings.embedding_dim:
        raise EmbeddingError(
            f"{path.name} was written by {model} (dim {dim}) but the configured model is "
            f"{settings.embedding_model} (dim {settings.embedding_dim}). Vectors from two "
            "models cannot be compared. Set EMBEDDING_MODEL and EMBEDDING_DIM to match, or "
            "re-export and re-embed."
        )
    if vectors.shape != (len(ids), dim):
        raise EmbeddingError(
            f"{path.name} has {len(ids)} id(s) but vectors of shape {vectors.shape}. "
            "The transfer is incomplete."
        )
    if not np.isfinite(vectors).all():
        raise EmbeddingError(
            f"{path.name} contains NaN or infinity. Cosine similarity would not complain "
            "about that, it would just rank badly, so the import stops here."
        )

    report = ImportReport(offered=len(ids), total_rows=0)

    # The whole id -> vector_row map in one query. An `IN (...)` over 135,000
    # ids would blow past SQLite's parameter limit, and the table is small
    # enough that reading it costs less than batching around that.
    rows = conn.execute("SELECT id, vector_row FROM chunks")
    known: dict[int, int | None] = {int(row["id"]): row["vector_row"] for row in rows}

    take: list[int] = []
    for position, chunk_id in enumerate(int(i) for i in ids):
        if chunk_id not in known:
            report.unknown += 1
        elif known[chunk_id] is not None:
            report.already_embedded += 1
        else:
            take.append(position)

    matrix = load_vectors(settings)
    report.total_rows = int(matrix.shape[0])
    if not take:
        return report

    fresh = vectors[take]
    first_row = int(matrix.shape[0])
    matrix = np.vstack([matrix, fresh]) if matrix.size else fresh
    save_vectors(matrix, settings)

    with conn:
        for offset, position in enumerate(take):
            conn.execute(
                "UPDATE chunks SET vector_row = ? WHERE id = ?",
                (first_row + offset, int(ids[position])),
            )

    report.imported = len(take)
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
