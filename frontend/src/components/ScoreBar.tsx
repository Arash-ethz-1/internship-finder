import type { SearchHit } from "../api/client";

/**
 * A fused retrieval score, decomposed into what produced it.
 *
 * This is the signature element of the whole app. Every job tracker can show
 * you a ranked list; this shows you *why* a result ranked where it did — how
 * much came from dense (meaning) similarity and how much from BM25 (exact
 * terms). The two segments sum to the fused score, so the bar is a real
 * measurement rather than a decoration.
 */

/** Dense is solid; BM25 is hatched. Different treatments, not just hues, so
 *  the split survives greyscale and colour-blindness. */
const HATCH =
  "repeating-linear-gradient(135deg, currentColor 0 2px, transparent 2px 4px)";

export function ScoreBar({ hit, max }: { hit: SearchHit; max: number }) {
  const dense = hit.component_scores.dense ?? 0;
  const bm25 = hit.component_scores.bm25 ?? 0;
  const scale = max > 0 ? 100 / max : 0;

  return (
    <div className="flex items-center gap-2">
      <div
        className="relative h-2 flex-1 overflow-hidden rounded-xs bg-surface-sunken"
        role="img"
        aria-label={`score ${hit.score.toFixed(4)}: dense ${dense.toFixed(4)}, bm25 ${bm25.toFixed(4)}`}
      >
        <div className="flex h-full">
          <div
            className="h-full bg-signal"
            style={{ width: `${dense * scale}%` }}
            title={`dense ${dense.toFixed(4)}`}
          />
          <div
            className="h-full text-signal"
            style={{ width: `${bm25 * scale}%`, backgroundImage: HATCH }}
            title={`bm25 ${bm25.toFixed(4)}`}
          />
        </div>
      </div>
      <span className="w-16 shrink-0 text-right font-mono text-2xs tabular-nums text-text-muted">
        {hit.score.toFixed(4)}
      </span>
    </div>
  );
}

/** Names the two treatments once, above a set of bars. */
export function ScoreLegend() {
  return (
    <div className="flex items-center gap-4 font-mono text-2xs text-text-faint">
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-2 w-4 rounded-xs bg-signal" />
        dense
      </span>
      <span className="flex items-center gap-1.5">
        <span
          className="inline-block h-2 w-4 rounded-xs text-signal"
          style={{ backgroundImage: HATCH }}
        />
        bm25
      </span>
      <span>contributions sum to the fused score</span>
    </div>
  );
}
