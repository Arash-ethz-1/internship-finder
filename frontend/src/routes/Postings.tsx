import { useVirtualizer } from "@tanstack/react-virtual";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getFilters,
  getPostings,
  getStats,
  setStatus,
  type PostingQuery,
  type PostingSummary,
  type Status,
} from "../api/client";
import { DetailPanel } from "../components/DetailPanel";
import { Rail } from "../components/Rail";
import { EmptyState, ErrorState, LoadingState } from "../components/states";
import { STATUS_KEYS, StatusDot, statusStyle } from "../components/status";

/**
 * The primary view, and the one that must feel fast.
 *
 * Fixed 36px rows, virtualised, hairline dividers, no cards. The whole
 * working set is fetched in one request and filtered client-side by the
 * server query — at a few thousand rows that beats paging.
 */

const ROW = 36;

export function Postings() {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState<PostingQuery>({ limit: 5000 });
  const [cursor, setCursor] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
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
    mutationFn: ({ id, status }: { id: string; status: Status }) => setStatus(id, status),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["postings"] });
      void queryClient.invalidateQueries({ queryKey: ["stats"] });
      void queryClient.invalidateQueries({ queryKey: ["posting", variables.id] });
    },
  });

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW,
    overscan: 12,
  });

  const patch = useCallback((next: Partial<PostingQuery>) => {
    setQuery((current) => ({ ...current, ...next, offset: 0 }));
    setCursor(0);
  }, []);

  // Keyboard navigation: j/k move, Enter opens, 1-6 set status, Escape closes.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
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
      if (Number.isInteger(index) && index >= 1 && index <= STATUS_KEYS.length) {
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

  return (
    <div className="flex h-full min-h-0">
      <Rail
        options={filters.data}
        query={query}
        counts={counts}
        total={postings.data?.total ?? 0}
        onChange={patch}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <div className="grid shrink-0 grid-cols-[1.5rem_11rem_1fr_10rem_4rem_6rem_6rem] items-center gap-3 border-b border-hairline px-3 py-1.5 font-mono text-2xs uppercase tracking-wide text-text-faint">
          <span />
          <span>company</span>
          <span>title</span>
          <span>location</span>
          <span>level</span>
          <span>posted</span>
          <span>status</span>
        </div>

        {postings.isPending && <LoadingState what="postings" />}
        {postings.error && <ErrorState error={postings.error} />}

        {postings.data && rows.length === 0 && (
          <EmptyState
            title="No postings match"
            detail={
              postings.data.total === 0 && !query.q && !query.level
                ? "The database is empty. Fetch postings from the configured job boards, then reload."
                : "Nothing matches these filters. Clear one from the left rail."
            }
            command={
              postings.data.total === 0
                ? "cd backend && uv run python -m agent_app.cli ingest"
                : undefined
            }
          />
        )}

        {rows.length > 0 && (
          <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto">
            <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
              {virtualizer.getVirtualItems().map((item) => {
                const row = rows[item.index];
                return (
                  <Row
                    key={row.id}
                    row={row}
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

        <div className="shrink-0 border-t border-hairline px-3 py-1.5 font-mono text-2xs text-text-faint">
          j/k move · enter open · 1-6 status · esc close
        </div>
      </main>

      {openId && <DetailPanel id={openId} onClose={() => setOpenId(null)} />}
    </div>
  );
}

function Row({
  row,
  selected,
  open,
  style,
  onSelect,
}: {
  row: PostingSummary;
  selected: boolean;
  open: boolean;
  style: React.CSSProperties;
  onSelect: () => void;
}) {
  const faded = statusStyle(row.status).faded;
  return (
    <div
      style={style}
      onClick={onSelect}
      role="row"
      tabIndex={-1}
      aria-selected={selected}
      className={`grid cursor-default grid-cols-[1.5rem_11rem_1fr_10rem_4rem_6rem_6rem] items-center gap-3 border-b border-hairline px-3 text-xs ${
        selected ? "bg-signal/8" : open ? "bg-surface-sunken" : ""
      } ${faded ? "opacity-45" : ""}`}
    >
      <StatusDot status={row.status} />
      <span className="truncate font-mono">{row.company}</span>
      <span className="truncate">{row.title}</span>
      <span className="truncate font-mono text-text-muted">
        {row.remote ? "remote" : (row.location ?? "—")}
      </span>
      <span className="font-mono text-text-muted">{row.level}</span>
      <span className="font-mono tabular-nums text-text-muted">
        {row.posted_at?.slice(0, 10) ?? "—"}
      </span>
      <span className="truncate font-mono text-2xs text-text-muted">
        {row.status === "untriaged" ? "" : row.status}
      </span>
    </div>
  );
}
