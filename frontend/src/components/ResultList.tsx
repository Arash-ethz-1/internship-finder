import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { setStatusBulk, type FoundPosting, type Status } from "../api/client";
import { ScoreBar, ScoreLegend } from "./ScoreBar";
import { StatusDot } from "./status";

/**
 * The agent's result list, and the one place in the app where several postings
 * are acted on at once.
 *
 * A posting arrives here as `found` — a search surfaced it, nobody has judged
 * it. Everything this component offers is a way to make that judgement: keep
 * it, get ready to apply, mark it done, or leave it alone. Leaving it alone is
 * a real outcome, which is why nothing here is preselected.
 */

/** What the bulk bar offers, in pipeline order. `found` is not here: it is
 *  where a posting starts, not somewhere you move one to.
 *
 *  "not for me" writes `not_relevant`, not `rejected`. It used to write
 *  `rejected`, which says a company turned you down — so triaging a search
 *  result made the pipeline read as rejections you never received, and put
 *  the posting in front of the email matcher as something a rejection letter
 *  could be about. */
const ACTIONS: { status: Status; label: string }[] = [
  { status: "interested", label: "keep" },
  { status: "applied", label: "applied" },
  { status: "not_relevant", label: "not for me" },
];

export function ResultList({ postings }: { postings: FoundPosting[] }) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [done, setDone] = useState<Record<string, Status>>({});

  // No reset effect: each search is its own trace step, so a new list is a new
  // instance of this component and the selection starts empty by construction.

  const apply = useMutation({
    mutationFn: ({ ids, status }: { ids: string[]; status: Status }) =>
      setStatusBulk(ids, status),
    onSuccess: (result, variables) => {
      setDone((current) => {
        const next = { ...current };
        for (const row of result.updated) next[row.posting_id] = variables.status;
        return next;
      });
      setSelected(new Set());
      void queryClient.invalidateQueries({ queryKey: ["postings"] });
      void queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  if (postings.length === 0) return null;

  const max = postings.reduce((best, p) => Math.max(best, p.score), 0);
  const allSelected = selected.size === postings.length;

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <section className="mt-4 border border-hairline">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-hairline px-3 py-2">
        <label className="flex items-center gap-2 font-mono text-2xs text-text-muted">
          <input
            type="checkbox"
            checked={allSelected}
            onChange={() =>
              setSelected(allSelected ? new Set() : new Set(postings.map((p) => p.posting_id)))
            }
            className="accent-signal"
            aria-label="Select every result"
          />
          {selected.size > 0 ? `${selected.size} selected` : `${postings.length} found`}
        </label>

        <div className="ml-auto flex flex-wrap items-center gap-1">
          {ACTIONS.map((action) => (
            <button
              key={action.status}
              type="button"
              disabled={selected.size === 0 || apply.isPending}
              onClick={() =>
                apply.mutate({ ids: [...selected], status: action.status })
              }
              className="rounded-xs border border-hairline px-2 py-1 font-mono text-2xs text-text-muted enabled:hover:border-signal enabled:hover:text-signal disabled:opacity-35"
            >
              {action.label}
            </button>
          ))}
        </div>
      </header>

      {apply.error && (
        <p className="border-b border-hairline px-3 py-2 font-mono text-2xs text-status-interviewing">
          Nothing was changed: {apply.error.message}
        </p>
      )}

      <div className="px-3 py-2">
        <ScoreLegend />
      </div>

      <ol>
        {postings.map((posting) => (
          <ResultRow
            key={posting.posting_id}
            posting={posting}
            max={max}
            checked={selected.has(posting.posting_id)}
            settled={done[posting.posting_id]}
            onToggle={() => toggle(posting.posting_id)}
          />
        ))}
      </ol>
    </section>
  );
}

function ResultRow({
  posting,
  max,
  checked,
  settled,
  onToggle,
}: {
  posting: FoundPosting;
  max: number;
  checked: boolean;
  settled: Status | undefined;
  onToggle: () => void;
}) {
  return (
    <li
      className={`grid grid-cols-[1.25rem_1fr_13rem] items-start gap-3 border-t border-hairline px-3 py-2 ${
        settled ? "opacity-55" : ""
      } ${checked ? "bg-signal/8" : ""}`}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        className="mt-1 accent-signal"
        aria-label={`Select ${posting.title} at ${posting.company}`}
      />

      <div className="min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-2xs text-text-faint">#{posting.rank}</span>
          <span className="truncate text-xs font-medium">{posting.title}</span>
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-3 font-mono text-2xs text-text-muted">
          <span>{posting.company}</span>
          <span>{posting.remote ? "remote" : (posting.location ?? "—")}</span>
          <span>{posting.level}</span>
          <span className="tabular-nums">{posting.posted_at?.slice(0, 10) ?? "—"}</span>
          {posting.deadline && (
            <span className="text-status-interviewing">due {posting.deadline.slice(0, 10)}</span>
          )}
        </div>
        <p className="mt-1 line-clamp-2 text-2xs text-text-faint">{posting.excerpt}</p>

        <div className="mt-1.5 flex items-center gap-3">
          <a
            href={posting.url}
            target="_blank"
            rel="noreferrer"
            className="font-mono text-2xs text-signal underline-offset-2 hover:underline"
          >
            open posting ↗
          </a>
          <Link
            to={`/letters/${encodeURIComponent(posting.posting_id)}`}
            className="font-mono text-2xs text-signal underline-offset-2 hover:underline"
          >
            draft letter
          </Link>
          {settled ? (
            <span className="flex items-center gap-1.5 font-mono text-2xs text-text-muted">
              <StatusDot status={settled} />
              {settled}
            </span>
          ) : (
            <span className="font-mono text-2xs text-text-faint">{posting.posting_id}</span>
          )}
        </div>
      </div>

      <ScoreBar hit={{ ...posting, chunk_id: 0, profile_doc: null, ordinal: 0, text: "" }} max={max} />
    </li>
  );
}
