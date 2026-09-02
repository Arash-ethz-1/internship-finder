import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { ApiError, createPosting, type ManualPostingBody } from "../api/client";

/**
 * Adding a posting the boards cannot see.
 *
 * LinkedIn and company careers pages have no public feed, and PLAN.md rules
 * out scraping — so without this, an application made through one of them is
 * invisible: the pipeline on /stats understates what you have actually sent,
 * and a reply from that company has no posting for the inbox matcher to
 * attach itself to.
 *
 * Deliberately not a route. Adding a posting is a thing you do *to* the grid
 * while looking at it, and navigating away and back would lose your place.
 */

const EMPTY: ManualPostingBody = {
  company: "",
  title: "",
  url: "",
  body: "",
  location: "",
};

/**
 * Mounted only while it is open, so there is no `open` prop and no effect
 * resetting the draft: a fresh mount is a fresh form. Closing and reopening
 * therefore discards whatever was half-typed, which is the right behaviour for
 * a dialog you dismissed on purpose.
 */
export function NewPostingDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<ManualPostingBody>(EMPTY);
  const firstField = useRef<HTMLInputElement>(null);

  // Escape closes, the same as the detail panel. The grid's own key handler
  // ignores events from inputs, so typing in here cannot set a status.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const create = useMutation({
    mutationFn: (body: ManualPostingBody) =>
      createPosting({
        ...body,
        location: body.location?.trim() || null,
        body: body.body?.trim() || "",
      }),
    onSuccess: (posting) => {
      void queryClient.invalidateQueries({ queryKey: ["postings"] });
      void queryClient.invalidateQueries({ queryKey: ["stats"] });
      void queryClient.invalidateQueries({ queryKey: ["filters"] });
      onCreated(posting.id);
      onClose();
    },
  });

  const ready =
    draft.company.trim() !== "" &&
    draft.title.trim() !== "" &&
    draft.url.trim() !== "";

  return (
    /* Centred with padding on both sides rather than pushed down from the top,
       and capped at the viewport height: with a fixed top offset and no cap,
       the description field pushed the footer — and the submit button with it —
       off the bottom of a laptop screen. The fields scroll; the header and the
       footer stay put, so the way out of the dialog is always reachable. */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-ink/40 p-6"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="flex max-h-full w-[34rem] max-w-[92vw] flex-col border border-hairline bg-surface rounded-xs shadow-lg"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Add a posting"
      >
        <div className="flex shrink-0 items-baseline justify-between border-b border-hairline px-4 py-2.5">
          <h2 className="text-sm">Add a posting</h2>
          <span className="font-mono text-2xs text-text-faint">
            for anything without a public job board
          </span>
        </div>

        <form
          className="flex min-h-0 flex-col"
          onSubmit={(event) => {
            event.preventDefault();
            if (ready) create.mutate(draft);
          }}
        >
          <div className="flex min-h-0 flex-col gap-3 overflow-y-auto px-4 py-3">
            <Field label="company" required>
              <input
                ref={firstField}
                autoFocus
                value={draft.company}
                onChange={(e) =>
                  setDraft({ ...draft, company: e.target.value })
                }
                className={INPUT}
                placeholder="Google"
              />
            </Field>

            <Field label="title" required>
              <input
                value={draft.title}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                className={INPUT}
                placeholder="Software Engineering Intern, Summer 2027"
              />
            </Field>

            <Field label="url" required>
              <input
                value={draft.url}
                onChange={(e) => setDraft({ ...draft, url: e.target.value })}
                className={INPUT}
                placeholder="https://www.linkedin.com/jobs/view/..."
              />
            </Field>

            <div className="flex gap-3">
              <Field label="location" className="flex-1">
                <input
                  value={draft.location ?? ""}
                  onChange={(e) =>
                    setDraft({ ...draft, location: e.target.value })
                  }
                  className={INPUT}
                  placeholder="Zurich, Switzerland"
                />
              </Field>
              <Field label="level" className="w-32">
                <select
                  value={draft.level ?? ""}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      level: (e.target.value ||
                        null) as ManualPostingBody["level"],
                    })
                  }
                  className={INPUT}
                >
                  <option value="">from title</option>
                  <option value="intern">intern</option>
                  <option value="newgrad">newgrad</option>
                  <option value="unknown">unknown</option>
                </select>
              </Field>
            </div>

            {/* The description is what gets chunked and embedded, so a posting
              pasted without one is trackable but never findable by search at
              all. Worth saying here rather than letting it be discovered. */}
            <Field label="description">
              <textarea
                value={draft.body ?? ""}
                onChange={(e) => setDraft({ ...draft, body: e.target.value })}
                rows={6}
                className={`${INPUT} resize-y font-sans leading-relaxed`}
                placeholder="Paste the posting text. This is what search reads — without it the posting is tracked but never findable."
              />
            </Field>

            {create.error && (
              <p className="text-xs text-status-rejected">
                {create.error instanceof ApiError
                  ? create.error.detail
                  : String(create.error)}
              </p>
            )}
          </div>

          <div className="flex shrink-0 items-center justify-between border-t border-hairline px-4 py-3">
            {/* Both halves of search need a step this dialog cannot do. The
                dense side needs a vector; the keyword side reads a prebuilt
                index that a fresh chunk is not in. `cli embed` does both, so
                that is the honest instruction rather than "searchable now". */}
            <span className="font-mono text-2xs text-text-faint">
              tracked at once · findable after{" "}
              <span className="text-text-muted">cli embed</span>
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={onClose}
                className="px-2 py-1 text-xs text-text-muted hover:text-text"
              >
                cancel
              </button>
              <button
                type="submit"
                disabled={!ready || create.isPending}
                className="border border-signal bg-signal/12 px-3 py-1 text-xs text-signal rounded-xs disabled:opacity-40"
              >
                {create.isPending ? "adding…" : "add posting"}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}

const INPUT =
  "w-full border border-hairline bg-transparent px-2 py-1 text-xs rounded-xs placeholder:text-text-faint";

function Field({
  label,
  required,
  className = "",
  children,
}: {
  label: string;
  required?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={`flex flex-col gap-1 ${className}`}>
      <span className="font-mono text-2xs uppercase tracking-wide text-text-faint">
        {label}
        {required && <span className="text-signal"> *</span>}
      </span>
      {children}
    </label>
  );
}
