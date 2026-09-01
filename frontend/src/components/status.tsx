import type { StatusOrUntriaged } from "../api/client";

/**
 * Status colour is the only other colour on the page besides the signal
 * accent, and it carries meaning rather than decorating: a row's state should
 * be readable from across the room without reading the label.
 *
 * `rejected` and `declined` fade to 45% — closed doors recede.
 */
const STYLES: Record<StatusOrUntriaged, { dot: string; label: string; faded?: boolean }> = {
  untriaged: { dot: "bg-transparent border border-hairline", label: "text-text-faint" },
  found: { dot: "bg-status-found", label: "text-text-faint" },
  interested: { dot: "bg-status-interested", label: "text-text-muted" },
  ready_to_submit: { dot: "bg-status-ready-to-submit", label: "text-signal" },
  applied: { dot: "bg-status-applied", label: "text-status-applied" },
  interviewing: { dot: "bg-status-interviewing", label: "text-status-interviewing" },
  offer: { dot: "bg-status-offer", label: "text-status-offer" },
  rejected: { dot: "bg-status-rejected", label: "text-status-rejected", faded: true },
  declined: { dot: "bg-status-declined", label: "text-status-declined", faded: true },
};

export const STATUS_LABELS: Record<string, string> = {
  untriaged: "untriaged",
  tracked: "on my list",
  found: "found",
  interested: "interested",
  ready_to_submit: "ready",
  applied: "applied",
  interviewing: "interview",
  offer: "offer",
  rejected: "rejected",
  declined: "declined",
};

/** `1`-`6` set these, in this order. `declined` is deliberately not bound to a
 *  key — it is rare, and six keys is the design. Set it from the panel. */
export const STATUS_KEYS = [
  "interested",
  "ready_to_submit",
  "applied",
  "interviewing",
  "offer",
  "rejected",
] as const;

export function statusStyle(status: StatusOrUntriaged) {
  return STYLES[status] ?? STYLES.untriaged;
}

export function StatusDot({ status }: { status: StatusOrUntriaged }) {
  const style = statusStyle(status);
  return (
    <span
      className={`inline-block size-2 shrink-0 rounded-full ${style.dot} ${
        style.faded ? "opacity-45" : ""
      }`}
      title={STATUS_LABELS[status]}
    />
  );
}

export function StatusLabel({ status }: { status: StatusOrUntriaged }) {
  const style = statusStyle(status);
  return (
    <span
      className={`flex items-center gap-1.5 font-mono text-2xs ${style.label} ${
        style.faded ? "opacity-45" : ""
      }`}
    >
      <StatusDot status={status} />
      {STATUS_LABELS[status]}
    </span>
  );
}
