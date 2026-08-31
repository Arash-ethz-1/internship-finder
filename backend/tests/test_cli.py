"""Phase 9: the CLI surface. Every subcommand is documented and dispatches."""

from __future__ import annotations

import sqlite3

import pytest

from agent_app import cli


def test_help_lists_every_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for command in (
        "init-db",
        "ingest",
        "discover",
        "companies",
        "draft-letter",
        "status",
        "eval",
        "chat",
    ):
        assert command in out, command
    # The two that do not exist yet say so rather than being silently absent.
    assert "embed" in out
    assert "ingest-profile" in out


@pytest.mark.parametrize(
    "command",
    ["init-db", "ingest", "discover", "companies", "draft-letter", "status", "eval", "chat"],
)
def test_every_subcommand_has_its_own_help(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        cli.main([command, "--help"])
    assert "usage:" in capsys.readouterr().out


def test_status_on_an_empty_database(
    conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "No postings yet" in out
    assert "cli ingest" in out


def test_status_summarises_a_populated_database(
    conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    conn.execute(
        "INSERT INTO postings (id, source, company, title, url, body, body_hash, level,"
        " first_seen, last_seen) VALUES ('greenhouse:1', 'greenhouse', 'Acme', 'Intern',"
        " 'https://e.com', 'b', 'h', 'intern', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.commit()

    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "1 postings" in out
    assert "untriaged" in out
    assert "Acme" in out
    # An empty chunks table names the command that fills it.
    assert "cli embed" in out


def test_eval_reports_a_missing_eval_set(
    conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["eval"]) == 2
    assert "relevant_posting_ids" in capsys.readouterr().err


def test_eval_runs_against_an_empty_index(
    conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str], tmp_path
) -> None:
    # Nothing is embedded, so every query retrieves nothing and recall is 0.
    # The harness still has to run and report that, because "0.000" is the
    # baseline every later change is measured against.
    path = tmp_path / "queries.jsonl"
    path.write_text('{"query": "ml internships", "relevant_posting_ids": ["greenhouse:1"]}\n')

    assert cli.main(["eval", "--path", str(path)]) == 0

    out = capsys.readouterr().out
    assert "1 labelled queries" in out
    assert "recall@1" in out


def test_chat_without_an_api_key_explains_itself(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt="": "find me internships")
    assert cli.main(["chat"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_chat_does_not_warn_once_the_descriptions_are_written(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    assert cli.main(["chat"]) == 0
    assert "placeholders" not in capsys.readouterr().out


def test_draft_letter_without_a_profile_corpus_explains_itself(
    conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    conn.execute(
        "INSERT INTO postings (id, source, company, title, url, body, body_hash,"
        " first_seen, last_seen) VALUES ('greenhouse:1', 'greenhouse', 'Acme', 'Intern',"
        " 'https://e.com', 'b', 'h', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    assert cli.main(["draft-letter", "greenhouse:1"]) == 2
    assert "profile" in capsys.readouterr().err.lower()


def test_status_survives_a_console_that_cannot_encode_its_glyphs(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The bar glyphs crashed cmd.exe with UnicodeEncodeError under cp1252.

    pytest captures stdout as UTF-8, so this pins the behaviour by writing
    through a genuinely cp1252 stream instead.
    """
    import io
    import sys

    conn.execute(
        "INSERT INTO postings (id, source, company, title, url, body, body_hash,"
        " first_seen, last_seen) VALUES ('greenhouse:1', 'greenhouse', 'Acme', 'Intern',"
        " 'https://e.com', 'b', 'h', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.commit()

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)

    assert cli.main(["status"]) == 0

    stream.flush()
    assert b"Acme" in raw.getvalue()
