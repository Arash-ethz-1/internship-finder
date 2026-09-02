"""Phase 5: reading the profile corpus.

Which files are read, which are deliberately skipped, what happens when the
folder is empty, and that a real write-up comes out the other side as chunks.
"""

from __future__ import annotations

import sqlite3

import pytest

from agent_app import cli, runtime
from agent_app.config import Settings, reset_settings
from agent_app.ingest.profile import (
    SKIP,
    ProfileReport,
    doc_slug,
    ingest_profile,
    read_profile_docs,
)


def write_doc(settings: Settings, name: str, text: str = "# Heading\n\nSome content.") -> None:
    settings.profile_dir.mkdir(parents=True, exist_ok=True)
    (settings.profile_dir / name).write_text(text, encoding="utf-8")


def test_doc_slug_is_the_filename_stem(settings: Settings) -> None:
    write_doc(settings, "GNN-Maze-Solver.md")
    path = settings.profile_dir / "GNN-Maze-Solver.md"
    assert doc_slug(path) == "gnn-maze-solver"


def test_read_profile_docs(settings: Settings) -> None:
    write_doc(settings, "pyblio.md", "# pyblio\n\nA bibliography tool.")
    write_doc(settings, "distributed-attention.md", "# attention\n\nSharded across GPUs.")

    docs = read_profile_docs(settings.profile_dir)
    assert [slug for slug, _ in docs] == ["distributed-attention", "pyblio"]
    assert "bibliography" in dict(docs)["pyblio"]


def test_the_readme_and_example_are_never_ingested(settings: Settings) -> None:
    # Grounding a letter in placeholder text is how a letter starts lying.
    write_doc(settings, "README.md", "# how to write these")
    write_doc(settings, "example-project.md", "# Example Project")
    write_doc(settings, "real.md", "# real\n\nsomething I built")

    assert [slug for slug, _ in read_profile_docs(settings.profile_dir)] == ["real"]
    assert SKIP == {"readme", "example-project"}


def test_empty_and_missing_files_are_ignored(settings: Settings) -> None:
    write_doc(settings, "blank.md", "   \n\n  ")
    write_doc(settings, "notes.txt", "not markdown")
    assert read_profile_docs(settings.profile_dir) == []


def test_read_profile_docs_of_a_missing_directory(settings: Settings) -> None:
    assert read_profile_docs(settings.profile_dir / "nope") == []


def test_ingest_profile_reports_an_empty_folder(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    report = ingest_profile(conn, settings)
    assert report.documents == 0
    assert "No project write-ups" in report.format()


def test_ingest_profile_chunks_a_real_document(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    write_doc(settings, "real.md")

    report = ingest_profile(conn, settings)

    assert report.documents == 1
    assert report.chunks >= 1
    rows = conn.execute("SELECT profile_doc, text FROM chunks ORDER BY ordinal").fetchall()
    assert rows[0]["profile_doc"] == "real"
    assert "Some content." in rows[0]["text"]


def test_ingest_profile_writes_chunks_once_chunking_exists(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_app.core.chunking import Chunk

    write_doc(settings, "pyblio.md", "# pyblio\n\nA bibliography tool.")
    monkeypatch.setattr(
        "agent_app.core.chunking.chunk_profile_doc",
        lambda text, max_chars=1200: [Chunk(text="A bibliography tool.", ordinal=0)],
    )

    report = ingest_profile(conn, settings)

    assert report.documents == 1
    assert report.chunks == 1
    rows = conn.execute("SELECT profile_doc, ordinal, text FROM chunks").fetchall()
    assert rows[0]["profile_doc"] == "pyblio"
    assert rows[0]["text"] == "A bibliography tool."


def test_reingesting_replaces_rather_than_duplicates(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_app.core.chunking import Chunk

    write_doc(settings, "pyblio.md", "# pyblio\n\nfirst version")
    monkeypatch.setattr(
        "agent_app.core.chunking.chunk_profile_doc",
        lambda text, max_chars=1200: [Chunk(text=text, ordinal=0)],
    )
    ingest_profile(conn, settings)

    write_doc(settings, "pyblio.md", "# pyblio\n\nsecond version")
    ingest_profile(conn, settings)

    rows = conn.execute("SELECT text FROM chunks WHERE profile_doc = 'pyblio'").fetchall()
    assert len(rows) == 1
    assert "second version" in rows[0]["text"]


def test_profile_and_posting_chunks_coexist(
    conn: sqlite3.Connection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_app.core.chunking import Chunk

    conn.execute(
        "INSERT INTO postings (id, source, company, title, url, body, body_hash,"
        " first_seen, last_seen) VALUES ('greenhouse:1', 'greenhouse', 'Acme', 'Intern',"
        " 'https://e.com', 'b', 'h', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.execute("INSERT INTO chunks (posting_id, ordinal, text) VALUES ('greenhouse:1', 0, 'job')")
    conn.commit()

    write_doc(settings, "pyblio.md")
    monkeypatch.setattr(
        "agent_app.core.chunking.chunk_profile_doc",
        lambda text, max_chars=1200: [Chunk(text=text, ordinal=0)],
    )
    ingest_profile(conn, settings)

    # PLAN.md's Phase 5 check: the chunks table shows both kinds.
    kinds = conn.execute(
        "SELECT count(*) FILTER (WHERE posting_id IS NOT NULL) AS postings,"
        " count(*) FILTER (WHERE profile_doc IS NOT NULL) AS profiles FROM chunks"
    ).fetchone()
    assert (kinds["postings"], kinds["profiles"]) == (1, 1)


def test_profile_report_formats() -> None:
    report = ProfileReport(documents=2, chunks=7, per_doc={"a": 3, "b": 4}, skipped=["readme"])
    text = report.format()
    assert "2 document(s), 7 chunk(s)" in text
    assert "skipped: readme" in text


def test_cli_ingest_profile_explains_an_empty_folder(
    conn: sqlite3.Connection, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["ingest-profile"]) == 0
    out = capsys.readouterr().out
    assert "No project write-ups" in out
    assert "profile/README.md" in out


def test_cli_ingest_profile_chunks_then_stops_without_an_api_key(
    conn: sqlite3.Connection,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Chunking is local and free; embedding through a paid provider is neither.
    # The write-up is chunked and stored, and only the embedding step reports
    # why it stopped. Pinned to Voyage on purpose: under the default local
    # provider there is no key to be missing, and the test would download a
    # model instead of asserting anything.
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    reset_settings()
    runtime.reset()
    write_doc(settings, "real.md")

    assert cli.main(["ingest-profile"]) == 2

    captured = capsys.readouterr()
    assert "1 document(s)" in captured.out
    assert "chunk(s)" in captured.out
    assert "VOYAGE_API_KEY" in captured.err
