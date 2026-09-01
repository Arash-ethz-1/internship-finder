import type { FilterOptions, PostingQuery } from "../api/client";
import { STATUS_LABELS } from "./status";

/**
 * The persistent left rail. Filters only — no navigation, no branding, no
 * stat tiles. Every control here narrows the grid.
 */

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="border-b border-hairline px-3 py-3">
      <div className="mb-2 font-mono text-2xs uppercase tracking-wide text-text-faint">{label}</div>
      {children}
    </div>
  );
}

function Radio({
  name,
  value,
  current,
  label,
  count,
  onChange,
}: {
  name: string;
  value: string;
  current: string | undefined;
  label: string;
  count?: number;
  onChange: (value: string | undefined) => void;
}) {
  const active = current === value;
  return (
    <button
      type="button"
      onClick={() => onChange(active ? undefined : value)}
      className={`flex w-full items-center justify-between gap-2 px-1 py-0.5 text-left text-xs rounded-xs ${
        active ? "bg-signal/12 text-signal" : "text-text-muted hover:text-text"
      }`}
      aria-pressed={active}
      name={name}
    >
      <span className="truncate">{label}</span>
      {count !== undefined && (
        <span className="font-mono text-2xs tabular-nums text-text-faint">{count}</span>
      )}
    </button>
  );
}

export function Rail({
  options,
  query,
  counts,
  total,
  onChange,
}: {
  options: FilterOptions | undefined;
  query: PostingQuery;
  counts: { level: Record<string, number>; status: Record<string, number> } | undefined;
  total: number;
  onChange: (patch: Partial<PostingQuery>) => void;
}) {
  return (
    <aside className="flex w-56 shrink-0 flex-col overflow-y-auto border-r border-hairline">
      <div className="border-b border-hairline px-3 py-3">
        <input
          type="search"
          value={query.q ?? ""}
          onChange={(e) => onChange({ q: e.target.value || undefined })}
          placeholder="title or company"
          className="w-full border border-hairline bg-transparent px-2 py-1 text-xs rounded-xs placeholder:text-text-faint"
          aria-label="Free text search"
        />
        <div className="mt-2 font-mono text-2xs tabular-nums text-text-faint">
          {total.toLocaleString()} matching
        </div>
      </div>

      <Group label="level">
        {["intern", "newgrad", "unknown"].map((level) => (
          <Radio
            key={level}
            name="level"
            value={level}
            current={query.level}
            label={level}
            count={counts?.level[level]}
            onChange={(v) => onChange({ level: v })}
          />
        ))}
      </Group>

      <Group label="status">
        {["tracked", "untriaged", ...Object.keys(STATUS_LABELS).filter((s) => s !== "untriaged")].map((s) => (
          <Radio
            key={s}
            name="status"
            value={s}
            current={query.status}
            label={STATUS_LABELS[s as keyof typeof STATUS_LABELS] ?? s}
            count={counts?.status[s]}
            onChange={(v) => onChange({ status: v })}
          />
        ))}
      </Group>

      <Group label="location">
        <input
          type="text"
          value={query.location ?? ""}
          onChange={(e) => onChange({ location: e.target.value || undefined })}
          placeholder="e.g. Zurich"
          className="w-full border border-hairline bg-transparent px-2 py-1 text-xs rounded-xs placeholder:text-text-faint"
          aria-label="Location contains"
        />
        <label className="mt-2 flex items-center gap-2 text-xs text-text-muted">
          <input
            type="checkbox"
            checked={query.remote === true}
            onChange={(e) => onChange({ remote: e.target.checked ? true : undefined })}
            className="accent-signal"
          />
          remote only
        </label>
      </Group>

      <Group label="source">
        {(options?.sources ?? []).map((source) => (
          <Radio
            key={source}
            name="source"
            value={source}
            current={query.source}
            label={source}
            onChange={(v) => onChange({ source: v })}
          />
        ))}
      </Group>

      <Group label="company">
        <div className="max-h-64 overflow-y-auto">
          {(options?.companies ?? []).map((company) => (
            <Radio
              key={company}
              name="company"
              value={company}
              current={query.company}
              label={company}
              onChange={(v) => onChange({ company: v })}
            />
          ))}
        </div>
      </Group>

      <div className="px-3 py-3">
        <button
          type="button"
          onClick={() =>
            onChange({
              q: undefined,
              level: undefined,
              status: undefined,
              location: undefined,
              remote: undefined,
              source: undefined,
              company: undefined,
            })
          }
          className="text-xs text-text-faint underline-offset-2 hover:text-text hover:underline"
        >
          clear filters
        </button>
      </div>
    </aside>
  );
}
