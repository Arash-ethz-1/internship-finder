import { useVirtualizer } from "@tanstack/react-virtual";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getFilters,
  getPostings,
  getStats,
  placeLabel,
  setStatus,
  type PostingQuery,
  type PostingSummary,
  type Status,
} from "../api/client";
import { NewPostingDialog } from "../components/NewPostingDialog";
import { DetailPanel } from "../components/DetailPanel";
import { ON_MY_LIST, Rail, STATUS_CHOICES } from "../components/Rail";
import { EmptyState, ErrorState, LoadingState } from "../components/states";
import { STATUS_KEYS, StatusDot, statusStyle } from "../components/status";
import {
  loadFilters,
  sameStatuses,
  saveFilters,
} from "../state/postingFilters";

/**
 * The primary view, and the one that must feel fast.
 *
 * Fixed 36px rows, virtualised, hairline dividers, no cards. The whole
 * working set is fetched in one request and filtered client-side by the
 * server query — at a few thousand rows that beats paging.
 */

const ROW = 36;

/**
 * The column template, defined once because two grids have to agree on it:
 * the header sits outside the scroll container and the rows sit inside it, so
 * if the two drift the labels stop lining up with the data.
 *
 * `compact` is what the grid narrows to while the detail panel is open. The
 * full template needs 44.5rem before the title column gets anything at all,
 * which is more than the panel leaves on a laptop — and fixed tracks cannot
 * shrink, so the header used to overflow into the panel. Dropping the three
 * columns you are least likely to be reading while a posting is open costs
 * nothing and keeps the rest legible.
 */
const COLS = {
  full: "grid-cols-[1.5rem_10rem_minmax(0,1fr)_9rem_4rem_5.5rem_5.5rem]",
  compact: "grid-cols-[1.5rem_8rem_minmax(0,1fr)_5.5rem]",
} as const;

export function Postings() {
  const queryClient = useQueryClient();
  // The grid is the working list, not the pile: it shows what you decided
  // something about, minus what you decided against. `ON_MY_LIST` is that set;
  // without it, opening this view means scrolling 24,000 rows nobody chose.
  // Lazy initialiser: the rail's settings are read from this browser once, on
  // mount, and a first visit falls back to the default view.
  const [query, setQuery] = useState<PostingQuery>(() => ({
    limit: 5000,
    status: ON_MY_LIST,
    ...loadFilters(STATUS_CHOICES),
  }));
  const [cursor, setCursor] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const postings = useQuery({
    queryKey: ["postings", query],
    queryFn: () => getPostings(query),
    placeholderData: keepPreviousData,
  });
  const filters = useQuery({ queryKey: ["filters"], queryFn: getFilters });
  const stats = useQuery({ queryKey: ["stats"], queryFn: getStats });

  const rows = useMemo(() => postings.data?.items ?? [], [postings.data]);

  const mutate = useMutation({
    mutationFn: ({ id, status }: { id: string; status: Status }) =>
      setStatus(id, status),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["postings"] });
      void queryClient.invalidateQueries({ queryKey: ["stats"] });
      void queryClient.invalidateQueries({
        queryKey: ["posting", variables.id],
      });
    },
  });

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW,
    overscan: 12,
  });

  const patch = useCallback((next: Partial<PostingQuery>) => {
    setQuery((current) => {
      const updated = { ...current, ...next, offset: 0 };
      // Written here rather than in an effect: this is the only place a filter
      // changes, and an effect would also fire for the initial load.
      saveFilters(updated);
      return updated;
    });
    setCursor(0);
  }, []);

  // Keyboard navigation: j/k move, Enter opens, 1-6 set status, Escape closes.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
        return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (rows.length === 0) return;

      const move = (delta: number) => {
        event.preventDefault();
        setCursor((current) => {
          const next = Math.min(Math.max(current + delta, 0), rows.length - 1);
          virtualizer.scrollToIndex(next, { align: "auto" });
          return next;
        });
      };

      if (event.key === "j" || event.key === "ArrowDown") return move(1);
      if (event.key === "k" || event.key === "ArrowUp") return move(-1);
      if (event.key === "Enter") {
        event.preventDefault();
        setOpenId(rows[cursor]?.id ?? null);
        return;
      }
      if (event.key === "Escape") {
        setOpenId(null);
        return;
      }
      const index = Number(event.key);
      if (
        Number.isInteger(index) &&
        index >= 1 &&
        index <= STATUS_KEYS.length
      ) {
        const row = rows[cursor];
        if (row) {
          event.preventDefault();
          mutate.mutate({ id: row.id, status: STATUS_KEYS[index - 1] });
        }
      }
    }

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, cursor, virtualizer, mutate]);

  const counts = stats.data
    ? { level: stats.data.by_level, status: stats.data.by_status }
    : undefined;

  const compact = openId !== null;
  const cols = compact ? COLS.compact : COLS.full;

  return (
    <div className="flex h-full min-h-0">
      <Rail
        options={filters.data}
        query={query}
        counts={counts}
        total={postings.data?.total ?? 0}
        onChange={patch}
      />

      {/* `overflow-hidden` is load-bearing: without it a grid wider than this
          column escapes into the detail panel instead of being clipped. */}
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <div
          className={`grid shrink-0 items-center gap-3 border-b border-hairline px-3 py-1.5 font-mono text-2xs uppercase tracking-wide text-text-faint ${cols}`}
        >
          <span />
          <span>company</span>
          <span>title</span>
          {!compact && (
            <>
              <span>location</span>
              <span>level</span>
              <span>posted</span>
            </>
          )}
          <span>status</span>
        </div>

        {postings.isPending && <LoadingState what="postings" />}
        {postings.error && <ErrorState error={postings.error} />}

        {postings.data && rows.length === 0 && (
          <EmptyState
            title="No postings match"
            detail={
              sameStatuses(query.status, ON_MY_LIST) && !query.q && !query.level
                ? "Nothing is on your list yet. Search on the chat page and keep what looks right — anything you keep shows up here."
                : "Nothing matches these filters. Clear one from the left rail."
            }
          />
        )}

        {rows.length > 0 && (
          <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto">
            <div
              style={{
                height: virtualizer.getTotalSize(),
                position: "relative",
              }}
            >
              {virtualizer.getVirtualItems().map((item) => {
                const row = rows[item.index];
                return (
                  <Row
                    key={row.id}
                    row={row}
                    cols={cols}
                    compact={compact}
                    selected={item.index === cursor}
                    open={openId === row.id}
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      width: "100%",
                      height: ROW,
                      transform: `translateY(${item.start}px)`,
                    }}
                    onSelect={() => {
                      setCursor(item.index);
                      setOpenId(row.id);
                    }}
                  />
                );
              })}
            </div>
          </div>
        )}

        <div className="flex shrink-0 items-center justify-between border-t border-hairline px-3 py-1.5 font-mono text-2xs text-text-faint">
          <span>j/k move · enter open · 1-5 status · esc close</span>
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="text-text-muted hover:text-signal"
          >
            + add a posting
          </button>
        </div>
      </main>

      {openId && <DetailPanel id={openId} onClose={() => setOpenId(null)} />}

      {adding && (
        <NewPostingDialog
          onClose={() => setAdding(false)}
          onCreated={setOpenId}
        />
      )}
    </div>
  );
}

function Row({
  row,
  cols,
  compact,
  selected,
  open,
  style,
  onSelect,
}: {
  row: PostingSummary;
  cols: string;
  compact: boolean;
  selected: boolean;
  open: boolean;
  style: React.CSSProperties;
  onSelect: () => void;
}) {
  const faded = statusStyle(row.status).dim;
  // Hover is offered only on rows that are neither selected nor open: a
  // pointer highlight that overrode the selection would hide where you are.
  const background = selected
    ? "bg-signal/8"
    : open
      ? "bg-surface-sunken"
      : "hover:bg-surface-sunken";
  return (
    <div
      style={style}
      onClick={onSelect}
      role="row"
      tabIndex={-1}
      aria-selected={selected}
      // Compact mode drops three columns, so the row carries what it hid.
      title={compact ? `${row.title} — ${row.company}` : undefined}
      className={`grid cursor-pointer items-center gap-3 border-b border-hairline px-3 text-xs ${cols} ${background} ${
        faded ? "opacity-45" : ""
      }`}
    >
      <StatusDot status={row.status} />
      <span className="truncate font-mono">{row.company}</span>
      <span className="truncate">{row.title}</span>
      {!compact && (
        <>
          <span
            className="truncate font-mono text-text-muted"
            // The parsed places, falling back to whatever the board wrote. A
            // posting offered in two cities says both rather than picking one.
            title={row.location ?? undefined}
          >
            {row.places.length > 0
              ? row.places.map(placeLabel).join(" · ")
              : row.remote
                ? "remote"
                : (row.location ?? "—")}
          </span>
          <span className="font-mono text-text-muted">{row.level}</span>
          <span className="font-mono tabular-nums text-text-muted">
            {row.posted_at?.slice(0, 10) ?? "—"}
          </span>
        </>
      )}
      <span className="truncate font-mono text-2xs text-text-muted">
        {/* A closed posting says so instead of its status: the fact that you
            can no longer apply outranks what you had decided about it. */}
        {row.closed_at ? (
          <span className="text-status-rejected">closed</span>
        ) : row.status === "untriaged" ? (
          ""
        ) : (
          row.status
        )}
      </span>
    </div>
  );
}
