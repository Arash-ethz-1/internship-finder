import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  acceptSuggestion,
  dismissSuggestion,
  getInbox,
  STATUSES,
  type InboxSuggestion,
  type Status,
} from "../api/client";
import { EmptyState, ErrorState, LoadingState } from "../components/states";
import { STATUS_LABELS, StatusLabel } from "../components/status";

/**
 * The review queue: what the classifier read in the mailbox, and what it
 * thinks each message means.
 *
 * The design constraint, from PLAN.md: nothing here has already happened. Every
 * row is a proposal, and the only way an application moves is a person
 * pressing accept. A wrongly auto-applied `rejected` is worse than no
 * automation at all — you stop checking a company that wanted to interview
 * you — so accept and dismiss are equally weighted, and the evidence the
 * decision rests on (who sent it, what it said, how sure the model was) is on
 * the row rather than behind a click.
 */

/** Confidence, as a small bar. Same visual grammar as the retrieval trace. */
function Confidence({ value }: { value: number | null }) {
  const pct = Math.round((value ?? 0) * 100);
  return (
    <span className="flex items-center gap-1.5" title={`model confidence ${pct}%`}>
      <span className="h-1 w-10 overflow-hidden rounded-xs bg-surface-sunken">
        <span
          className={`block h-full ${pct >= 70 ? "bg-signal" : "bg-text-faint"}`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="font-mono text-2xs tabular-nums text-text-faint">{pct}%</span>
    </span>
  );
}

const CLASSIFICATION_LABEL: Record<string, string> = {
  rejection: "rejection",
  interview: "interview",
  offer: "offer",
  other: "not about an application",
};

function Row({ item }: { item: InboxSuggestion }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<Status | "">(item.suggested_status ?? "");
  const [postingId, setPostingId] = useState(item.posting_id ?? "");

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["inbox"] });
    void queryClient.invalidateQueries({ queryKey: ["postings"] });
    void queryClient.invalidateQueries({ queryKey: ["stats"] });
  };

  const accept = useMutation({
    mutationFn: () =>
      acceptSuggestion(item.id, postingId || undefined, (status || undefined) as Status),
    onSuccess: invalidate,
  });
  const dismiss = useMutation({
    mutationFn: () => dismissSuggestion(item.id),
    onSuccess: invalidate,
  });

  const busy = accept.isPending || dismiss.isPending;
  const unmatched = !item.posting_id;

  return (
    <article className="border-b border-hairline px-4 py-3">
      <div className="flex items-baseline gap-3">
        <span className="font-mono text-2xs uppercase tracking-wide text-text-faint">
          {CLASSIFICATION_LABEL[item.classification ?? ""] ?? item.classification}
        </span>
        <Confidence value={item.confidence} />
        <span className="ml-auto font-mono text-2xs text-text-faint">
          {item.received_at?.slice(0, 10) ?? "—"}
        </span>
      </div>

      <div className="mt-1.5 text-xs font-medium">{item.subject || "(no subject)"}</div>
      <div className="mt-0.5 font-mono text-2xs text-text-faint">{item.sender}</div>
      {item.snippet && (
        <p className="mt-1.5 max-w-prose text-2xs text-text-muted">{item.snippet}</p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        {item.posting_id ? (
          <span className="flex items-baseline gap-2 text-xs">
            <span className="font-mono text-2xs text-text-faint">{item.posting_id}</span>
            <span>
              {item.company} — {item.title}
            </span>
            {item.current_status && (
              <StatusLabel status={item.current_status as Status} />
            )}
          </span>
        ) : (
          <span className="flex items-center gap-2 text-xs text-text-muted">
            <span>
              Not matched
              {item.company_guess ? ` — looks like ${item.company_guess}` : ""}. Which posting?
            </span>
            <input
              value={postingId}
              onChange={(e) => setPostingId(e.target.value)}
              placeholder="greenhouse:12345"
              aria-label="Posting id this email is about"
              className="w-48 border border-hairline bg-transparent px-2 py-0.5 font-mono text-2xs rounded-xs placeholder:text-text-faint"
            />
          </span>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-2xs text-text-faint">
          set to
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value as Status | "")}
            className="border border-hairline bg-transparent px-2 py-0.5 font-mono text-2xs rounded-xs"
          >
            <option value="">choose…</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        </label>

        <button
          type="button"
          onClick={() => accept.mutate()}
          disabled={busy || !status || (unmatched && !postingId)}
          className="border border-signal px-3 py-1 font-mono text-2xs text-signal rounded-xs disabled:opacity-40"
        >
          {accept.isPending ? "applying…" : "accept"}
        </button>
        <button
          type="button"
          onClick={() => dismiss.mutate()}
          disabled={busy}
          className="border border-hairline px-3 py-1 font-mono text-2xs text-text-muted rounded-xs disabled:opacity-40"
        >
          dismiss
        </button>

        {accept.error && (
          <span className="font-mono text-2xs text-status-interviewing">
            {accept.error.message}
          </span>
        )}
      </div>
    </article>
  );
}

export function Inbox() {
  const { data, isPending, error } = useQuery({
    queryKey: ["inbox"],
    queryFn: () => getInbox(true),
  });

  if (isPending) return <LoadingState what="the review queue" />;
  if (error) return <ErrorState error={error} />;

  if (data.items.length === 0) {
    return (
      <EmptyState
        title="Nothing to review"
        detail={
          "Suggestions appear here after a mailbox sync reads replies to applications " +
          "you have already recorded. Nothing is ever applied automatically — every " +
          "status change starts as a row on this page that you accept."
        }
        command={"cd backend\nuv run python -m agent_app.cli sync-email --login"}
      />
    );
  }

  const actionable = data.items.filter((item) => item.suggested_status);
  const rest = data.items.filter((item) => !item.suggested_status);

  return (
    <div className="mx-auto h-full w-full max-w-4xl overflow-y-auto">
      <div className="flex items-baseline gap-3 px-4 pt-6">
        <h1 className="text-lg font-medium">Inbox</h1>
        <span className="font-mono text-xs tabular-nums text-text-muted">
          {data.pending} waiting
        </span>
      </div>
      <p className="max-w-prose px-4 pt-1 pb-4 text-xs text-text-muted">
        Read-only from Gmail. Nothing below has been applied — accepting one is what
        moves the application, and records which email caused it.
      </p>

      <div className="border-t border-hairline">
        {actionable.map((item) => (
          <Row key={item.id} item={item} />
        ))}
      </div>

      {rest.length > 0 && (
        <details className="px-4 py-4">
          <summary className="cursor-pointer font-mono text-2xs uppercase tracking-wide text-text-faint">
            {rest.length} read and judged not to be about an application
          </summary>
          <div className="mt-2 border-t border-hairline">
            {rest.map((item) => (
              <Row key={item.id} item={item} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
