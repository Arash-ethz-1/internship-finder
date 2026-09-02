import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  ApiError,
  getProfile,
  getProfileDoc,
  deleteProfileDoc,
  saveProfileDoc,
  type ProfileSummary,
} from "../api/client";
import { ErrorState, LoadingState } from "../components/states";

/**
 * The write-ups every letter is grounded in.
 *
 * This is the corpus the letter drafter retrieves from, and it was previously
 * only editable in a text editor — followed by remembering to run
 * `cli ingest-profile`. Forgetting meant every subsequent letter was grounded
 * in text the author had already rewritten, with nothing anywhere saying so.
 * Saving here rewrites the file and re-chunks it in the same request, so the
 * two cannot drift.
 *
 * Saving embeds too, which the postings grid deliberately does not: the whole
 * of `profile/` is a few dozen chunks, so it costs seconds, where the posting
 * corpus is 135,000 and had to go to a cluster. That asymmetry is what lets a
 * write-up you just edited ground the very next letter.
 */
export function Profile() {
  const queryClient = useQueryClient();
  const [chosen, setChosen] = useState<string | null>(null);
  // Unsaved text, keyed by document. Derived-not-synced on purpose: mirroring
  // the fetched document into a `text` state inside an effect means the
  // editor's contents and the server's are two sources of truth that have to
  // be kept in step, and every "my edit vanished" bug lives in that gap.
  // Switching documents and switching back therefore keeps your edit.
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  // Which write-up the delete button is armed for. One click arms, the second
  // deletes; blurring disarms.
  const [confirming, setConfirming] = useState<string | null>(null);

  const list = useQuery({ queryKey: ["profile"], queryFn: getProfile });

  // Open the first real write-up rather than an empty pane. README and the
  // placeholder are listed but not ingested, so they are the wrong default.
  const documents = list.data?.documents ?? [];
  const fallback =
    documents.find((entry) => entry.ingested)?.slug ?? documents[0]?.slug;
  const slug = chosen ?? fallback ?? null;

  const doc = useQuery({
    queryKey: ["profile", slug],
    queryFn: () => getProfileDoc(slug as string),
    enabled: slug !== null,
  });

  const text = (slug && edits[slug]) ?? doc.data?.text ?? "";
  const dirty = doc.data !== undefined && text !== doc.data.text;

  function setText(next: string) {
    if (slug) setEdits((current) => ({ ...current, [slug]: next }));
  }

  useEffect(() => {
    if (!saved) return;
    const timer = setTimeout(() => setSaved(false), 2000);
    return () => clearTimeout(timer);
  }, [saved]);

  // Creating a write-up is a save to a slug that has no file yet, so it is the
  // same call — only the heading it starts from differs.
  const create = useMutation({
    mutationFn: (newSlug: string) =>
      saveProfileDoc(newSlug, `# ${newSlug.replace(/-/g, " ")}\n\n`),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["profile"] }),
  });

  const remove = useMutation({
    mutationFn: (target: string) => deleteProfileDoc(target),
    onSuccess: (_void, target) => {
      // Drop any unsaved edit with it, and fall back to whichever write-up the
      // list picks next rather than pointing at a file that is gone.
      setEdits((current) => {
        const rest = { ...current };
        delete rest[target];
        return rest;
      });
      setChosen(null);
      void queryClient.invalidateQueries({ queryKey: ["profile"] });
    },
  });

  const save = useMutation({
    mutationFn: () => saveProfileDoc(slug as string, text),
    onSuccess: (updated) => {
      setSaved(true);
      // The edit has landed, so drop it and let the server's copy be the
      // truth again. Without this the document reads as permanently dirty.
      setEdits((current) => {
        const rest = { ...current };
        delete rest[updated.slug];
        return rest;
      });
      void queryClient.invalidateQueries({ queryKey: ["profile"] });
    },
  });

  if (list.isPending) return <LoadingState what="your write-ups" />;
  if (list.error) return <ErrorState error={list.error} />;

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-64 shrink-0 flex-col overflow-y-auto border-r border-hairline">
        <div className="border-b border-hairline px-3 py-3">
          <div className="font-mono text-2xs uppercase tracking-wide text-text-faint">
            write-ups
          </div>
          <p className="mt-1.5 text-2xs leading-relaxed text-text-faint">
            Every letter is built from these and nothing else. A claim that is
            not here cannot appear in a draft.
          </p>
        </div>

        <div className="flex-1">
          {documents.map((entry) => (
            <DocRow
              key={entry.slug}
              entry={entry}
              active={entry.slug === slug}
              onSelect={() => setChosen(entry.slug)}
            />
          ))}
          {documents.length === 0 && (
            <p className="px-3 py-4 text-xs text-text-faint">
              Nothing in <span className="font-mono">profile/</span> yet. Add
              one per project — what you built, with what, and what happened.
            </p>
          )}

          {/* Creating and saving are the same request: PUT to a slug that has
              no file yet writes it. The name is typed in the page rather than
              in a `window.prompt`, which is a browser chrome dialog this app
              has no control over and which looks nothing like the rest of it. */}
          {adding ? (
            <form
              className="border-b border-hairline px-3 py-2"
              onSubmit={(event) => {
                event.preventDefault();
                const slugified = slugify(newName);
                if (!slugified) return;
                setAdding(false);
                setNewName("");
                setChosen(slugified);
                if (!documents.some((d) => d.slug === slugified)) {
                  create.mutate(slugified);
                }
              }}
            >
              <input
                autoFocus
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onBlur={() => !newName.trim() && setAdding(false)}
                onKeyDown={(e) => e.key === "Escape" && setAdding(false)}
                placeholder="e.g. maze solver"
                aria-label="Name for the new write-up"
                className="w-full border border-hairline bg-transparent px-2 py-1 text-xs rounded-xs placeholder:text-text-faint"
              />
              <div className="mt-1 flex items-center justify-between font-mono text-2xs text-text-faint">
                <span>{slugify(newName) || "…"}.md</span>
                <button
                  type="submit"
                  disabled={!slugify(newName) || create.isPending}
                  className="text-signal disabled:opacity-40"
                >
                  create
                </button>
              </div>
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setAdding(true)}
              className="w-full border-b border-hairline px-3 py-2 text-left text-xs text-text-muted hover:bg-surface-sunken hover:text-signal"
            >
              + add a write-up
            </button>
          )}
        </div>

        {list.data && list.data.pending_embedding > 0 && (
          <div className="border-t border-hairline px-3 py-2 font-mono text-2xs text-text-faint">
            {list.data.pending_embedding} chunk(s) not embedded
            <div className="mt-1 text-text-muted">
              saving embeds — run cli embed if this persists
            </div>
          </div>
        )}
      </aside>

      <main className="flex min-w-0 flex-1 flex-col px-6 py-4">
        {slug === null ? (
          <p className="text-xs text-text-faint">Pick a write-up to edit.</p>
        ) : (
          <>
            <div className="flex shrink-0 items-baseline justify-between">
              <h1 className="text-sm">{doc.data?.title ?? slug}</h1>
              <div className="flex items-center gap-3">
                {doc.data && !doc.data.ingested && (
                  <span
                    className="font-mono text-2xs text-text-faint"
                    title="README and the example file are deliberately not grounded in — a letter built on placeholder text is how a letter starts lying."
                  >
                    not used for letters
                  </span>
                )}
                <span className="font-mono text-2xs text-text-faint">
                  {doc.data
                    ? `${doc.data.chunks} chunk(s), ${doc.data.embedded} embedded`
                    : ""}
                </span>
                <button
                  type="button"
                  onClick={() => save.mutate()}
                  disabled={!dirty || save.isPending}
                  className="border border-signal bg-signal/12 px-3 py-1 font-mono text-2xs text-signal rounded-xs disabled:opacity-40"
                >
                  {save.isPending
                    ? "saving…"
                    : saved && !dirty
                      ? "saved"
                      : "save"}
                </button>
                {/* Two clicks, not a modal: a write-up is a file you wrote and
                    losing it to a stray click would be worse than the extra
                    step, but a confirm dialog for one file is heavy. */}
                <button
                  type="button"
                  onClick={() => {
                    if (confirming === slug) remove.mutate(slug);
                    else setConfirming(slug);
                  }}
                  onBlur={() => setConfirming(null)}
                  disabled={remove.isPending}
                  className={`border px-2 py-1 font-mono text-2xs rounded-xs disabled:opacity-40 ${
                    confirming === slug
                      ? "border-status-rejected bg-status-rejected/12 text-status-rejected"
                      : "border-hairline text-text-faint hover:text-status-rejected"
                  }`}
                  title="Delete this write-up and the chunks built from it"
                >
                  {remove.isPending
                    ? "deleting…"
                    : confirming === slug
                      ? "really delete?"
                      : "delete"}
                </button>
              </div>
            </div>

            {save.error && (
              <p className="mt-2 text-xs text-status-rejected">
                {save.error instanceof ApiError
                  ? save.error.detail
                  : String(save.error)}
              </p>
            )}

            {doc.isPending && <LoadingState what="the write-up" />}
            {doc.error && <ErrorState error={doc.error} />}

            {doc.data && (
              <>
                <textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  className="mt-3 min-h-0 flex-1 resize-none border border-hairline bg-transparent p-4 font-mono text-xs leading-relaxed rounded-xs"
                  aria-label={`${slug} write-up`}
                  spellCheck
                />
                <p className="mt-2 shrink-0 font-mono text-2xs text-text-faint">
                  markdown · chunks split on headings, so a heading per topic
                  retrieves better than one long block
                  {dirty && (
                    <span className="ml-2 text-status-interviewing">
                      unsaved
                    </span>
                  )}
                </p>
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}

/** A filename from a human-typed name. Shown live under the input so the file
 *  it will create is never a surprise. */
function slugify(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
}

function DocRow({
  entry,
  active,
  onSelect,
}: {
  entry: ProfileSummary;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex w-full flex-col gap-0.5 border-b border-hairline px-3 py-2 text-left ${
        active ? "bg-signal/8" : "hover:bg-surface-sunken"
      }`}
      aria-current={active}
    >
      <span
        className={`truncate text-xs ${
          entry.ingested ? "text-text" : "text-text-faint"
        }`}
      >
        {entry.title}
      </span>
      <span className="font-mono text-2xs text-text-faint">
        {entry.slug}
        {entry.ingested && ` · ${entry.chunks} chunk(s)`}
        {/* An unembedded chunk is findable by keyword but not by meaning, and
            the letter drafter searches by meaning — so this is the difference
            between a write-up that grounds letters and one that does not yet. */}
        {entry.ingested && entry.embedded < entry.chunks && (
          <span className="text-status-interviewing">
            {" "}
            · {entry.chunks - entry.embedded} unembedded
          </span>
        )}
      </span>
    </button>
  );
}
