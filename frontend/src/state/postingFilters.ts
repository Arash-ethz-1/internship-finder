import type { PostingQuery } from "../api/client";

/**
 * The left rail's settings, remembered between visits.
 *
 * Unchecking `found` and finding it checked again after a reload is the app
 * disagreeing with you about what you want to look at. These are preferences,
 * not navigation, so they belong in this browser rather than in the URL.
 *
 * The free-text box is deliberately excluded. A filter is a standing decision
 * about what the grid is for; a search is a thing you are doing right now, and
 * reviving last week's search on a cold start is a puzzle, not a convenience.
 */

const KEY = "internship-finder:postings-filters:v1";

/** Only these are remembered. `q`, `limit`, `offset` and sort are not. */
const REMEMBERED = [
  "status",
  "level",
  "source",
  "company",
  "location",
  "remote",
  "posted_after",
  "region",
  "country",
  // "Show me the closed ones too" is a standing decision about what the grid
  // is for, the same as any other filter here.
  "include_closed",
  "only_closed",
] as const;

type Remembered = Pick<PostingQuery, (typeof REMEMBERED)[number]>;

export function loadFilters(known: readonly string[]): Remembered {
  let raw: string | null;
  try {
    raw = localStorage.getItem(KEY);
  } catch {
    // Private windows and blocked site data throw on access, not on read.
    return {};
  }
  if (!raw) return {};

  try {
    const saved = JSON.parse(raw) as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const key of REMEMBERED) {
      if (!(key in saved)) continue;
      const value = saved[key];
      // A key stored as null means you cleared it on purpose. It has to come
      // back as a present-but-undefined property, so that spreading this over
      // the defaults overrides them instead of letting them win.
      if (value === null || value === undefined) {
        out[key] = undefined;
        continue;
      }
      if (key === "status") {
        // A status saved by an older build may no longer exist — `tracked`
        // was in this list once. Dropping it beats sending the API a 422.
        const kept = Array.isArray(value)
          ? value.filter((s) => known.includes(String(s)))
          : [];
        out.status = kept.length ? kept : undefined;
      } else {
        out[key] = value;
      }
    }
    return out as Remembered;
  } catch {
    return {};
  }
}

export function saveFilters(query: PostingQuery): void {
  // Every remembered key is written, `null` where it is unset, so that
  // "clear filters" survives a reload rather than reverting to the default.
  const out: Record<string, unknown> = {};
  for (const key of REMEMBERED) {
    out[key] = query[key] ?? null;
  }
  try {
    localStorage.setItem(KEY, JSON.stringify(out));
  } catch {
    // Storage being unavailable is not worth failing a filter change over.
  }
}

const RAIL_KEY = "internship-finder:rail-open:v1";

/** Whether the filter rail is showing. Open on a first visit: a rail you have
 *  never seen cannot be found. */
export function loadRailOpen(): boolean {
  try {
    return localStorage.getItem(RAIL_KEY) !== "0";
  } catch {
    return true;
  }
}

export function saveRailOpen(open: boolean): void {
  try {
    localStorage.setItem(RAIL_KEY, open ? "1" : "0");
  } catch {
    // Storage being unavailable is not worth failing a toggle over.
  }
}

/** Two status selections are the same set, whatever the order. */
export function sameStatuses(
  a: string[] | undefined,
  b: string[] | undefined,
): boolean {
  const left = [...(a ?? [])].sort();
  const right = [...(b ?? [])].sort();
  return (
    left.length === right.length && left.every((value, i) => value === right[i])
  );
}
