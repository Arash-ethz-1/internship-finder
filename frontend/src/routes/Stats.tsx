import { useQuery } from "@tanstack/react-query";

import { getStats } from "../api/client";
import { ErrorState, LoadingState } from "../components/states";
import { STATUS_LABELS, statusStyle } from "../components/status";

/**
 * The pipeline. One horizontal stacked bar for status, a compact table by
 * company. No pie charts, no stat-tile row.
 */

const ORDER = [
  "untriaged",
  "interested",
  "ready_to_submit",
  "applied",
  "interviewing",
  "offer",
  "rejected",
  "declined",
] as const;

export function Stats() {
  const { data, isPending, error } = useQuery({ queryKey: ["stats"], queryFn: getStats });

  if (isPending) return <LoadingState what="stats" />;
  if (error) return <ErrorState error={error} />;

  const total = data.total || 1;
  const segments = ORDER.filter((status) => (data.by_status[status] ?? 0) > 0);
  const maxCompany = data.by_company.reduce((best, row) => Math.max(best, row.count), 0);
  const maxDay = data.recent.reduce((best, row) => Math.max(best, row.count), 0);

  return (
    <div className="mx-auto w-full max-w-5xl px-6 py-6">
      <div className="flex items-baseline gap-3">
        <h1 className="text-lg font-medium">Pipeline</h1>
        <span className="font-mono text-xs tabular-nums text-text-muted">
          {data.total.toLocaleString()} postings
        </span>
      </div>

      <div className="mt-4 flex h-8 w-full overflow-hidden rounded-xs border border-hairline">
        {segments.map((status) => {
          const count = data.by_status[status] ?? 0;
          const style = statusStyle(status);
          return (
            <div
              key={status}
              className={`${style.dot} ${style.faded ? "opacity-45" : ""}`}
              style={{ width: `${(count / total) * 100}%` }}
              title={`${STATUS_LABELS[status]}: ${count}`}
            />
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
        {segments.map((status) => (
          <span key={status} className="flex items-center gap-1.5 font-mono text-2xs">
            <span
              className={`inline-block size-2 rounded-full ${statusStyle(status).dot} ${
                statusStyle(status).faded ? "opacity-45" : ""
              }`}
            />
            <span className="text-text-muted">{STATUS_LABELS[status]}</span>
            <span className="tabular-nums text-text-faint">{data.by_status[status]}</span>
          </span>
        ))}
      </div>

      <div className="mt-8 grid grid-cols-2 gap-8">
        <section>
          <h2 className="mb-2 font-mono text-2xs uppercase tracking-wide text-text-faint">
            by level
          </h2>
          <table className="w-full text-xs">
            <tbody>
              {Object.entries(data.by_level)
                .sort((a, b) => b[1] - a[1])
                .map(([level, count]) => (
                  <tr key={level} className="border-b border-hairline">
                    <td className="py-1 font-mono">{level}</td>
                    <td className="py-1 text-right font-mono tabular-nums text-text-muted">
                      {count.toLocaleString()}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>

        <section>
          <h2 className="mb-2 font-mono text-2xs uppercase tracking-wide text-text-faint">
            by source
          </h2>
          <table className="w-full text-xs">
            <tbody>
              {Object.entries(data.by_source)
                .sort((a, b) => b[1] - a[1])
                .map(([source, count]) => (
                  <tr key={source} className="border-b border-hairline">
                    <td className="py-1 font-mono">{source}</td>
                    <td className="py-1 text-right font-mono tabular-nums text-text-muted">
                      {count.toLocaleString()}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>
      </div>

      {data.recent.length > 0 && (
        <section className="mt-8">
          <h2 className="mb-2 font-mono text-2xs uppercase tracking-wide text-text-faint">
            posted per day
          </h2>
          <div className="flex h-16 items-end gap-px">
            {data.recent.map((day) => (
              <div
                key={day.date}
                className="flex-1 bg-signal/60"
                style={{ height: `${maxDay > 0 ? (day.count / maxDay) * 100 : 0}%` }}
                title={`${day.date}: ${day.count}`}
              />
            ))}
          </div>
          <div className="mt-1 flex justify-between font-mono text-2xs text-text-faint">
            <span>{data.recent[0]?.date}</span>
            <span>{data.recent[data.recent.length - 1]?.date}</span>
          </div>
        </section>
      )}

      <section className="mt-8">
        <h2 className="mb-2 font-mono text-2xs uppercase tracking-wide text-text-faint">
          by company
        </h2>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-hairline font-mono text-2xs uppercase text-text-faint">
              <th className="py-1 text-left font-normal">company</th>
              <th className="py-1 text-right font-normal">intern</th>
              <th className="py-1 text-right font-normal">total</th>
              <th className="w-40 py-1 text-left font-normal" />
            </tr>
          </thead>
          <tbody>
            {data.by_company.map((row) => (
              <tr key={row.company} className="border-b border-hairline">
                <td className="py-1 font-mono">{row.company}</td>
                <td className="py-1 text-right font-mono tabular-nums text-signal">
                  {row.intern || ""}
                </td>
                <td className="py-1 text-right font-mono tabular-nums text-text-muted">
                  {row.count.toLocaleString()}
                </td>
                <td className="py-1 pl-3">
                  <div
                    className="h-1.5 rounded-xs bg-signal/40"
                    style={{ width: `${maxCompany > 0 ? (row.count / maxCompany) * 100 : 0}%` }}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
