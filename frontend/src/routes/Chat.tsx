import { useQuery } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";

import { getStats, streamChat, type AgentEvent } from "../api/client";
import { Trace, reduceEvents } from "../components/Trace";

/**
 * The agent, and the app's front door.
 *
 * Say what you are looking for; the agent builds a list you can act on. Turns
 * accumulate rather than replacing each other, and the conversation history is
 * carried forward — without it, "mark the first three" has nothing to refer to.
 */

interface Turn {
  question: string;
  events: AgentEvent[];
}

const EXAMPLES = [
  "ML research internships in Zurich",
  "quantitative researcher internships, Europe",
  "AI agent engineering internships that mention LLMs",
  "what have I applied to so far?",
];

export function Chat() {
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [history, setHistory] = useState<unknown[]>([]);
  const [running, setRunning] = useState(false);
  const stats = useQuery({ queryKey: ["stats"], queryFn: getStats });
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (message: string) => {
      if (!message.trim() || running) return;
      setInput("");
      setRunning(true);
      const index = turns.length;
      setTurns((current) => [...current, { question: message, events: [] }]);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        for await (const event of streamChat(message, history, controller.signal)) {
          // Appending per event is what makes rows arrive one at a time
          // rather than all at once when the turn finishes.
          setTurns((current) =>
            current.map((turn, i) =>
              i === index ? { ...turn, events: [...turn.events, event] } : turn,
            ),
          );
          if (event.kind === "done" && Array.isArray(event.history)) {
            setHistory(event.history);
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          const failure: AgentEvent = {
            kind: "error",
            detail: error instanceof Error ? error.message : String(error),
            status: 0,
          };
          setTurns((current) =>
            current.map((turn, i) =>
              i === index ? { ...turn, events: [...turn.events, failure] } : turn,
            ),
          );
        }
      } finally {
        setRunning(false);
        abortRef.current = null;
      }
    },
    [running, turns.length, history],
  );

  // Before the first turn the composer is the page: centred, nothing above it
  // to read past. After it, the transcript is the page and the composer drops
  // to the bottom edge where a chat input belongs.
  const empty = turns.length === 0;

  const composer = (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void send(input);
      }}
      className="flex gap-2"
    >
      <input
        value={input}
        onChange={(e) => setInput(e.target.value)}
        placeholder="ML research internships in Zurich"
        autoFocus={empty}
        className="flex-1 rounded-xs border border-hairline bg-transparent px-3 py-2 text-sm placeholder:text-text-faint"
        aria-label="Ask the agent"
      />
      <button
        type="submit"
        disabled={running || !input.trim()}
        className="rounded-xs border border-signal px-4 py-2 font-mono text-xs text-signal disabled:opacity-40"
      >
        {running ? "running" : "ask"}
      </button>
      {running && (
        <button
          type="button"
          onClick={() => abortRef.current?.abort()}
          className="rounded-xs border border-hairline px-3 py-2 font-mono text-xs text-text-muted"
        >
          stop
        </button>
      )}
    </form>
  );

  if (empty) {
    return (
      <div className="mx-auto flex h-full min-h-0 w-full max-w-2xl flex-col justify-center px-6 pb-20">
        <h1 className="text-lg font-medium">What are you looking for?</h1>
        <p className="mt-2 text-xs text-text-muted">
          The agent searches your{" "}
          <span className="font-mono tabular-nums">
            {(stats.data?.total ?? 0).toLocaleString()}
          </span>{" "}
          postings and gives you a list you can keep, open, or draft a letter for. Anything you
          have already triaged is left out, so the same posting is never offered twice.
        </p>

        <div className="mt-6">{composer}</div>

        <ul className="mt-3 flex flex-wrap gap-1.5">
          {EXAMPLES.map((example) => (
            <li key={example}>
              <button
                type="button"
                onClick={() => void send(example)}
                className="rounded-xs border border-hairline px-2 py-1 text-left font-mono text-2xs text-text-muted hover:border-signal hover:text-signal"
              >
                {example}
              </button>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="mx-auto flex h-full min-h-0 w-full max-w-4xl flex-col px-6 py-6">
      <div className="min-h-0 flex-1 overflow-y-auto">
        {turns.map((turn, index) => {
          const { steps, text, error } = reduceEvents(turn.events);
          return (
            <article key={index} className="mb-8">
              <p className="font-mono text-2xs text-text-faint">you asked</p>
              <p className="mt-0.5 text-sm">{turn.question}</p>
              <Trace
                steps={steps}
                text={text}
                error={error}
                running={running && index === turns.length - 1}
              />
            </article>
          );
        })}
      </div>

      <div className="mt-4 shrink-0">{composer}</div>
    </div>
  );
}
