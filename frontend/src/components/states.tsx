/**
 * Empty and error states, written rather than defaulted.
 *
 * An empty table says what to run to populate it. An error names what failed
 * and what to do about it. A spinner with no explanation is not a state.
 */

export function EmptyState({
  title,
  detail,
  command,
}: {
  title: string;
  detail: string;
  command?: string;
}) {
  return (
    <div className="flex flex-col items-start gap-2 p-8">
      <h2 className="text-sm font-medium">{title}</h2>
      <p className="max-w-prose text-xs text-text-muted">{detail}</p>
      {command && (
        <pre className="mt-2 border border-hairline bg-surface-sunken px-3 py-2 font-mono text-2xs">
          {command}
        </pre>
      )}
    </div>
  );
}

export function ErrorState({ error, hint }: { error: unknown; hint?: string }) {
  const message =
    error instanceof Error ? error.message : typeof error === "string" ? error : "Unknown error";
  return (
    <div className="flex flex-col items-start gap-2 p-8">
      <h2 className="text-sm font-medium">Something failed</h2>
      <p className="max-w-prose font-mono text-xs text-text-muted">{message}</p>
      <p className="max-w-prose text-xs text-text-faint">
        {hint ?? (
          <>
            The API may not be running. Start both servers with{" "}
            <span className="font-mono">python dev.py</span> from the project root.
          </>
        )}
      </p>
    </div>
  );
}

export function LoadingState({ what }: { what: string }) {
  return <p className="p-8 font-mono text-2xs text-text-faint">loading {what}…</p>;
}
