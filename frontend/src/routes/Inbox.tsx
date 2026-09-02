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
import { SyncControl } from "../components/SyncControl";
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
    <span
      className="flex items-center gap-1.5"
      title={`model confidence ${pct}%`}
    >
      <span className="h-1 w-10 overflow-hidden rounded-xs bg-surface-sunken">
        <span
          className={`block h-full ${pct >= 70 ? "bg-signal" : "bg-text-faint"}`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="font-mono text-2xs tabular-nums text-text-faint">
        {pct}%
      </span>
    </span>
  );
}

/**
 * What the classifier decided, as a chip.
 *
 * Uniform faint uppercase mono made every row look like every other row, which
 * is the opposite of what a review queue needs: the first thing you want to
 * know is what kind of news this is. The colours are the status ramp's, so a
 * rejection here and a rejected posting on /postings read the same.
 */
const CLASSIFICATION: Record<string, { label: string; chip: string }> = {
  rejection: {
    label: "rejection",
    chip: "border-status-rejected/50 text-text-muted",
  },
  interview: {
    label: "interview",
    chip: "border-status-interviewing/55 bg-status-interviewing/12 text-status-interviewing",
  },
  offer: {
    label: "offer",
    chip: "border-status-offer/55 bg-status-offer/12 text-status-offer",
  },
  other: {
    label: "not about an application",
    chip: "border-hairline text-text-faint",
  },
};

function Row({ item }: { item: InboxSuggestion }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<Status | "">(
    item.suggested_status ?? "",
  );
  const [postingId, setPostingId] = useState(item.posting_id ?? "");

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["inbox"] });
    void queryClient.invalidateQueries({ queryKey: ["postings"] });
    void queryClient.invalidateQueries({ queryKey: ["stats"] });
  };

  const accept = useMutation({
    mutationFn: () =>
      acceptSuggestion(
        item.id,
        postingId || undefined,
        (status || undefined) as Status,
      ),
    onSuccess: invalidate,
  });
  const dismiss = useMutation({
    mutationFn: () => dismissSuggestion(item.id),
    onSuccess: invalidate,
  });

  const busy = accept.isPending || dismiss.isPending;
  const unmatched = !item.posting_id;

  const kind = CLASSIFICATION[item.classification ?? ""] ?? {
    label: item.classification ?? "unread",
    chip: "border-hairline text-text-faint",
  };

  return (
    // A thicker rule and real vertical air: at py-3 with everything in 12px
    // grey, one message ran into the next and the queue read as a wall.
    <article className="border-b-2 border-hairline px-4 py-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span
          className={`inline-flex items-center rounded-xs border px-1.5 py-0.5 font-mono text-2xs whitespace-nowrap ${kind.chip}`}
        >
          {kind.label}
        </span>
        <Confidence value={item.confidence} />
        <span className="ml-auto font-mono text-2xs tabular-nums text-text-faint">
          {item.received_at?.slice(0, 10) ?? "—"}
        </span>
      </div>

      {/* The subject is how you recognise a message, so it is the one thing
          here at reading size rather than at data size. */}
      <div className="mt-2 text-sm font-medium">
        {item.subject || "(no subject)"}
      </div>
      <div className="mt-0.5 font-mono text-2xs text-text-muted">
        {item.sender}
      </div>
      {item.snippet && (
        <p className="mt-2 max-w-prose border-l border-hairline pl-3 text-xs leading-relaxed text-text-muted">
          {item.snippet}
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        {item.posting_id ? (
          <span className="flex items-baseline gap-2 text-xs">
            <span className="font-mono text-2xs text-text-faint">
              {item.posting_id}
            </span>
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
              {item.company_guess ? ` — looks like ${item.company_guess}` : ""}.
              Which posting?
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

      {/* The decision, on its own ground. Separating what the mail said from
          what you are about to do to the application is most of what makes a
          row scannable. */}
      <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xs bg-surface-sunken px-3 py-2">
        <label className="flex items-center gap-2 text-2xs text-text-muted">
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

/** What the queue can be narrowed to. `min` is the classifier's confidence. */
const KINDS = [
  { value: "", label: "everything" },
  { value: "rejection", label: "rejections" },
  { value: "interview", label: "interviews" },
  { value: "offer", label: "offers" },
  { value: "other", label: "other" },
] as const;

const CONFIDENCES = [
  { value: 0, label: "any" },
  { value: 0.5, label: "50%+" },
  { value: 0.7, label: "70%+" },
  { value: 0.9, label: "90%+" },
] as const;

export function Inbox() {
  const [kind, setKind] = useState("");
  const [minConfidence, setMinConfidence] = useState(0);

  const { data, isPending, error } = useQuery({
    queryKey: ["inbox", kind, minConfidence],
    queryFn: () => getInbox({ classification: kind, minConfidence }),
  });

  const filters = (
    <div className="flex flex-wrap items-center gap-2 font-mono text-2xs text-text-faint">
      <label className="flex items-center gap-1.5">
        show
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          className="rounded-xs border border-hairline bg-transparent px-2 py-0.5 font-mono text-2xs text-text"
        >
          {KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1.5">
        confidence
        <select
          value={minConfidence}
          onChange={(e) => setMinConfidence(Number(e.target.value))}
          className="rounded-xs border border-hairline bg-transparent px-2 py-0.5 font-mono text-2xs text-text"
        >
          {CONFIDENCES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  );

  if (isPending) return <LoadingState what="the review queue" />;
  if (error) return <ErrorState error={error} />;

  // `pending` is the unfiltered count, so this is what the filters are hiding
  // rather than what does not exist.
  const hidden = Math.max(0, data.pending - data.items.length);
  // A suggestion with no suggested_status is one the classifier read and
  // decided was not about an application. Still a result, just not a decision.
  const actionable = data.items.filter((item) => item.suggested_status);
  const rest = data.items.filter((item) => !item.suggested_status);
  const narrowed = kind !== "" || minConfidence > 0;

  if (data.items.length === 0 && narrowed) {
    return (
      <div className="mx-auto h-full w-full max-w-4xl overflow-y-auto px-4 pt-6">
        <h1 className="text-lg font-medium">Inbox</h1>
        <div className="mt-3"><SyncControl /></div>
        <div className="mt-3">{filters}</div>
        <p className="mt-6 max-w-prose text-xs text-text-muted">
          Nothing matches this filter. {data.pending} suggestion
          {data.pending === 1 ? " is" : "s are"} waiting behind it.
        </p>
        <button
          type="button"
          onClick={() => {
            setKind("");
            setMinConfidence(0);
          }}
          className="mt-3 rounded-xs border border-hairline px-2 py-1 font-mono text-2xs text-text-muted hover:border-signal hover:text-signal"
        >
          show everything
        </button>
      </div>
    );
  }

  if (data.items.length === 0) {
    return (
      <EmptyState
        title="Nothing to review"
        detail={
          "Suggestions appear here after a mailbox sync reads replies to applications " +
          "you have already recorded. Nothing is ever applied automatically — every " +
          "status change starts as a row on this page that you accept."
        }
        command={
          "cd backend\nuv run python -m agent_app.cli sync-email --login"
        }
      />
    );
  }

  return (
    <div className="mx-auto h-full w-full max-w-4xl overflow-y-auto">
      <div className="flex items-baseline gap-3 px-4 pt-6">
        <h1 className="text-lg font-medium">Inbox</h1>
        <div className="mt-3"><SyncControl /></div>
        <span className="font-mono text-xs tabular-nums text-text-muted">
          {data.pending} waiting
        </span>
      </div>
      <p className="max-w-prose px-4 pt-1 text-xs text-text-muted">
        Read-only from Gmail. Nothing below has been applied — accepting one is
        what moves the application, and records which email caused it.
      </p>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 pt-3 pb-4">
        {filters}
        {/* "everything" returning one visible row is confusing unless the page
            says where the rest went: the ones the classifier judged not to be
            about an application are real results, they are just folded away. */}
        <span className="font-mono text-2xs text-text-faint">
          {actionable.length} to act on
          {rest.length > 0 && ` · ${rest.length} not about an application`}
          {hidden > 0 && ` · ${hidden} hidden by this filter`}
        </span>
      </div>

      <div className="border-t border-hairline">
        {actionable.map((item) => (
          <Row key={item.id} item={item} />
        ))}
      </div>

      {rest.length > 0 && (
        <details
          className="px-4 py-4"
          open={kind === "other" || actionable.length === 0}
        >
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
