import type {
  AgentEvent,
  FoundPosting,
  ScreenedPosting,
  SearchHit,
} from "../api/client";
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

/** The rows `find_postings` judged to be a different kind of job.
 *
 *  They arrive in the same array as the results, which is why `postingsFrom`
 *  above insists on `excerpt`: a screened-out row carries neither an excerpt
 *  nor scores, so it cannot reach `ResultList` and cannot be bulk-triaged by
 *  accident. */
function screenedOutFrom(output: unknown): ScreenedPosting[] {
  if (!Array.isArray(output)) return [];
  return output.filter(
    (item): item is ScreenedPosting =>
      typeof item === "object" && item !== null && "screened_out" in item,
  );
}

function hitsFrom(output: unknown): SearchHit[] {
  if (!Array.isArray(output)) return [];
  return output.filter(
    (item): item is SearchHit =>
      typeof item === "object" && item !== null && "component_scores" in item,
  );
}

/** Folded away rather than deleted.
 *
 *  A screen that silently removed rows would be the one part of this app whose
 *  cost is invisible: you cannot miss a posting you were never shown. So the
 *  count is always on screen and one click opens the reasons, in the same
 *  shape the inbox uses for mail it decided was not about an application.
 *
 *  These postings are still untriaged in the database. The screen did not
 *  decide anything about them, and the next search will offer them again.
 *
 *  The link goes to the board rather than into the app: there is no route to
 *  a single posting, and checking a screen means reading the job. */
function ScreenedOutResult({ screened }: { screened: ScreenedPosting[] }) {
  return (
    <details className="mt-3 border-t border-hairline pt-2">
      <summary className="cursor-pointer font-mono text-2xs text-text-faint hover:text-text-muted">
        {screened.length} screened out
      </summary>
      <ul className="mt-2 space-y-1.5">
        {screened.map((posting) => (
          <li key={posting.posting_id} className="flex items-baseline gap-2">
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs text-text-muted">
                {posting.title}
              </div>
              <div className="truncate font-mono text-2xs text-text-faint">
                {posting.company} · {posting.screen_reason}
              </div>
            </div>
            <a
              href={posting.url}
              target="_blank"
              rel="noreferrer"
              className="shrink-0 font-mono text-2xs text-signal underline-offset-2 hover:underline"
            >
              open ↗
            </a>
          </li>
        ))}
      </ul>
    </details>
  );
}

/** What `corpus_stats` returns: a ceiling, not a ranking. */
interface CorpusStats {
  postings: number;
  companies: number;
  undecided: number;
  top_companies: { name: string; postings: number }[];
}

function corpusStatsFrom(output: unknown): CorpusStats | null {
  if (typeof output !== "object" || output === null || Array.isArray(output))
    return null;
  const value = output as Record<string, unknown>;
  if (typeof value.postings !== "number" || !Array.isArray(value.top_companies))
    return null;
  return value as unknown as CorpusStats;
}

/** The whole point of this tool is the shape of what exists, so show the
 *  numbers rather than the JSON they arrived in. `undecided` is the one that
 *  answers "is there anything left to show me". */
function CorpusStatsResult({ stats }: { stats: CorpusStats }) {
  return (
    <div className="mt-3 space-y-2">
      <div className="flex gap-4 font-mono text-2xs tabular-nums">
        <span>
          <span className="text-lg text-text">{stats.postings}</span>
          <span className="ml-1 text-text-faint">postings</span>
        </span>
        <span>
          <span className="text-lg text-text">{stats.companies}</span>
          <span className="ml-1 text-text-faint">companies</span>
        </span>
        <span>
          <span className="text-lg text-text">{stats.undecided}</span>
          <span className="ml-1 text-text-faint">undecided</span>
        </span>
      </div>
      {stats.top_companies.length > 0 && (
        <div className="font-mono text-2xs text-text-muted">
          {stats.top_companies
            .map((company) => `${company.name} (${company.postings})`)
            .join("  ·  ")}
        </div>
      )}
    </div>
  );
}

/** What `past_decisions` returns. Deliberately not the FoundPosting shape:
 *  these are not offers, they are things already ruled on. */
interface Decision {
  posting_id: string;
  company: string;
  title: string;
  status: string;
  note: string;
}

function decisionsFrom(output: unknown): Decision[] {
  if (!Array.isArray(output)) return [];
  return output.filter(
    (item): item is Decision =>
      typeof item === "object" &&
      item !== null &&
      "posting_id" in item &&
      "status" in item &&
      !("excerpt" in item),
  );
}

function DecisionsResult({ decisions }: { decisions: Decision[] }) {
  return (
    <ul className="mt-3 space-y-1">
      {decisions.slice(0, 12).map((decision) => (
        <li
          key={decision.posting_id}
          className="grid grid-cols-[7rem_1fr] gap-3 font-mono text-2xs"
        >
          <span className="truncate text-text-faint">{decision.status}</span>
          <span className="truncate text-text-muted">
            {decision.company} — {decision.title}
          </span>
        </li>
      ))}
      {decisions.length > 12 && (
        <li className="font-mono text-2xs text-text-faint">
          and {decisions.length - 12} more
        </li>
      )}
    </ul>
  );
}

function ToolStep({ step }: { step: TraceStep }) {
  const postings = postingsFrom(step.output);
  const screened = screenedOutFrom(step.output);
  const hits = postings.length > 0 ? [] : hitsFrom(step.output);
  const max = hits.reduce((best, hit) => Math.max(best, hit.score), 0);
  const stats = corpusStatsFrom(step.output);
  const decisions =
    postings.length > 0 || hits.length > 0 || screened.length > 0
      ? []
      : decisionsFrom(step.output);

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

      {screened.length > 0 && <ScreenedOutResult screened={screened} />}

      {stats !== null && <CorpusStatsResult stats={stats} />}

      {decisions.length > 0 && <DecisionsResult decisions={decisions} />}

      {!step.pending &&
        hits.length === 0 &&
        postings.length === 0 &&
        screened.length === 0 &&
        decisions.length === 0 &&
        stats === null &&
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
          The rest of the app does not depend on this: the postings grid,
          filters and status changes all work while chat is down.
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
