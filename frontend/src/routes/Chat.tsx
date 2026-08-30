import { useCallback, useRef, useState } from "react";

import { streamChat, type AgentEvent } from "../api/client";
import { Trace, reduceEvents } from "../components/Trace";

/**
 * The agent. Consumes the SSE stream and renders the trace live: each tool
 * call appears as it is issued, then fills in with its result.
 */
export function Chat() {
  const [input, setInput] = useState("");
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [running, setRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (message: string) => {
      if (!message.trim() || running) return;
      setEvents([]);
      setRunning(true);
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        for await (const event of streamChat(message, [], controller.signal)) {
          // Appending per event is what makes rows arrive one at a time
          // rather than all at once when the turn finishes.
          setEvents((current) => [...current, event]);
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setEvents((current) => [
            ...current,
            {
              kind: "error",
              detail: error instanceof Error ? error.message : String(error),
              status: 0,
            },
          ]);
        }
      } finally {
        setRunning(false);
        abortRef.current = null;
      }
    },
    [running],
  );

  const { steps, text, error } = reduceEvents(events);

  return (
    <div className="mx-auto flex h-full min-h-0 w-full max-w-4xl flex-col px-6 py-6">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
        className="flex shrink-0 gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="remote ML internships in Europe that mention PyTorch"
          className="flex-1 border border-hairline bg-transparent px-3 py-2 text-sm rounded-xs placeholder:text-text-faint"
          aria-label="Ask the agent"
        />
        <button
          type="submit"
          disabled={running || !input.trim()}
          className="border border-signal px-4 py-2 font-mono text-xs text-signal rounded-xs disabled:opacity-40"
        >
          {running ? "running" : "ask"}
        </button>
        {running && (
          <button
            type="button"
            onClick={() => abortRef.current?.abort()}
            className="border border-hairline px-3 py-2 font-mono text-xs text-text-muted rounded-xs"
          >
            stop
          </button>
        )}
      </form>

      <div className="mt-6 min-h-0 flex-1 overflow-y-auto">
        {events.length === 0 && !running && (
          <div className="text-xs text-text-muted">
            <p>
              Ask in plain language. The agent searches your local postings and shows its work:
              every tool call, and the score behind every hit split into its dense and BM25 parts.
            </p>
            <ul className="mt-3 space-y-1 font-mono text-2xs text-text-faint">
              <li>remote ML internships in Europe that mention PyTorch</li>
              <li>which robotics companies have open internships?</li>
              <li>mark the top three as interested</li>
            </ul>
          </div>
        )}
        <Trace steps={steps} text={text} error={error} running={running} />
      </div>
    </div>
  );
}
