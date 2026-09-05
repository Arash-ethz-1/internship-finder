import { useQuery } from "@tanstack/react-query";

import { getStats } from "../api/client";
import { Trace, reduceEvents } from "../components/Trace";
import {
  reset,
  send,
  setInput,
  stop,
  useChatSession,
} from "../state/chatSession";

/**
 * The agent, and the app's front door.
 *
 * Say what you are looking for; the agent builds a list you can act on. Turns
 * accumulate rather than replacing each other, and the conversation history is
 * carried forward — without it, "mark the first three" has nothing to refer to.
 *
 * The conversation itself lives in `state/chatSession`, outside React's tree,
 * because this is a route: opening a posting the agent just found unmounts
 * this component, and a transcript in `useState` would not survive it.
 */

const EXAMPLES = [
  "ML research internships in Zurich",
  "quantitative researcher internships, Europe",
  "AI agent engineering internships that mention LLMs",
  "what have I applied to so far?",
];

export function Chat() {
  const { input, turns, running } = useChatSession();
  const stats = useQuery({ queryKey: ["stats"], queryFn: getStats });

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
          onClick={stop}
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
          postings and gives you a list you can keep, open, or draft a letter
          for. Anything you have already triaged is left out, so the same
          posting is never offered twice.
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
      {/* The transcript now outlives this component, so there has to be a way
          back to an empty one. */}
      <div className="mb-4 flex shrink-0 items-baseline gap-3 border-b border-hairline pb-2">
        <span className="font-mono text-2xs text-text-faint">
          {turns.length} turn{turns.length === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          onClick={reset}
          className="ml-auto font-mono text-2xs text-text-faint hover:text-signal"
        >
          new conversation
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {turns.map((turn, index) => {
          const { blocks, error } = reduceEvents(turn.events);
          return (
            /* A turn is a question, a lot of results, and often several
               hundred pixels of trace. Without a frame the next question
               reads as more output from the last one, and the thing you
               typed -- the only part of the page you wrote -- is the
               easiest thing to lose. So: a numbered rule opens each turn,
               and the question sits in a tinted block against the accent. */
            <article key={index} className="mb-10 scroll-mt-4">
              <div className="mb-3 flex items-center gap-3">
                <span className="shrink-0 font-mono text-2xs text-text-faint">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className="h-px flex-1 bg-hairline" />
              </div>

              <div className="border-l-2 border-signal bg-surface-sunken py-2 pr-3 pl-3">
                <p className="font-mono text-2xs text-signal">you asked</p>
                <p className="mt-1 text-sm break-words">{turn.question}</p>
              </div>

              <Trace
                blocks={blocks}
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
