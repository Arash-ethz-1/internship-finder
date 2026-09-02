"""Phase 1 checks: the package imports, settings resolve, the schema is sound."""

from __future__ import annotations

import sqlite3

import pytest

from agent_app import cli
from agent_app.config import ConfigError, Settings
from agent_app.db import RETIRED_STATUSES, STATUSES, TRACKED_STATUSES, table_names


def test_settings_paths_live_under_the_data_dir(settings: Settings) -> None:
    assert settings.db_path.parent == settings.data_dir
    assert settings.vectors_path.parent == settings.data_dir
    assert settings.embed_cache_dir.parent == settings.data_dir


def test_missing_keys_raise_a_message_naming_the_variable(settings: Settings) -> None:
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        settings.require_anthropic_key()
    with pytest.raises(ConfigError, match="VOYAGE_API_KEY"):
        settings.require_voyage_key()


def test_init_db_creates_every_table(conn: sqlite3.Connection) -> None:
    assert table_names(conn) == [
        "applications",
        "chunks",
        "companies",
        "email_matches",
        "posting_locations",
        "postings",
        "status_history",
    ]


# PLAN.md's own list, minus the one status that has since been retired.
PLAN_STATUSES = {
    "interested",
    "applied",
    "rejected",
    "interviewing",
    "offer",
    "declined",
}

# Retired 2026-09-02: `ready_to_submit` described a state of the author's
# intent rather than of the world, and "interested but not yet sent" already
# covered it. The letter-is-written case it stood for is a filter --
# `letter_path IS NOT NULL AND status = 'interested'` -- not a status.
RETIRED_FROM_PLAN = {"ready_to_submit"}


def test_the_plans_status_set_is_still_all_there() -> None:
    assert PLAN_STATUSES <= set(STATUSES)


def test_retired_statuses_are_really_gone() -> None:
    """A retired status must not come back by accident.

    It also must stay in `RETIRED_STATUSES`, because that mapping is what
    `migrate()` uses to move an existing application off it -- deleting the
    entry would strand any row still holding the old value.
    """
    assert RETIRED_FROM_PLAN & set(STATUSES) == set()
    assert RETIRED_FROM_PLAN <= set(RETIRED_STATUSES)


def test_the_additions_to_the_plan_are_the_documented_two() -> None:
    """`found` (2026-09-01) and `not_relevant` (2026-09-02).

    `not_relevant` exists because "not for me" was writing `rejected`, which
    says a company turned you down. Kept as its own test so a third
    undocumented status is still caught.
    """
    assert set(STATUSES) - PLAN_STATUSES == {"found", "not_relevant"}


def test_untouched_postings_are_not_tracked_statuses() -> None:
    """The exclusion the email matcher depends on.

    A posting a search merely surfaced, or one you passed on without ever
    applying, must never be a candidate for "your application was rejected",
    because no application was ever sent.
    """
    assert "found" not in TRACKED_STATUSES
    assert "not_relevant" not in TRACKED_STATUSES
    assert set(TRACKED_STATUSES) == PLAN_STATUSES


def _insert_posting(conn: sqlite3.Connection, posting_id: str = "greenhouse:1") -> str:
    conn.execute(
        "INSERT INTO postings (id, source, company, title, url, body, body_hash, "
        "first_seen, last_seen) VALUES (?, 'greenhouse', 'Acme', 'Intern', "
        "'https://example.com', 'body', 'hash', '2026-01-01T00:00:00Z', "
        "'2026-01-01T00:00:00Z')",
        (posting_id,),
    )
    conn.commit()
    return posting_id


def test_chunk_must_belong_to_exactly_one_owner(conn: sqlite3.Connection) -> None:
    posting_id = _insert_posting(conn)

    # Neither owner set.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO chunks (ordinal, text) VALUES (0, 'x')")

    # Both owners set.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO chunks (posting_id, profile_doc, ordinal, text) VALUES (?, ?, 0, 'x')",
            (posting_id, "some-doc"),
        )

    # Either one alone is fine.
    conn.execute("INSERT INTO chunks (posting_id, ordinal, text) VALUES (?, 0, 'x')", (posting_id,))
    conn.execute("INSERT INTO chunks (profile_doc, ordinal, text) VALUES ('doc', 0, 'y')")
    conn.commit()

    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 2


def test_two_chunks_cannot_share_a_vector_row(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO chunks (profile_doc, ordinal, text, vector_row) VALUES ('a', 0, 'x', 0)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO chunks (profile_doc, ordinal, text, vector_row) VALUES ('b', 0, 'y', 0)"
        )
    # NULL vector_row is still allowed for any number of unembedded chunks.
    conn.execute("INSERT INTO chunks (profile_doc, ordinal, text) VALUES ('c', 0, 'z')")
    conn.execute("INSERT INTO chunks (profile_doc, ordinal, text) VALUES ('d', 0, 'w')")
    conn.commit()


def test_deleting_a_posting_takes_its_chunks_with_it(conn: sqlite3.Connection) -> None:
    posting_id = _insert_posting(conn)
    conn.execute("INSERT INTO chunks (posting_id, ordinal, text) VALUES (?, 0, 'x')", (posting_id,))
    conn.commit()
    conn.execute("DELETE FROM postings WHERE id = ?", (posting_id,))
    conn.commit()
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0


def test_cli_init_db_reports_the_tables(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["init-db"]) == 0
    out = capsys.readouterr().out
    assert "postings" in out
    assert "status_history" in out


def test_cli_with_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 1
    assert "usage:" in capsys.readouterr().out


def test_api_app_exposes_health() -> None:
    from agent_app.api.main import create_app

    app = create_app()
    routes = {getattr(r, "path", None) for r in app.routes}
    assert "/api/health" in routes
