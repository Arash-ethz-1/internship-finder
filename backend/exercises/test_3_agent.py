"""Problems 7 and 8: run_agent and the tool descriptions.

A fake model stands in for Anthropic, so these run offline and for free. It
replies with a scripted sequence of turns, which lets the tests check the loop
rather than the model.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

import pytest

from agent_app.core import tools
from agent_app.core.agent import (
    DoneEvent,
    TextEvent,
    ToolResultEvent,
    run_agent,
)

# --- a fake Anthropic client ------------------------------------------------


@dataclass
class Block:
    type: str
    text: str = ""
    name: str = ""
    id: str = ""
    input: dict[str, Any] | None = None


class FakeMessage:
    def __init__(self, blocks: list[Block], stop_reason: str) -> None:
        self.content = blocks
        self.stop_reason = stop_reason
        self.role = "assistant"


class FakeMessages:
    def __init__(self, script: list[FakeMessage]) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeMessage:
        self.calls.append(kwargs)
        if not self.script:
            return FakeMessage([Block(type="text", text="done")], "end_turn")
        return self.script.pop(0)


class FakeClient:
    def __init__(self, script: list[FakeMessage]) -> None:
        self.messages = FakeMessages(script)


@pytest.fixture
def fake_anthropic(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Install a scripted client. Returns a function to set the script."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from agent_app.config import reset_settings

    reset_settings()

    holder: dict[str, FakeClient] = {}

    def install(script: list[FakeMessage]) -> FakeClient:
        client = FakeClient(script)
        holder["client"] = client
        monkeypatch.setattr("anthropic.Anthropic", lambda **_kw: client)
        return client

    return install


def seed_posting(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO postings (id, source, company, title, url, body, body_hash,"
        " first_seen, last_seen) VALUES ('greenhouse:1', 'greenhouse', 'Acme', 'Intern',"
        " 'https://e.com', 'the body', 'h', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.commit()


# --- problem 7: run_agent ---------------------------------------------------


def test_a_plain_answer_yields_text_then_done(fake_anthropic) -> None:  # type: ignore[no-untyped-def]
    fake_anthropic([FakeMessage([Block(type="text", text="Hello.")], "end_turn")])

    events = list(run_agent("hi", []))

    assert any(isinstance(e, TextEvent) for e in events)
    assert isinstance(events[-1], DoneEvent), "the last event must be DoneEvent"
    assert sum(isinstance(e, DoneEvent) for e in events) == 1


def test_a_tool_call_is_announced_then_resolved(conn: sqlite3.Connection, fake_anthropic) -> None:  # type: ignore[no-untyped-def]
    seed_posting(conn)
    fake_anthropic(
        [
            FakeMessage(
                [
                    Block(
                        type="tool_use",
                        name="get_posting",
                        id="call_1",
                        input={"posting_id": "greenhouse:1"},
                    )
                ],
                "tool_use",
            ),
            FakeMessage([Block(type="text", text="It is an internship at Acme.")], "end_turn"),
        ]
    )

    events = list(run_agent("tell me about greenhouse:1", []))
    kinds = [type(e).__name__ for e in events]

    assert "ToolCallEvent" in kinds
    assert "ToolResultEvent" in kinds
    # Announced before it runs, so the dashboard can show it as pending.
    assert kinds.index("ToolCallEvent") < kinds.index("ToolResultEvent")
    assert isinstance(events[-1], DoneEvent)


def test_the_tool_actually_runs(conn: sqlite3.Connection, fake_anthropic) -> None:  # type: ignore[no-untyped-def]
    seed_posting(conn)
    fake_anthropic(
        [
            FakeMessage(
                [
                    Block(
                        type="tool_use",
                        name="update_status",
                        id="c1",
                        input={"posting_id": "greenhouse:1", "status": "applied"},
                    )
                ],
                "tool_use",
            ),
            FakeMessage([Block(type="text", text="Marked as applied.")], "end_turn"),
        ]
    )

    list(run_agent("mark it applied", []))

    row = conn.execute("SELECT status FROM applications WHERE posting_id='greenhouse:1'").fetchone()
    assert row is not None and row["status"] == "applied"


def test_a_failing_tool_does_not_kill_the_loop(conn: sqlite3.Connection, fake_anthropic) -> None:  # type: ignore[no-untyped-def]
    # get_posting raises KeyError for an unknown id. The model should be told,
    # and get a chance to recover, rather than the whole turn exploding.
    fake_anthropic(
        [
            FakeMessage(
                [Block(type="tool_use", name="get_posting", id="c1", input={"posting_id": "nope"})],
                "tool_use",
            ),
            FakeMessage([Block(type="text", text="That posting does not exist.")], "end_turn"),
        ]
    )

    events = list(run_agent("look up nope", []))
    assert isinstance(events[-1], DoneEvent)
    assert any(isinstance(e, ToolResultEvent) for e in events)


def test_an_unknown_tool_name_does_not_kill_the_loop(fake_anthropic) -> None:  # type: ignore[no-untyped-def]
    fake_anthropic(
        [
            FakeMessage(
                [Block(type="tool_use", name="invented_tool", id="c1", input={})],
                "tool_use",
            ),
            FakeMessage([Block(type="text", text="Sorry, I cannot do that.")], "end_turn"),
        ]
    )
    events = list(run_agent("do something impossible", []))
    assert isinstance(events[-1], DoneEvent)


def test_max_iters_is_respected(conn: sqlite3.Connection, fake_anthropic) -> None:  # type: ignore[no-untyped-def]
    seed_posting(conn)
    # A model that asks for a tool forever. The loop must stop and still finish
    # cleanly rather than running until the API bill does.
    script = [
        FakeMessage(
            [
                Block(
                    type="tool_use",
                    name="get_posting",
                    id=f"c{i}",
                    input={"posting_id": "greenhouse:1"},
                )
            ],
            "tool_use",
        )
        for i in range(50)
    ]
    client = fake_anthropic(script)

    events = list(run_agent("loop forever", [], max_iters=3))

    assert isinstance(events[-1], DoneEvent)
    assert len(client.messages.calls) <= 4, "stopped at roughly max_iters model calls"


def test_the_result_carries_a_reusable_history(fake_anthropic) -> None:  # type: ignore[no-untyped-def]
    fake_anthropic([FakeMessage([Block(type="text", text="Hi.")], "end_turn")])
    events = list(run_agent("hello", []))
    result = events[-1].result

    assert isinstance(result.history, list)
    assert result.history, "history must contain at least the user turn"
    assert result.text


def test_the_tools_are_offered_to_the_model(fake_anthropic) -> None:  # type: ignore[no-untyped-def]
    client = fake_anthropic([FakeMessage([Block(type="text", text="Hi.")], "end_turn")])
    list(run_agent("hello", []))

    sent = client.messages.calls[0]
    names = {t["name"] for t in sent.get("tools", [])}
    assert names == set(tools.TOOL_FUNCTIONS), "all four tools must be offered"


def test_trace_records_the_calls(conn: sqlite3.Connection, fake_anthropic) -> None:  # type: ignore[no-untyped-def]
    seed_posting(conn)
    fake_anthropic(
        [
            FakeMessage(
                [
                    Block(
                        type="tool_use",
                        name="get_posting",
                        id="c1",
                        input={"posting_id": "greenhouse:1"},
                    )
                ],
                "tool_use",
            ),
            FakeMessage([Block(type="text", text="Done.")], "end_turn"),
        ]
    )
    result = list(run_agent("look it up", []))[-1].result
    assert len(result.trace) == 1
    assert result.trace[0].name == "get_posting"


# --- problem 8: the tool descriptions ---------------------------------------


def test_descriptions_are_written() -> None:
    assert tools.descriptions_written(), (
        "every 'TODO: author writes this' in TOOL_SCHEMAS still needs replacing"
    )


def test_descriptions_are_not_one_liners() -> None:
    # The model reads only these. "Searches postings." tells it nothing about
    # when to use this instead of get_posting.
    for schema in tools.TOOL_SCHEMAS:
        assert len(schema["description"]) > 60, f"{schema['name']} needs a real description"
