import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { ApiError, getSyncStatus, startSync } from "../api/client";

/**
 * Running a mailbox sync from the dashboard.
 *
 * This used to be `cli sync-email` only, on the grounds that a button making a
 * page load wait on Google is a bad button. That was right about the mechanism
 * and wrong about the conclusion: the work runs as a background job, this
 * polls a status endpoint, and no request is ever held open.
 *
 * The rule that mattered is untouched and stated on the control itself: a sync
 * only ever writes *suggestions*. Nothing moves until someone accepts a row.
 */
export function SyncControl() {
  const queryClient = useQueryClient();

  const status = useQuery({
    queryKey: ["sync"],
    queryFn: getSyncStatus,
    // Poll only while something is happening. A dashboard that talks to its
    // own backend once a second forever is a dashboard nobody leaves open.
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 1500 : false,
    staleTime: 0,
  });

  const start = useMutation({
    mutationFn: () => startSync(),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["sync"] }),
  });

  const state = status.data;
  const running = state?.status === "running" || start.isPending;

  // When a run finishes, the queue it filled is stale. In an effect rather
  // than in the render body: invalidating during render would re-enter the
  // query client on every paint.
  const finishedAt = state?.status === "done" ? state.finished_at : null;
  useEffect(() => {
    if (finishedAt) {
      void queryClient.invalidateQueries({ queryKey: ["inbox"] });
    }
  }, [finishedAt, queryClient]);

  if (state && !state.authorised) {
    return (
      <div className="border border-hairline px-3 py-2 text-2xs rounded-xs">
        <span className="text-text-muted">Gmail is not connected.</span>{" "}
        <span className="font-mono text-text-faint">
          uv run python -m agent_app.cli sync-email --login
        </span>
        <p className="mt-1 text-text-faint">
          Read-only, one scope. The sign-in opens a browser window, so it has to
          be started from a terminal once.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        onClick={() => start.mutate()}
        disabled={running}
        className="border border-signal bg-signal/12 px-3 py-1 font-mono text-2xs text-signal rounded-xs disabled:opacity-40"
      >
        {running ? "checking mail…" : "check mail"}
      </button>

      <span className="font-mono text-2xs text-text-faint">
        only ever suggests — nothing moves until you accept it
      </span>

      {start.error && (
        <span className="font-mono text-2xs text-status-rejected">
          {start.error instanceof ApiError
            ? start.error.detail
            : String(start.error)}
        </span>
      )}

      {state?.status === "error" && (
        <span className="font-mono text-2xs text-status-rejected">
          {state.error}
        </span>
      )}

      {state?.status === "done" && state.report && (
        <span className="font-mono text-2xs text-text-faint">
          {state.report.skipped_reason ??
            `${state.report.fetched} examined · ${state.report.matched} matched · ` +
              `${state.report.suggestions} suggested`}
        </span>
      )}
    </div>
  );
}
