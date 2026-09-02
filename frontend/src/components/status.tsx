import type { StatusOrUntriaged } from "../api/client";

/**
 * Status colour is the only other colour on the page besides the signal
 * accent, and it carries meaning rather than decorating: a row's state should
 * be readable from across the room without reading the label.
 *
 * Which is what the label has to be: readable. `rejected` and `declined` used
 * to render the whole chip at 45% opacity, so the one thing you wanted to
 * check at a glance was the hardest thing on the page to read. The dot still
 * recedes for closed doors — that is the across-the-room signal — but the
 * text sits on a tinted ground at full strength.
 *
 * `not_relevant` is you passing on a posting. `rejected` is a company passing
 * on you. They look different because they mean different things.
 */
const STYLES: Record<StatusOrUntriaged, { dot: string; chip: string; dim?: boolean }> = {
  untriaged: {
    dot: "bg-transparent border border-hairline",
    chip: "border-hairline text-text-faint",
  },
  found: {
    dot: "bg-status-found",
    chip: "border-hairline text-text-muted",
  },
  not_relevant: {
    dot: "bg-status-not-relevant",
    chip: "border-hairline text-text-muted",
    dim: true,
  },
  interested: {
    dot: "bg-status-interested",
    chip: "border-status-interested/40 bg-status-interested/8 text-text",
  },
  ready_to_submit: {
    dot: "bg-status-ready-to-submit",
    chip: "border-signal/50 bg-signal/10 text-signal",
  },
  applied: {
    dot: "bg-status-applied",
    chip: "border-status-applied/50 bg-status-applied/10 text-status-applied",
  },
  interviewing: {
    dot: "bg-status-interviewing",
    chip: "border-status-interviewing/55 bg-status-interviewing/12 text-status-interviewing",
  },
  offer: {
    dot: "bg-status-offer",
    chip: "border-status-offer/55 bg-status-offer/12 text-status-offer",
  },
  rejected: {
    dot: "bg-status-rejected",
    chip: "border-hairline text-text-muted",
    dim: true,
  },
  declined: {
    dot: "bg-status-declined",
    chip: "border-hairline text-text-muted",
    dim: true,
  },
};

export const STATUS_LABELS: Record<string, string> = {
  untriaged: "untriaged",
  tracked: "on my list",
  found: "found",
  not_relevant: "not for me",
  interested: "interested",
  ready_to_submit: "ready",
  applied: "applied",
  interviewing: "interview",
  offer: "offer",
  rejected: "rejected",
  declined: "declined",
};

/** `1`-`6` set these, in this order. `declined` and `not_relevant` are
 *  deliberately not bound — six keys is the design; set them from the panel. */
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
        style.dim ? "opacity-45" : ""
      }`}
      title={STATUS_LABELS[status]}
    />
  );
}

export function StatusLabel({ status }: { status: StatusOrUntriaged }) {
  const style = statusStyle(status);
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-1.5 py-0.5 font-mono text-2xs whitespace-nowrap ${style.chip}`}
    >
      <StatusDot status={status} />
      {STATUS_LABELS[status]}
    </span>
  );
}
