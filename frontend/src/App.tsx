import { useEffect, useState } from "react";

import { getHealth } from "./api/client";

type ApiState = { kind: "checking" } | { kind: "up"; version: string } | { kind: "down" };

/**
 * Phase 1 placeholder. Its only job is to prove the toolchain works end to
 * end: tokens applied, fonts loaded, and the Vite proxy reaching the API.
 * Phase 8 replaces this with the four real routes.
 */
export function App() {
  const [api, setApi] = useState<ApiState>({ kind: "checking" });

  useEffect(() => {
    let cancelled = false;
    getHealth()
      .then((health) => {
        if (!cancelled) setApi({ kind: "up", version: health.version });
      })
      .catch(() => {
        if (!cancelled) setApi({ kind: "down" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="min-h-screen px-8 py-10">
      <h1 className="text-lg font-medium tracking-tight">Internship screener</h1>
      <p className="mt-1 text-xs text-text-muted">Scaffold. Phase 8 builds the real interface.</p>

      <dl className="mt-8 max-w-xl border-t border-hairline text-xs">
        <div className="flex items-center justify-between border-b border-hairline py-2">
          <dt className="text-text-muted">Backend API</dt>
          <dd className="font-mono">
            {api.kind === "checking" && <span className="text-text-faint">checking…</span>}
            {api.kind === "up" && <span className="text-signal">up · v{api.version}</span>}
            {api.kind === "down" && (
              <span className="text-text-muted">
                not reachable — run <span className="text-text">python dev.py</span>
              </span>
            )}
          </dd>
        </div>
        {(["/postings", "/chat", "/letters/:id", "/stats"] as const).map((route) => (
          <div
            key={route}
            className="flex items-center justify-between border-b border-hairline py-2"
          >
            <dt className="font-mono text-text-muted">{route}</dt>
            <dd className="text-text-faint">phase 8</dd>
          </div>
        ))}
      </dl>
    </main>
  );
}
