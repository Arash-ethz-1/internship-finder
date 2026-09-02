import type { FilterOptions, PostingQuery } from "../api/client";
import { STATUS_LABELS } from "./status";

/**
 * The persistent left rail. Filters only — no navigation, no branding, no
 * stat tiles. Every control here narrows the grid.
 */

function Group({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="border-b border-hairline px-3 py-3">
      <div className="mb-2 font-mono text-2xs uppercase tracking-wide text-text-faint">
        {label}
      </div>
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
        <span className="font-mono text-2xs tabular-nums text-text-faint">
          {count}
        </span>
      )}
    </button>
  );
}

/**
 * Every state a posting can be in, as its own checkbox.
 *
 * It was one radio button, so "everything I have touched except the ones I
 * passed on" — the view you actually work in — could not be expressed at all.
 * Nothing ticked means no constraint, which is not the same as ticking them
 * all: an untriaged posting has no status to be in a list.
 *
 * `tracked` is gone from here. It was a group masquerading as a member, and
 * the presets below say the same thing without the category error.
 */
export const STATUS_CHOICES = [
  "untriaged",
  "found",
  "interested",
  "applied",
  "interviewing",
  "offer",
  "rejected",
  "declined",
  "not_relevant",
];

/** `on my list` is the default the grid opens on: everything you decided
 *  something about, minus the ones you decided against. */
const PRESETS: { label: string; statuses: () => string[] | undefined }[] = [
  {
    label: "on my list",
    statuses: () =>
      STATUS_CHOICES.filter((s) => !["untriaged", "not_relevant"].includes(s)),
  },
  { label: "untriaged", statuses: () => ["untriaged"] },
  { label: "everything", statuses: () => undefined },
];

export const ON_MY_LIST = PRESETS[0].statuses() as string[];

function Check({
  value,
  chosen,
  label,
  count,
  onChange,
}: {
  value: string;
  chosen: string[];
  label: string;
  count?: number;
  onChange: (patch: Partial<PostingQuery>) => void;
}) {
  const active = chosen.includes(value);
  return (
    <label
      className={`flex w-full cursor-pointer items-center gap-2 rounded-xs px-1 py-0.5 text-xs ${
        active ? "text-text" : "text-text-muted hover:text-text"
      }`}
    >
      <input
        type="checkbox"
        checked={active}
        onChange={() => {
          const next = active
            ? chosen.filter((s) => s !== value)
            : [...chosen, value];
          onChange({ status: next.length ? next : undefined });
        }}
        className="accent-signal"
      />
      <span className="truncate">{label}</span>
      {count !== undefined && (
        <span className="ml-auto font-mono text-2xs tabular-nums text-text-faint">
          {count}
        </span>
      )}
    </label>
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
  counts:
    | { level: Record<string, number>; status: Record<string, number> }
    | undefined;
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
        <div className="mb-2 flex flex-wrap gap-1">
          {PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              onClick={() => onChange({ status: preset.statuses() })}
              className="rounded-xs border border-hairline px-1.5 py-0.5 font-mono text-2xs text-text-muted hover:border-signal hover:text-signal"
            >
              {preset.label}
            </button>
          ))}
        </div>
        {STATUS_CHOICES.map((s) => (
          <Check
            key={s}
            value={s}
            chosen={query.status ?? []}
            label={STATUS_LABELS[s] ?? s}
            count={counts?.status[s]}
            onChange={onChange}
          />
        ))}
      </Group>

      {/* Region and country come from the parsed `posting_locations` table, so
          they mean the same thing however the board spelled the place. The
          free-text box below is still a substring match on the raw string —
          it is the escape hatch for somewhere the parser does not know. */}
      <Group label="region">
        {(options?.regions ?? []).map((region) => (
          <Radio
            key={region.id}
            name="region"
            value={region.id}
            current={query.region}
            label={region.name}
            count={region.count}
            onChange={(v) => onChange({ region: v, country: undefined })}
          />
        ))}
      </Group>

      <Group label="country">
        <div className="max-h-56 overflow-y-auto">
          {(options?.countries ?? [])
            .filter((c) => !query.region || c.region === query.region)
            .map((country) => (
              <Radio
                key={country.code}
                name="country"
                value={country.code}
                current={query.country}
                label={country.name}
                count={country.count}
                onChange={(v) => onChange({ country: v })}
              />
            ))}
        </div>
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
            onChange={(e) =>
              onChange({ remote: e.target.checked ? true : undefined })
            }
            className="accent-signal"
          />
          remote only
        </label>
      </Group>

      {/* Closed postings are hidden by default: the grid is a list of things
          you could still apply to. They are never deleted, so this is how you
          go and look at one you already applied to. */}
      <Group label="on the board">
        <label className="flex items-center gap-2 text-xs text-text-muted">
          <input
            type="checkbox"
            checked={query.include_closed === true}
            onChange={(e) =>
              onChange({
                include_closed: e.target.checked ? true : undefined,
                only_closed: undefined,
              })
            }
            className="accent-signal"
          />
          include closed
        </label>
        <label className="mt-1 flex items-center gap-2 text-xs text-text-muted">
          <input
            type="checkbox"
            checked={query.only_closed === true}
            onChange={(e) =>
              onChange({
                only_closed: e.target.checked ? true : undefined,
                include_closed: undefined,
              })
            }
            className="accent-signal"
          />
          closed only
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
              region: undefined,
              country: undefined,
              include_closed: undefined,
              only_closed: undefined,
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
