"""The agent turn, streamed as Server-Sent Events.

This is the endpoint the dashboard's signature element depends on. It must
stream: a tool-use loop takes ten to twenty seconds, and a page that freezes
for that long and then dumps a blob hides the one thing worth showing — the
agent working.

The route owns none of the loop: it consumes the generator contract from
``run_agent`` and translates whatever it yields into SSE frames. Anything that
goes wrong before the first event — a missing API key, most often — becomes a
real HTTP error, because once a stream has started the status code is already
on the wire.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..core.agent import AgentEvent, run_agent
from .schemas import ChatRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


def sse(event: str, data: dict[str, object]) -> str:
    """Format one Server-Sent Event.

    The blank line is the record separator and is not optional; without it the
    browser buffers the event instead of dispatching it.
    """
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/chat")
def post_chat(request: ChatRequest) -> StreamingResponse:
    """Run one agent turn, streaming tool calls and text as they happen.

    Emits ``tool_call``, ``tool_result``, ``text`` and ``done`` events, plus
    ``error`` if the turn fails partway through.

    The first event is pulled *before* the response starts. That is what lets
    an immediately-broken turn — a missing key, a bad request — be a real HTTP
    error with a JSON body rather than a 200 containing bad news; once a stream
    has begun, the status code is already on the wire and cannot be taken back.
    """
    events: Iterator[AgentEvent] = iter(())
    first: AgentEvent | None = None

    try:
        # run_agent is a generator, so nothing runs until this next() call.
        events = run_agent(request.message, list(request.history), request.max_iters)
        first = next(events, None)
    except Exception as exc:
        log.exception("agent turn failed before streaming")
        raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc

    def body() -> Iterator[str]:
        if first is None:
            return
        yield sse(first.event_name, first.to_dict())
        try:
            for event in events:
                yield sse(event.event_name, event.to_dict())
        except Exception as exc:  # noqa: BLE001 - a dead stream tells the user nothing
            log.exception("agent turn failed mid-stream")
            yield sse("error", {"detail": f"{type(exc).__name__}: {exc}", "status": 500})

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
