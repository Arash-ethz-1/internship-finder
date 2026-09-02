"""The two ends of the cluster round trip have to agree on one file format.

``cluster/embed_chunks.py`` is deliberately standalone -- it never imports
``agent_app`` -- which means nothing but a test stops the two halves drifting
apart. So this loads the script by path and feeds it a real export.

Nothing here loads a model. The far end's arithmetic is fastembed's; what is
worth testing is the contract around it.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pytest

from agent_app import cli, runtime
from agent_app.config import Settings
from agent_app.core.embeddings import LOCAL_MODEL_PREFIXES, export_pending

SCRIPT = Path(__file__).resolve().parents[1] / "cluster" / "embed_chunks.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("embed_chunks", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["embed_chunks"] = module
    spec.loader.exec_module(module)
    return module


def add_chunk(conn: sqlite3.Connection, text: str) -> int:
    cursor = conn.execute(
        "INSERT INTO chunks (profile_doc, ordinal, text) VALUES ('doc', 0, ?)", (text,)
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def test_the_script_reads_what_the_cli_writes(
    conn: sqlite3.Connection, settings: Settings, tmp_path: Path
) -> None:
    first = add_chunk(conn, "one")
    second = add_chunk(conn, "two")
    export = tmp_path / "pending.jsonl"
    export_pending(conn, export)

    header, ids, texts = load_script().read_export(export)
    assert header["model"] == settings.embedding_model
    assert header["dim"] == settings.embedding_dim
    assert ids == [first, second]
    assert texts == ["one", "two"]


def test_the_script_refuses_a_file_with_no_header(tmp_path: Path) -> None:
    path = tmp_path / "not-an-export.jsonl"
    path.write_text('{"id": 1, "text": "one"}\n', encoding="utf-8")

    with pytest.raises(SystemExit, match="no header line"):
        load_script().read_export(path)


def test_the_script_names_the_line_it_could_not_read(tmp_path: Path) -> None:
    path = tmp_path / "truncated.jsonl"
    path.write_text('{"model": "m", "dim": 4}\n{"id": 1, "text": "one"}\n{"id": 2\n', "utf-8")

    with pytest.raises(SystemExit, match=":3 is not a chunk record"):
        load_script().read_export(path)


def test_the_two_prefix_tables_agree() -> None:
    """A prefix applied on one machine and not the other splits the space."""
    assert load_script().MODEL_PREFIXES == LOCAL_MODEL_PREFIXES


def test_cli_export_writes_a_file_and_touches_nothing(
    conn: sqlite3.Connection, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_chunk(conn, "one")
    add_chunk(conn, "two")
    out = tmp_path / "pending.jsonl"

    assert cli.main(["embed", "--export", str(out), "--limit", "1"]) == 0

    assert len(out.read_text(encoding="utf-8").splitlines()) == 2  # header + one chunk
    assert "1 chunk(s) written" in capsys.readouterr().out
    # cli.main closes the connection it opened, so ask for a fresh one.
    fresh = runtime.get_db()
    assert fresh.execute("SELECT count(*) FROM chunks WHERE vector_row IS NULL").fetchone()[0] == 2


def test_cli_import_explains_a_bad_file(
    conn: sqlite3.Connection, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "nope.npz"
    bad.write_bytes(b"not an npz")

    assert cli.main(["embed", "--import", str(bad)]) == 2
    assert "error:" in capsys.readouterr().err
