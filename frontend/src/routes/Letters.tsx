import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router";

import {
  ApiError,
  draftLetter,
  reviseLetter,
  type LetterResponse,
} from "../api/client";
import { ScoreBar, ScoreLegend } from "../components/ScoreBar";

/**
 * A draft beside the pieces of the author's history it was grounded in, each
 * with its retrieval score. The point is that every claim is auditable.
 *
 * Two ways to change it, and the difference matters. *Regenerate* throws this
 * letter away and rolls the dice again. *Revise* applies one instruction to
 * the text currently in the editor — including whatever you typed into it by
 * hand — which is how anyone actually works with a draft they mostly like.
 */

/** Openers, because the hard part of a revision box is knowing what to say to
 *  it. Chosen to be the four things you nearly always want. */
const SUGGESTIONS = [
  "Make it about a third shorter.",
  "Cut the third paragraph.",
  "Less formal, more direct.",
  "Say more about the distributed attention work.",
];

interface Revision {
  instruction: string;
  at: string;
}

export function Letters() {
  const { id = "" } = useParams();
  const [text, setText] = useState("");
  const [copied, setCopied] = useState(false);
  const [instruction, setInstruction] = useState("");
  // What has been asked for, so the panel reads as a conversation about this
  // letter rather than a box that forgets. Not persisted: the letter itself is
  // the artefact, and it is on disk.
  const [history, setHistory] = useState<Revision[]>([]);
  const [letter, setLetter] = useState<LetterResponse | null>(null);
  const instructionRef = useRef<HTMLTextAreaElement>(null);

  const draft = useMutation<LetterResponse, Error>({
    mutationFn: () => draftLetter(id),
    onSuccess: (data) => {
      setText(data.text);
      setLetter(data);
      setHistory([]);
    },
  });

  const revise = useMutation<LetterResponse, Error, string>({
    // The editor's contents, not the saved copy: revising what is on disk
    // would silently throw away any hand edits made since drafting.
    mutationFn: (what: string) => reviseLetter(id, what, text),
    onSuccess: (data, what) => {
      setText(data.text);
      setLetter(data);
      setHistory((current) => [
        ...current,
        { instruction: what, at: new Date().toISOString() },
      ]);
      setInstruction("");
    },
  });

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  const grounding = letter?.grounding ?? [];
  const max = grounding.reduce((best, hit) => Math.max(best, hit.score), 0);
  const error = draft.error ?? revise.error;
  const busy = draft.isPending || revise.isPending;

  function ask(what: string) {
    const trimmed = what.trim();
    if (trimmed && text.trim() && !busy) revise.mutate(trimmed);
  }

  return (
    <div className="mx-auto grid h-full min-h-0 w-full max-w-6xl grid-cols-[1fr_22rem] gap-6 px-6 py-6">
      <section className="flex min-h-0 flex-col">
        <div className="flex shrink-0 items-baseline justify-between">
          <h1 className="font-mono text-xs text-text-muted">{id}</h1>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => draft.mutate()}
              disabled={busy}
              className="border border-signal px-3 py-1 font-mono text-2xs text-signal rounded-xs disabled:opacity-40"
              title={
                letter
                  ? "Throw this letter away and draft a new one from scratch"
                  : undefined
              }
            >
              {draft.isPending ? "drafting…" : letter ? "regenerate" : "draft"}
            </button>
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard.writeText(text);
                setCopied(true);
              }}
              disabled={!text}
              className="border border-hairline px-3 py-1 font-mono text-2xs text-text-muted rounded-xs disabled:opacity-40"
            >
              {copied ? "copied" : "copy"}
            </button>
          </div>
        </div>

        {error && (
          <div className="mt-4 border border-hairline p-4 text-xs">
            <div className="font-medium">
              {error instanceof ApiError && error.status === 503
                ? "The model is busy"
                : revise.error
                  ? "That revision could not be applied"
                  : "Drafting failed"}
            </div>
            <p className="mt-1 font-mono text-2xs text-text-muted">
              {error.message}
            </p>
            {error instanceof ApiError && error.status === 503 && (
              <p className="mt-3 text-text-faint">
                Nothing is wrong with the request. Press the button again in a
                moment — your text is untouched.
              </p>
            )}
          </div>
        )}

        {letter?.todos.length ? (
          <div className="mt-4 border border-hairline px-3 py-2 text-2xs">
            <span className="font-mono text-text-faint">
              {letter.todos.length} marker(s) left for you:
            </span>{" "}
            <span className="font-mono text-status-interviewing">
              {letter.todos.join(" · ")}
            </span>
          </div>
        ) : null}

        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Draft a letter to see it here. It stays editable."
          className="mt-4 min-h-0 flex-1 resize-none border border-hairline bg-transparent p-4 text-sm leading-relaxed rounded-xs placeholder:text-text-faint"
          aria-label="Letter draft"
        />

        {/* The revision box. Disabled until there is something to revise,
            because "make it shorter" has no meaning against an empty page. */}
        <div className="mt-3 shrink-0 border border-hairline p-3 rounded-xs">
          <div className="mb-2 flex flex-wrap gap-1">
            {SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => ask(suggestion)}
                disabled={!text.trim() || busy}
                className="rounded-xs border border-hairline px-1.5 py-0.5 font-mono text-2xs text-text-muted hover:border-signal hover:text-signal disabled:opacity-30"
              >
                {suggestion}
              </button>
            ))}
          </div>
          <div className="flex gap-2">
            <textarea
              ref={instructionRef}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              onKeyDown={(event) => {
                // Enter sends, shift+enter breaks a line: this is a message
                // box, not a document.
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  ask(instruction);
                }
              }}
              rows={2}
              disabled={!text.trim()}
              placeholder={
                text.trim()
                  ? "What should change? One thing at a time works best."
                  : "Draft a letter first, then ask for changes here."
              }
              className="min-h-0 flex-1 resize-none border border-hairline bg-transparent px-2 py-1 text-xs leading-relaxed rounded-xs placeholder:text-text-faint disabled:opacity-50"
              aria-label="Revision instruction"
            />
            <button
              type="button"
              onClick={() => ask(instruction)}
              disabled={!instruction.trim() || !text.trim() || busy}
              className="self-end border border-signal bg-signal/12 px-3 py-1 font-mono text-2xs text-signal rounded-xs disabled:opacity-40"
            >
              {revise.isPending ? "revising…" : "revise"}
            </button>
          </div>
          <p className="mt-1.5 font-mono text-2xs text-text-faint">
            revises the text above, hand edits included — it cannot invent a
            fact your write-ups do not have
          </p>
        </div>
      </section>

      <aside className="flex min-h-0 flex-col overflow-y-auto">
        <h2 className="mb-2 font-mono text-2xs uppercase tracking-wide text-text-faint">
          grounded in
        </h2>
        {grounding.length === 0 ? (
          <p className="text-xs text-text-faint">
            Nothing yet. Every claim in the draft will be traceable to one of
            these extracts.
          </p>
        ) : (
          <div className="space-y-4">
            <ScoreLegend />
            {grounding.map((hit) => (
              <div key={hit.chunk_id} className="border-b border-hairline pb-3">
                <div className="mb-1 font-mono text-2xs text-text-faint">
                  #{hit.rank} {hit.profile_doc}
                </div>
                <ScoreBar hit={hit} max={max} />
                <p className="mt-2 text-xs leading-relaxed text-text-muted">
                  {hit.text}
                </p>
              </div>
            ))}
          </div>
        )}

        {history.length > 0 && (
          <div className="mt-6">
            <h2 className="mb-2 font-mono text-2xs uppercase tracking-wide text-text-faint">
              changes asked for
            </h2>
            <ol className="space-y-1.5">
              {history.map((entry, index) => (
                <li
                  key={entry.at}
                  className="flex gap-2 text-xs text-text-muted"
                >
                  <span className="font-mono text-2xs tabular-nums text-text-faint">
                    {index + 1}
                  </span>
                  <span>{entry.instruction}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </aside>
    </div>
  );
}
