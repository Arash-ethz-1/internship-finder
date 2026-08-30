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

Later phases add ``get_provider()`` (Phase 4) and ``get_vectors()`` (Phase 4)
here alongside ``get_db()``.
"""

from __future__ import annotations

import sqlite3
import threading

from .config import get_settings
from .db import connect, init_db

_local = threading.local()


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
