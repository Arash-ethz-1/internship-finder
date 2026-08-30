import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useParams } from "react-router";

import { ApiError, draftLetter, type LetterResponse } from "../api/client";
import { ScoreBar, ScoreLegend } from "../components/ScoreBar";

/**
 * A draft beside the pieces of the author's history it was grounded in, each
 * with its retrieval score. The point is that every claim is auditable.
 */
export function Letters() {
  const { id = "" } = useParams();
  const [text, setText] = useState("");
  const [copied, setCopied] = useState(false);

  const draft = useMutation<LetterResponse, Error>({
    mutationFn: () => draftLetter(id),
    onSuccess: (data) => setText(data.text),
  });

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  const grounding = draft.data?.grounding ?? [];
  const max = grounding.reduce((best, hit) => Math.max(best, hit.score), 0);
  const notImplemented = draft.error instanceof ApiError && draft.error.status === 501;

  return (
    <div className="mx-auto grid h-full min-h-0 w-full max-w-6xl grid-cols-[1fr_22rem] gap-6 px-6 py-6">
      <section className="flex min-h-0 flex-col">
        <div className="flex shrink-0 items-baseline justify-between">
          <h1 className="font-mono text-xs text-text-muted">{id}</h1>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => draft.mutate()}
              disabled={draft.isPending}
              className="border border-signal px-3 py-1 font-mono text-2xs text-signal rounded-xs disabled:opacity-40"
            >
              {draft.isPending ? "drafting…" : draft.data ? "regenerate" : "draft"}
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

        {draft.error && (
          <div className="mt-4 border border-hairline p-4 text-xs">
            <div className="font-medium">
              {notImplemented ? "Retrieval is not written yet" : "Drafting failed"}
            </div>
            <p className="mt-1 font-mono text-2xs text-text-muted">{draft.error.message}</p>
            {notImplemented && (
              <p className="mt-3 text-text-faint">
                A letter is grounded in retrieved pieces of your own project write-ups, so it needs{" "}
                <span className="font-mono">retrieval.search</span>. Until that exists there is
                nothing honest to write from.
              </p>
            )}
          </div>
        )}

        {draft.data?.todos.length ? (
          <div className="mt-4 border border-hairline px-3 py-2 text-2xs">
            <span className="font-mono text-text-faint">
              {draft.data.todos.length} marker(s) left for you:
            </span>{" "}
            <span className="font-mono text-status-interviewing">
              {draft.data.todos.join(" · ")}
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
      </section>

      <aside className="flex min-h-0 flex-col overflow-y-auto">
        <h2 className="mb-2 font-mono text-2xs uppercase tracking-wide text-text-faint">
          grounded in
        </h2>
        {grounding.length === 0 ? (
          <p className="text-xs text-text-faint">
            Nothing yet. Every claim in the draft will be traceable to one of these extracts.
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
                <p className="mt-2 text-xs leading-relaxed text-text-muted">{hit.text}</p>
              </div>
            ))}
          </div>
        )}
      </aside>
    </div>
  );
}
