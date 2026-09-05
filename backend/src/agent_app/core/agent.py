"""The tool-use loop.

**Category B**: :func:`run_agent`. The event and result types below are
Category A and complete — the API and the CLI both read them.

The whole concept in one paragraph: you send the model a question plus a list
of tools. It replies either with a final answer or with "call
``find_postings`` with these arguments". You run the tool, send the result
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

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ..config import get_settings
from .tools import TOOL_FUNCTIONS, TOOL_SCHEMAS

NOT_IMPLEMENTED = "Category B — author writes this by hand"

DEFAULT_MAX_ITERS = 12

# Enough for a long answer about several postings; the loop, not the reply,
# is where the tokens actually go.
DEFAULT_MAX_TOKENS = 4096

# How many times one turn may call `find_postings`. This is the budget that
# makes re-searching a real option rather than an unbounded one: without a
# ceiling, "search again if the results are wrong" is an invitation to spend
# twenty model calls discovering that the corpus does not contain the answer.
# Four is enough for a first hypothesis and three corrections, which is more
# rephrasing than a person would do before giving up.
DEFAULT_MAX_SEARCHES = 4

# The tools that count against that budget. The others are local database
# reads: cheap, and the point of having them is that the agent reaches for
# them freely.
BUDGETED_TOOLS = ("find_postings",)

SYSTEM_PROMPT = """You help one person track internship and new-grad \
applications. You are talking to them about their own database of postings.

**A first search is a hypothesis, not an answer.**

You do not know how these postings are worded until you have seen some. Your
first guess at the vocabulary is often wrong, and the results say so if you
read them. So: search, then look at the titles that came back and ask whether
they are the thing that was asked for. If they are not, search again with
different words. You have a budget of a few searches per turn and you are
expected to use more than one whenever the first is unconvincing.

What "unconvincing" looks like, concretely. Someone asks for ML research
internships. The first search returns an M&A analyst internship, a
lab-automation internship and a frontend intern. Those are not near misses;
they are evidence that the query matched filler text rather than the role.
Searching again with "machine learning research internship" instead of "AI
internship" is the whole fix, and it costs one more call.

Do not hand the problem back. Noticing that half the results are off-target is
the signal to search again yourself, not a question to pass to the person -
"do you want to narrow this down?" after one search is the failure this whole
instruction exists to prevent. Ask only once you have spent the budget, or
when the ambiguity is genuinely about what they want rather than about how
postings happen to be worded. Which words the corpus uses is your problem to
solve, not theirs.

Pass two or three alternate phrasings in `queries` on every search. Companies
word the same job differently - "machine learning research intern", "deep
learning PhD internship", "applied scientist intern" - and one phrasing finds
only the companies that happened to use it.

Before settling for a thin result, call `corpus_stats` with the same
constraints. It says whether a better query would have helped or whether that
is simply all there is, and those deserve different answers: one is "let me
try again", the other is "there are only nine postings in that city at all,
here they are". Never imply you selected ten out of many when ten was the
whole set.

On a vague or self-referential request - "something that fits me", or "ML
research" with no other detail - call `search_profile` first. It returns the
person's own project write-ups, and searching in the vocabulary they actually
work in beats searching with generic words for the same idea.

Before showing a long list, consider `past_decisions`. The roles they struck
as not_relevant matched a search well enough to be shown and were rejected
anyway, which is the mistake you are about to repeat. Drop your own likely
false positives, and say that you did and why - never filter silently. It is
evidence, not a rule: one rejection is noise, and people change their minds.

Working rules:

- Search before you answer. You do not know what is in the database until you
  call a tool, and a plausible-sounding posting you invented is worse than
  saying you found nothing.
- Always name postings by their posting_id (like `greenhouse:abc123`), so the
  answer can be checked against the dashboard.
- Read a posting with get_posting before changing its status. Statuses are a
  record of what the person actually did.
- If a tool returns an error, say what failed and what you tried. Do not retry
  the same call unchanged.
- Say what you actually did when it is not obvious: that the first phrasing
  came back wrong so you tried another, or that a count is the whole corpus
  rather than a selection. Do not narrate every call.
- Be brief. This is a working tool, not a chat companion."""


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
        # `history` travels with the event so the browser can pass it back on
        # the next turn. There is no conversations table: the client holds the
        # thread, which is what keeps a follow-up like "mark the first three"
        # able to refer to anything at all.
        return {
            "iters": self.result.iters,
            "text": self.result.text,
            "history": self.result.history,
        }


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
    max_searches: int = DEFAULT_MAX_SEARCHES,
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
    * at most ``max_searches`` calls to a tool in :data:`BUDGETED_TOOLS`
      succeed in one turn; beyond that the call is refused with an explanation
      the model can act on. The system prompt tells it to search again when
      the first results look wrong, and this is what stops "again" from being
      unbounded — an agent convinced the corpus contains something it does not
      will otherwise rephrase until ``max_iters`` runs out.

    Dependencies come from :mod:`agent_app.runtime` and
    :mod:`agent_app.config` (``settings.require_anthropic_key()``,
    ``settings.agent_model``), since this signature takes no client.

    Category B, written by Claude at the author's request on 2026-08-31.
    """
    import anthropic  # deferred: importing this module must not need the SDK

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.require_anthropic_key())

    messages: list[dict[str, Any]] = [*history, {"role": "user", "content": user_message}]
    trace: list[ToolCall] = []
    spoken: list[str] = []
    iters = 0
    searches = 0

    while iters < max_iters:
        iters += 1
        reply = client.messages.create(
            model=settings.agent_model,
            max_tokens=DEFAULT_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )
        blocks = list(reply.content)
        messages.append(
            {"role": "assistant", "content": [_serialise(b) for b in blocks if _serialise(b)]}
        )

        said = "".join(b.text for b in blocks if b.type == "text").strip()
        if said:
            spoken.append(said)
            yield TextEvent(delta=said)

        requested = [b for b in blocks if b.type == "tool_use"]
        if not requested:
            break

        results: list[dict[str, Any]] = []
        for call in requested:
            arguments = dict(call.input or {})
            # Announced before it runs so the trace panel can show it pending.
            yield ToolCallEvent(name=call.name, input=arguments)

            started = time.perf_counter()
            budgeted = call.name in BUDGETED_TOOLS
            if budgeted and searches >= max_searches:
                # Refused, not silently dropped, and it still travels through
                # the trace: a turn that ran out of searches should look
                # different in the panel from one that chose to stop.
                output, failed = _budget_spent(max_searches), True
            else:
                searches += int(budgeted)
                output, failed = _run_tool(call.name, arguments)
            ms = int((time.perf_counter() - started) * 1000)

            yield ToolResultEvent(name=call.name, output=output, ms=ms)
            trace.append(ToolCall(name=call.name, input=arguments, output=output, ms=ms))
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": json.dumps(output, default=str),
                    "is_error": failed,
                }
            )
        messages.append({"role": "user", "content": results})

    yield DoneEvent(
        result=AgentResult(
            text="\n\n".join(spoken),
            history=messages,
            trace=trace,
            iters=iters,
        )
    )


def _budget_spent(max_searches: int) -> str:
    """What a tool call refused by the search budget tells the model.

    Phrased as an instruction rather than an error, because it is not a
    failure: the loop worked as designed and the remaining question is what to
    say. Naming what it already has is what stops the model reporting an
    internal limit to the person as though it were a property of their corpus.
    """
    return (
        f"Search budget spent: {max_searches} searches already ran this turn. "
        "Answer now with the results you have. If they are still not what was "
        "asked for, say that plainly, say which phrasings you tried, and use "
        "corpus_stats to say whether anything matching exists at all — do not "
        "describe this limit to the person as though the database were empty."
    )


def _serialise(block: Any) -> dict[str, Any] | None:
    """Turn one SDK content block into the plain dict the history needs.

    The history is handed back to the frontend as JSON and passed straight
    into the next call, so it cannot hold SDK objects. Block types we do not
    recognise are dropped rather than guessed at.
    """
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": dict(block.input or {}),
        }
    return None


def _run_tool(name: str, arguments: dict[str, Any]) -> tuple[Any, bool]:
    """Run one tool, returning ``(output, failed)``.

    Nothing raises out of here. A tool that blows up — a bad posting id, an
    invented status, a tool name the model made up — comes back as a result
    the model can read and recover from. Killing the turn instead would hand
    the user a stack trace where an explanation was possible.
    """
    function = TOOL_FUNCTIONS.get(name)
    if function is None:
        known = ", ".join(sorted(TOOL_FUNCTIONS))
        return f"No tool named {name!r}. Available tools: {known}", True
    try:
        return function(**arguments), False
    except Exception as exc:  # noqa: BLE001 - reported to the model, not swallowed
        return f"{type(exc).__name__}: {exc}", True
