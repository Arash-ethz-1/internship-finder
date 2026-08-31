"""Phase 6: prompt assembly, file writing and the DB update.

The model is never called. What is tested is everything around it: that the
prompt only ever contains retrieved text, that TODO markers are surfaced, and
that drafting records the letter against the posting.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_app.config import Settings
from agent_app.core import letters
from agent_app.core.letters import (
    SYSTEM_PROMPT,
    Letter,
    LetterError,
    build_prompt,
    format_extracts,
    letter_path,
    read_letter,
)
from agent_app.core.retrieval import SearchHit
from agent_app.db import Posting


def make_hit(doc: str, text: str, score: float = 0.5, ordinal: int = 0) -> SearchHit:
    return SearchHit(
        chunk_id=1,
        posting_id=None,
        profile_doc=doc,
        ordinal=ordinal,
        text=text,
        score=score,
        rank=1,
        component_scores={"dense": score / 2, "bm25": score / 2},
    )


def make_posting(**over: object) -> Posting:
    fields: dict[str, object] = {
        "id": "greenhouse:1",
        "source": "greenhouse",
        "company": "Acme Robotics",
        "title": "Software Engineering Intern",
        "location": "Zurich",
        "remote": False,
        "url": "https://example.com/1",
        "body": "Build control systems in C++ and Python.",
        "body_hash": "h",
    }
    fields.update(over)
    return Posting(**fields)  # type: ignore[arg-type]


def _insert(conn: sqlite3.Connection, posting: Posting) -> None:
    conn.execute(
        "INSERT INTO postings (id, source, company, title, location, remote, url, body,"
        " body_hash, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,"
        " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (
            posting.id,
            posting.source,
            posting.company,
            posting.title,
            posting.location,
            int(posting.remote),
            posting.url,
            posting.body,
            posting.body_hash,
        ),
    )
    conn.commit()


# --- the anti-fabrication contract -----------------------------------------


def test_system_prompt_forbids_invention_and_demands_markers() -> None:
    assert "Never invent a detail" in SYSTEM_PROMPT
    assert "[TODO:" in SYSTEM_PROMPT
    assert "marker is a success" in SYSTEM_PROMPT


def test_prompt_contains_only_the_posting_and_the_retrieved_extracts() -> None:
    posting = make_posting()
    hits = [make_hit("gnn-maze-solver", "I built a GNN that solves mazes.")]
    prompt = build_prompt(posting, hits)

    assert "Acme Robotics" in prompt
    assert "Build control systems" in prompt
    assert "I built a GNN that solves mazes." in prompt
    assert "gnn-maze-solver" in prompt


def test_prompt_labels_each_extract_with_its_source_document() -> None:
    hits = [
        make_hit("pyblio", "A bibliography tool."),
        make_hit("distributed-attention", "Sharded attention across GPUs."),
    ]
    extracts = format_extracts(hits)
    assert "Extract 1 (from pyblio)" in extracts
    assert "Extract 2 (from distributed-attention)" in extracts


def test_no_grounding_is_an_error_with_the_command_to_fix_it() -> None:
    # Drafting from nothing is exactly how a letter gets fabricated, so it is
    # refused rather than attempted.
    with pytest.raises(LetterError, match="ingest-profile"):
        format_extracts([])


def test_long_posting_bodies_are_truncated() -> None:
    posting = make_posting(body="x" * 10_000)
    prompt = build_prompt(posting, [make_hit("doc", "text")])
    assert "x" * letters.MAX_POSTING_CHARS in prompt
    assert "x" * (letters.MAX_POSTING_CHARS + 1) not in prompt


def test_missing_location_is_stated_not_blank() -> None:
    prompt = build_prompt(make_posting(location=None), [make_hit("doc", "text")])
    assert "Location: not stated" in prompt


# --- paths -----------------------------------------------------------------


def test_letter_path_is_windows_safe(settings: Settings) -> None:
    # Posting ids contain a colon, which is not a legal filename character.
    path = letter_path(settings, "greenhouse:4012345")
    assert path.name == "greenhouse_4012345.md"
    assert ":" not in path.name


def test_read_letter_returns_none_when_absent(settings: Settings) -> None:
    assert read_letter("greenhouse:nope") is None


# --- drafting end to end, with the model stubbed ---------------------------


def test_draft_letter_writes_the_file_and_records_it(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    posting = make_posting()
    _insert(conn, posting)

    monkeypatch.setattr(
        letters, "find_grounding", lambda _p, k=3: [make_hit("pyblio", "I wrote a parser.")]
    )
    monkeypatch.setattr(
        letters,
        "call_model",
        lambda _s, _p: "I built a parser. [TODO: your degree] Regards.",
    )

    letter = letters.draft_letter("greenhouse:1")

    assert isinstance(letter, Letter)
    assert letter.path.exists()
    assert letter.path.read_text(encoding="utf-8").startswith("I built a parser.")
    assert letter.todos == ["[TODO: your degree]"]

    row = conn.execute("SELECT * FROM applications WHERE posting_id='greenhouse:1'").fetchone()
    assert row["letter_path"] == str(letter.path)
    # Drafting implies interest, so the posting stops being untriaged.
    assert row["status"] == "interested"
    assert conn.execute("SELECT count(*) FROM status_history").fetchone()[0] == 1


def test_draft_letter_keeps_an_existing_status(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _insert(conn, make_posting())
    from agent_app.core import tools

    tools.update_status("greenhouse:1", "applied", "already sent")

    monkeypatch.setattr(letters, "find_grounding", lambda _p, k=3: [make_hit("d", "t")])
    monkeypatch.setattr(letters, "call_model", lambda _s, _p: "text")
    letters.draft_letter("greenhouse:1")

    row = conn.execute("SELECT * FROM applications WHERE posting_id='greenhouse:1'").fetchone()
    assert row["status"] == "applied"  # not reset to interested
    assert row["letter_path"] is not None


def test_draft_letter_rejects_an_unknown_posting(conn: sqlite3.Connection) -> None:
    with pytest.raises(KeyError):
        letters.draft_letter("greenhouse:missing")


def test_draft_letter_refuses_without_profile_chunks(conn: sqlite3.Connection) -> None:
    # A letter with nothing to ground it in would be invented. Refusing is the
    # feature: the model never gets the chance to make the projects up.
    _insert(conn, make_posting())
    with pytest.raises(letters.LetterError, match="profile"):
        letters.draft_letter("greenhouse:1")


def test_letter_serialises_its_grounding(tmp_path: Path) -> None:
    letter = Letter(
        posting_id="greenhouse:1",
        text="body",
        path=tmp_path / "x.md",
        grounding=[make_hit("pyblio", "text", score=0.8)],
        todos=[],
    )
    data = letter.to_dict()
    assert data["grounding"][0]["profile_doc"] == "pyblio"
    assert data["grounding"][0]["score"] == 0.8
