"""Bringing an existing database up to the current schema.

There is no migration framework here on purpose -- one database, one machine --
so these tests are what stands in for one.
"""

from __future__ import annotations

import sqlite3

from agent_app.db import connect, init_db, migrate, now_iso


def _legacy_db(path: str) -> sqlite3.Connection:
    """A database as it stood before `closed_at` and `posting_locations`."""
    conn = connect(path)
    conn.executescript(
        """
        CREATE TABLE postings (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, company TEXT NOT NULL,
            title TEXT NOT NULL, location TEXT, remote INTEGER NOT NULL DEFAULT 0,
            url TEXT NOT NULL, body TEXT NOT NULL, body_hash TEXT NOT NULL,
            posted_at TEXT, deadline TEXT, level TEXT NOT NULL DEFAULT 'unknown',
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
        );
        CREATE TABLE applications (
            posting_id TEXT PRIMARY KEY, status TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '', letter_path TEXT, updated_at TEXT NOT NULL
        );
        CREATE TABLE status_history (
            id INTEGER PRIMARY KEY, posting_id TEXT NOT NULL, from_status TEXT,
            to_status TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', changed_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    return conn


def _add_application(conn: sqlite3.Connection, posting_id: str, status: str) -> None:
    conn.execute(
        "INSERT INTO postings (id, source, company, title, url, body, body_hash, "
        "first_seen, last_seen) VALUES (?, 'greenhouse', 'Acme', 'Intern', "
        "'https://example.com', 'body', 'h', ?, ?)",
        (posting_id, now_iso(), now_iso()),
    )
    conn.execute(
        "INSERT INTO applications (posting_id, status, updated_at) VALUES (?, ?, ?)",
        (posting_id, status, now_iso()),
    )
    conn.commit()


def test_an_old_database_gains_the_new_column_and_table(tmp_path) -> None:
    """The ordering this pins is the one that bit: an index over a new column
    is created by the schema script, which runs *after* the column is added."""
    path = str(tmp_path / "legacy.db")
    conn = _legacy_db(path)
    assert "closed_at" not in {r["name"] for r in conn.execute("PRAGMA table_info(postings)")}

    init_db(conn)

    assert "closed_at" in {r["name"] for r in conn.execute("PRAGMA table_info(postings)")}
    tables = {
        r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "posting_locations" in tables


def test_a_retired_status_is_moved_and_the_move_is_recorded(tmp_path) -> None:
    """A status disappearing from the app must not make the change to the
    pipeline invisible. The same rule was applied when `not_relevant` arrived.
    """
    conn = _legacy_db(str(tmp_path / "legacy.db"))
    _add_application(conn, "greenhouse:1", "ready_to_submit")
    _add_application(conn, "greenhouse:2", "applied")

    init_db(conn)

    statuses = {
        row["posting_id"]: row["status"] for row in conn.execute("SELECT * FROM applications")
    }
    assert statuses == {"greenhouse:1": "interested", "greenhouse:2": "applied"}

    history = conn.execute(
        "SELECT from_status, to_status, note FROM status_history WHERE posting_id = ?",
        ("greenhouse:1",),
    ).fetchall()
    assert len(history) == 1
    assert history[0]["from_status"] == "ready_to_submit"
    assert history[0]["to_status"] == "interested"
    assert "retired" in history[0]["note"]


def test_migrating_twice_changes_nothing_the_second_time(tmp_path) -> None:
    conn = _legacy_db(str(tmp_path / "legacy.db"))
    _add_application(conn, "greenhouse:1", "ready_to_submit")

    init_db(conn)
    before = conn.execute("SELECT count(*) FROM status_history").fetchone()[0]

    assert migrate(conn) == []
    after = conn.execute("SELECT count(*) FROM status_history").fetchone()[0]
    assert before == after == 1


def test_a_fresh_database_needs_no_migration(tmp_path) -> None:
    conn = connect(str(tmp_path / "fresh.db"))
    init_db(conn)
    assert migrate(conn) == []
