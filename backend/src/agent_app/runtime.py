"""Process-wide resources, reachable without passing them as arguments.

PLAN.md fixes the Category B signatures ``search(query, filters, k)`` and
``run_agent(user_message, history, max_iters)``. Neither takes a database
handle, so the resources have to be reachable from anywhere. That is what this
module is for::

    from ..runtime import get_db

    def search(query: str, filters: SearchFilters, k: int = 10) -> list[SearchHit]:
        conn = get_db()
        ...

Connections are per-thread, not one global. FastAPI runs sync endpoints in a
threadpool, and a single sqlite3 connection shared across threads is a bug
waiting to happen; a thread-local one keeps the zero-argument accessor above
while staying safe.

``get_vectors()`` caches the whole array in memory. At a few thousand chunks
that is a handful of megabytes and makes brute-force cosine essentially free;
anything that changes the array must call :func:`reset_vectors` so the next
search sees it.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import TYPE_CHECKING

from .config import get_settings
from .db import connect, init_db

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, fine for types
    import numpy as np

    from .core.bm25_index import Bm25Index
    from .core.embeddings import EmbeddingProvider

_local = threading.local()

# Process-wide, not per-thread: the provider is stateless and the vector matrix
# is read-only once loaded, so sharing them across request threads is safe.
_provider: EmbeddingProvider | None = None
_vectors: np.ndarray | None = None
_bm25: Bm25Index | None = None


def get_db() -> sqlite3.Connection:
    """Return this thread's connection, opening and initialising it on first use."""
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is None:
        settings = get_settings()
        settings.ensure_dirs()
        conn = connect(settings.db_path)
        init_db(conn)
        _local.conn = conn
    return conn


def close_db() -> None:
    """Close this thread's connection if it has one. Mainly for tests and the CLI."""
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def get_provider() -> EmbeddingProvider:
    """Return the configured embedding provider, building it on first use.

    Deferred so that importing this module never requires an API key: only the
    code that actually embeds needs one.
    """
    global _provider
    if _provider is None:
        from .core.embeddings import build_provider

        _provider = build_provider(get_settings())
    return _provider


def get_vectors() -> np.ndarray:
    """Return the whole vector matrix, loading vectors.npy on first use.

    Shape is ``(n_chunks_embedded, dim)``. A chunk's row is its ``vector_row``.
    """
    global _vectors
    if _vectors is None:
        from .core.embeddings import load_vectors

        _vectors = load_vectors(get_settings())
    return _vectors


def reset_vectors() -> None:
    """Drop the cached matrix. Anything that writes vectors.npy must call this."""
    global _vectors
    _vectors = None


def get_bm25_index() -> Bm25Index:
    """Return the inverted index BM25 scores against, loading it on first use.

    Read from ``data/bm25.npz`` when it still describes the chunks table, and
    rebuilt from the corpus when it does not. Rebuilding tokenises everything,
    so it is slow and rare; loading is a handful of numpy arrays off disk.
    """
    global _bm25
    if _bm25 is None:
        from .core.bm25_index import get_or_build

        _bm25 = get_or_build(get_db(), get_settings())
    return _bm25


def reset_bm25_index() -> None:
    """Drop the cached index. Anything that changes the chunks table calls this."""
    global _bm25
    _bm25 = None


def reset() -> None:
    """Drop every cached resource. Tests use this between databases."""
    global _provider
    close_db()
    reset_vectors()
    reset_bm25_index()
    _provider = None
