import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";

import { getPosting, setStatus, type Status } from "../api/client";
import { ErrorState, LoadingState } from "./states";
import { STATUS_KEYS, STATUS_LABELS, StatusLabel, statusStyle } from "./status";

/**
 * The right panel. Slides in over the grid rather than navigating away, so
 * the row you were on stays where it was.
 */
export function DetailPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { data, isPending, error } = useQuery({
    queryKey: ["posting", id],
    queryFn: () => getPosting(id),
  });

  const mutate = useMutation({
    mutationFn: (status: Status) => setStatus(id, status),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["posting", id] });
      void queryClient.invalidateQueries({ queryKey: ["postings"] });
      void queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  return (
    <aside className="flex w-[32rem] shrink-0 flex-col overflow-y-auto border-l border-hairline">
      <div className="flex items-center justify-between border-b border-hairline px-4 py-2">
        <span className="font-mono text-2xs text-text-faint">{id}</span>
        <button
          type="button"
          onClick={onClose}
          className="rounded-xs px-1 text-xs text-text-faint hover:text-text"
          aria-label="Close detail panel (Escape)"
        >
          esc ✕
        </button>
      </div>

      {isPending && <LoadingState what="posting" />}
      {error && <ErrorState error={error} />}

      {data && (
        <div className="flex-1 px-4 py-4">
          <h2 className="text-base font-medium">{data.title}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-2xs text-text-muted">
            <span>{data.company}</span>
            <span>{data.location ?? "—"}</span>
            {data.remote && <span className="text-signal">remote</span>}
            <span>{data.level}</span>
            <span>{data.posted_at?.slice(0, 10) ?? "—"}</span>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-1">
            {STATUS_KEYS.map((status, index) => {
              const active = data.status === status;
              return (
                <button
                  key={status}
                  type="button"
                  onClick={() => mutate.mutate(status)}
                  className={`rounded-xs border px-2 py-1 font-mono text-2xs ${
                    active
                      ? "border-signal text-signal"
                      : "border-hairline text-text-muted hover:text-text"
                  }`}
                  title={`Press ${index + 1}`}
                >
                  <span className="text-text-faint">{index + 1}</span> {STATUS_LABELS[status]}
                </button>
              );
            })}
            <button
              type="button"
              onClick={() => mutate.mutate("declined")}
              className={`rounded-xs border px-2 py-1 font-mono text-2xs ${
                data.status === "declined"
                  ? "border-signal text-signal"
                  : "border-hairline text-text-muted hover:text-text"
              }`}
              title="No key: declined is rare, and the design specifies six keys"
            >
              declined
            </button>
          </div>

          <div className="mt-3 flex items-center gap-4">
            <StatusLabel status={data.status} />
            <a
              href={data.url}
              target="_blank"
              rel="noreferrer"
              className="font-mono text-2xs text-signal underline-offset-2 hover:underline"
            >
              open posting ↗
            </a>
            <Link
              to={`/letters/${encodeURIComponent(id)}`}
              className="font-mono text-2xs text-signal underline-offset-2 hover:underline"
            >
              draft letter
            </Link>
          </div>

          {data.history.length > 0 && (
            <div className="mt-5 border-t border-hairline pt-3">
              <div className="mb-2 font-mono text-2xs uppercase tracking-wide text-text-faint">
                history
              </div>
              <ol className="space-y-1">
                {data.history.map((change, index) => (
                  <li key={index} className="flex gap-3 font-mono text-2xs text-text-muted">
                    <span className="tabular-nums text-text-faint">
                      {change.changed_at.slice(0, 10)}
                    </span>
                    <span>
                      {change.from_status ?? "untriaged"} → {change.to_status}
                    </span>
                    {change.note && <span className="text-text-faint">{change.note}</span>}
                  </li>
                ))}
              </ol>
            </div>
          )}

          <div className="mt-5 border-t border-hairline pt-3">
            <div className="mb-2 font-mono text-2xs uppercase tracking-wide text-text-faint">
              description
            </div>
            <p className="whitespace-pre-wrap text-xs leading-relaxed text-text-muted">
              {data.body}
            </p>
          </div>
        </div>
      )}
    </aside>
  );
}

export { statusStyle };
