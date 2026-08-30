import type { AgentEvent, SearchHit } from "../api/client";
import { ScoreBar, ScoreLegend } from "./ScoreBar";

/**
 * The agent's work, shown as it happens.
 *
 * Each tool call appears the moment it is issued and fills in when its result
 * arrives. This is the one orchestrated moment in the app: rows fade in as
 * the stream delivers them, and nothing else animates.
 */

export interface TraceStep {
  id: number;
  name: string;
  input: Record<string, unknown>;
  output?: unknown;
  ms?: number;
  pending: boolean;
}

/** Fold the event stream into the steps the panel renders. */
export function reduceEvents(events: AgentEvent[]): {
  steps: TraceStep[];
  text: string;
  error: string | null;
  done: boolean;
} {
  const steps: TraceStep[] = [];
  let text = "";
  let error: string | null = null;
  let done = false;

  for (const event of events) {
    switch (event.kind) {
      case "tool_call":
        steps.push({
          id: steps.length,
          name: event.name,
          input: event.input,
          pending: true,
        });
        break;
      case "tool_result": {
        // Fill in the most recent pending call with this name.
        for (let i = steps.length - 1; i >= 0; i--) {
          if (steps[i].pending && steps[i].name === event.name) {
            steps[i] = { ...steps[i], output: event.output, ms: event.ms, pending: false };
            break;
          }
        }
        break;
      }
      case "text":
        text += event.delta;
        break;
      case "done":
        done = true;
        break;
      case "error":
        error = event.detail;
        break;
    }
  }

  return { steps, text, error, done };
}

function hitsFrom(output: unknown): SearchHit[] {
  if (!Array.isArray(output)) return [];
  return output.filter(
    (item): item is SearchHit =>
      typeof item === "object" && item !== null && "component_scores" in item,
  );
}

function ToolStep({ step }: { step: TraceStep }) {
  const hits = hitsFrom(step.output);
  const max = hits.reduce((best, hit) => Math.max(best, hit.score), 0);

  return (
    <li className="animate-trace-in border-b border-hairline py-3">
      <div className="flex items-baseline justify-between gap-4">
        <span className="font-mono text-xs">
          <span className="text-text-faint">▸ </span>
          {step.name}
        </span>
        <span className="font-mono text-2xs tabular-nums text-text-faint">
          {step.pending ? "running…" : `${step.ms ?? 0}ms`}
        </span>
      </div>

      <pre className="mt-1 overflow-x-auto font-mono text-2xs text-text-muted">
        {JSON.stringify(step.input)}
      </pre>

      {hits.length > 0 && (
        <div className="mt-3 space-y-2">
          <ScoreLegend />
          {hits.map((hit) => (
            <div key={hit.chunk_id} className="grid grid-cols-[1fr_14rem] items-center gap-3">
              <div className="min-w-0">
                <div className="truncate font-mono text-2xs text-text-faint">
                  #{hit.rank} {hit.posting_id ?? hit.profile_doc}
                </div>
                <div className="truncate text-xs">{hit.text}</div>
              </div>
              <ScoreBar hit={hit} max={max} />
            </div>
          ))}
        </div>
      )}

      {!step.pending && hits.length === 0 && step.output !== undefined && (
        <pre className="mt-2 max-h-40 overflow-auto font-mono text-2xs text-text-muted">
          {JSON.stringify(step.output, null, 2)}
        </pre>
      )}
    </li>
  );
}

export function Trace({
  steps,
  text,
  error,
  running,
}: {
  steps: TraceStep[];
  text: string;
  error: string | null;
  running: boolean;
}) {
  if (error) {
    return (
      <div className="border border-hairline p-4 text-xs">
        <div className="font-medium">The agent could not run</div>
        <p className="mt-1 text-text-muted">{error}</p>
        <p className="mt-3 text-text-faint">
          This is expected until <span className="font-mono">run_agent</span> is written. Everything
          else — the postings grid, filters, status changes — works now.
        </p>
      </div>
    );
  }

  if (steps.length === 0 && !text && !running) return null;

  return (
    <div>
      {steps.length > 0 && (
        <ol className="border-t border-hairline">
          {steps.map((step) => (
            <ToolStep key={step.id} step={step} />
          ))}
        </ol>
      )}
      {text && <p className="mt-4 whitespace-pre-wrap text-sm">{text}</p>}
      {running && steps.length === 0 && !text && (
        <p className="py-3 font-mono text-2xs text-text-faint">thinking…</p>
      )}
    </div>
  );
}
