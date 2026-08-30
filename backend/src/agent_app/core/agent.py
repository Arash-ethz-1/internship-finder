"""The tool-use loop.

**Category B**: :func:`run_agent`. The event and result types below are
Category A and complete — the API and the CLI both read them.

The whole concept in one paragraph: you send the model a question plus a list
of tools. It replies either with a final answer or with "call
``search_postings`` with these arguments". You run the tool, send the result
back, and it decides again. Repeat until it answers or you hit ``max_iters``.
That loop is what an "AI agent" is; everything else is plumbing.

**Signature note.** ``plan.md`` specifies ``run_agent(...) -> AgentResult``.
That was amended: it is a *generator* that yields :class:`AgentEvent` values as
the loop runs, so ``/api/chat`` can stream tool calls into the trace panel
live rather than freezing for fifteen seconds. See PROGRESS.md, "Amendments to
plan.md". The blocking behaviour is still available through
:func:`collect_result`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

NOT_IMPLEMENTED = "Category B — author writes this by hand"

DEFAULT_MAX_ITERS = 12


@dataclass(frozen=True)
class ToolCall:
    """One completed tool call, for the trace panel."""

    name: str
    input: dict[str, Any]
    output: Any
    ms: int

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "input": self.input, "output": self.output, "ms": self.ms}


@dataclass(frozen=True)
class AgentResult:
    """What one agent turn produced.

    ``history`` is the full message list including this turn, ready to pass
    back in as the ``history`` argument of the next call.
    """

    text: str
    history: list[dict[str, Any]]
    trace: list[ToolCall] = field(default_factory=list)
    iters: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "history": self.history,
            "trace": [t.to_dict() for t in self.trace],
            "iters": self.iters,
        }


# --- events ----------------------------------------------------------------
#
# One class per SSE event type in plan.md's Phase 7 contract. `event_name` is
# the SSE `event:` field and `to_dict()` becomes its `data:` payload.


@dataclass(frozen=True)
class ToolCallEvent:
    """The model has asked for a tool. Emitted before the tool runs."""

    name: str
    input: dict[str, Any]
    event_name = "tool_call"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "input": self.input}


@dataclass(frozen=True)
class ToolResultEvent:
    """A tool finished. Emitted after it runs, with how long it took."""

    name: str
    output: Any
    ms: int
    event_name = "tool_result"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "output": self.output, "ms": self.ms}


@dataclass(frozen=True)
class TextEvent:
    """A piece of the model's prose answer.

    One of these per loop iteration is enough — the tool calls are the slow
    part, so the page already feels live without per-token streaming.
    """

    delta: str
    event_name = "text"

    def to_dict(self) -> dict[str, Any]:
        return {"delta": self.delta}


@dataclass(frozen=True)
class DoneEvent:
    """The loop finished. Always the last event, and carries the result."""

    result: AgentResult
    event_name = "done"

    def to_dict(self) -> dict[str, Any]:
        return {"iters": self.result.iters, "text": self.result.text}


AgentEvent = ToolCallEvent | ToolResultEvent | TextEvent | DoneEvent


def collect_result(events: Iterator[AgentEvent]) -> AgentResult:
    """Drain a :func:`run_agent` generator and return its final result.

    Category A. This is how the CLI REPL and any test gets blocking behaviour
    out of a streaming function, so the loop only has to be written once.
    """
    result: AgentResult | None = None
    for event in events:
        if isinstance(event, DoneEvent):
            result = event.result
    if result is None:
        raise RuntimeError("run_agent finished without emitting a DoneEvent")
    return result


# --- Category B ------------------------------------------------------------


def run_agent(
    user_message: str,
    history: list[dict[str, Any]],
    max_iters: int = DEFAULT_MAX_ITERS,
) -> Iterator[AgentEvent]:
    """Run the tool-use loop, yielding events as they happen.

    Expected shape:

    1. append ``user_message`` to ``history`` as a user message
    2. call the model with ``TOOL_SCHEMAS`` from :mod:`agent_app.core.tools`
    3. for each tool the model asks for: yield a :class:`ToolCallEvent`, run it
       through ``TOOL_FUNCTIONS``, yield a :class:`ToolResultEvent` with the
       elapsed milliseconds, and append the result to ``history``
    4. yield any prose the model produced as a :class:`TextEvent`
    5. loop until the model stops asking for tools or ``max_iters`` is reached
    6. yield exactly one :class:`DoneEvent` last, carrying the
       :class:`AgentResult`

    Must hold:

    * exactly one :class:`DoneEvent`, and it is last — :func:`collect_result`
      and the SSE route both depend on it
    * a tool that raises is reported back to the model as a tool result, not
      allowed to kill the loop; the model can then recover or explain
    * hitting ``max_iters`` still emits a :class:`DoneEvent`, with whatever
      text there is

    Dependencies come from :mod:`agent_app.runtime` and
    :mod:`agent_app.config` (``settings.require_anthropic_key()``,
    ``settings.agent_model``), since this signature takes no client.

    One thing to know: as written this raises on *call*. Once you add a
    ``yield``, Python turns it into a generator function and calling it will
    return an iterator that only raises on the first ``next()``. Both the 501
    handling in the API and the test suite account for that.

    Category B — author implements.
    """
    raise NotImplementedError(NOT_IMPLEMENTED)
