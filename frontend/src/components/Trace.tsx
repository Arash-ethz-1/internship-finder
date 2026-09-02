import type { AgentEvent, FoundPosting, SearchHit } from "../api/client";
import { Answer } from "./Answer";
import { ResultList } from "./ResultList";
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
/** A turn, in the order it happened: the agent's own words and its tool calls
 *  interleaved. Collecting all the text into one blob and printing it under
 *  the results put "let me check the postings" *after* the postings. */
export type TraceBlock =
  | { kind: "text"; id: number; text: string }
  | { kind: "step"; id: number; step: TraceStep };

export function reduceEvents(events: AgentEvent[]): {
  steps: TraceStep[];
  blocks: TraceBlock[];
  text: string;
  error: string | null;
  done: boolean;
} {
  const steps: TraceStep[] = [];
  const blocks: TraceBlock[] = [];
  let text = "";
  let error: string | null = null;
  let done = false;

  for (const event of events) {
    switch (event.kind) {
      case "tool_call": {
        const step: TraceStep = {
          id: steps.length,
          name: event.name,
          input: event.input,
          pending: true,
        };
        steps.push(step);
        blocks.push({ kind: "step", id: blocks.length, step });
        break;
      }
      case "tool_result": {
        // Fill in the most recent pending call with this name.
        for (let i = steps.length - 1; i >= 0; i--) {
          if (steps[i].pending && steps[i].name === event.name) {
            const filled = {
              ...steps[i],
              output: event.output,
              ms: event.ms,
              pending: false,
            };
            steps[i] = filled;
            const at = blocks.findIndex(
              (b) => b.kind === "step" && b.step.id === filled.id,
            );
            if (at >= 0) blocks[at] = { kind: "step", id: at, step: filled };
            break;
          }
        }
        break;
      }
      case "text": {
        text += event.delta;
        // One block per contiguous run of text, so a sentence written before a
        // tool call stays above it.
        const last = blocks[blocks.length - 1];
        if (last && last.kind === "text") last.text += event.delta;
        else
          blocks.push({ kind: "text", id: blocks.length, text: event.delta });
        break;
      }
      case "done":
        done = true;
        break;
      case "error":
        error = event.detail;
        break;
    }
  }

  return { steps, blocks, text, error, done };
}

/** `find_postings` returns whole postings; a posting_id with no chunk_id is
 *  what tells them apart from the raw chunk hits retrieval returns. */
function postingsFrom(output: unknown): FoundPosting[] {
  if (!Array.isArray(output)) return [];
  return output.filter(
    (item): item is FoundPosting =>
      typeof item === "object" &&
      item !== null &&
      "posting_id" in item &&
      "excerpt" in item,
  );
}

function hitsFrom(output: unknown): SearchHit[] {
  if (!Array.isArray(output)) return [];
  return output.filter(
    (item): item is SearchHit =>
      typeof item === "object" && item !== null && "component_scores" in item,
  );
}

function ToolStep({ step }: { step: TraceStep }) {
  const postings = postingsFrom(step.output);
  const hits = postings.length > 0 ? [] : hitsFrom(step.output);
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
            <div
              key={hit.chunk_id}
              className="grid grid-cols-[1fr_14rem] items-center gap-3"
            >
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

      {postings.length > 0 && <ResultList key={step.id} postings={postings} />}

      {!step.pending &&
        hits.length === 0 &&
        postings.length === 0 &&
        step.output !== undefined && (
          <pre className="mt-2 max-h-40 overflow-auto font-mono text-2xs text-text-muted">
            {JSON.stringify(step.output, null, 2)}
          </pre>
        )}
    </li>
  );
}

export function Trace({
  blocks,
  error,
  running,
}: {
  blocks: TraceBlock[];
  error: string | null;
  running: boolean;
}) {
  if (error) {
    return (
      <div className="border border-hairline p-4 text-xs">
        <div className="font-medium">The agent could not run</div>
        <p className="mt-1 text-text-muted">{error}</p>
        <p className="mt-3 text-text-faint">
          This is expected until <span className="font-mono">run_agent</span> is
          written. Everything else — the postings grid, filters, status changes
          — works now.
        </p>
      </div>
    );
  }

  if (blocks.length === 0 && !running) return null;

  return (
    <div>
      {blocks.map((block) =>
        block.kind === "text" ? (
          <Answer key={block.id} text={block.text} />
        ) : (
          <ol key={block.id} className="mt-4 border-t border-hairline">
            <ToolStep step={block.step} />
          </ol>
        ),
      )}
      {running && blocks.length === 0 && (
        <p className="py-3 font-mono text-2xs text-text-faint">thinking…</p>
      )}
    </div>
  );
}
