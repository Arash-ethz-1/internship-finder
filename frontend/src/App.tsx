import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router";

import { getInbox } from "./api/client";
import { Chat } from "./routes/Chat";
import { Inbox } from "./routes/Inbox";
import { Letters } from "./routes/Letters";
import { Postings } from "./routes/Postings";
import { Stats } from "./routes/Stats";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

type Theme = "light" | "dark" | "system";

function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem("theme") as Theme | null) ?? "system",
  );

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);

  return [theme, setTheme];
}

function Nav() {
  const [theme, setTheme] = useTheme();
  // A review queue nobody looks at is the same as no review queue, so the
  // count is the one number promoted to the nav. `pending` counts only
  // suggestions that actually propose a status change.
  const { data: inbox } = useQuery({
    queryKey: ["inbox"],
    queryFn: () => getInbox(),
    staleTime: 60_000,
  });
  const link = ({ isActive }: { isActive: boolean }) =>
    `px-2 py-1 font-mono text-2xs rounded-xs ${
      isActive ? "text-signal" : "text-text-muted hover:text-text"
    }`;

  return (
    <nav className="flex shrink-0 items-center gap-1 border-b border-hairline px-3 py-1.5">
      <span className="mr-3 font-mono text-2xs text-text-faint">screener</span>
      <NavLink to="/chat" className={link}>
        search
      </NavLink>
      <NavLink to="/postings" className={link}>
        list
      </NavLink>
      <NavLink to="/inbox" className={link}>
        inbox
        {inbox && inbox.pending > 0 && (
          <span className="ml-1.5 tabular-nums text-signal">{inbox.pending}</span>
        )}
      </NavLink>
      <NavLink to="/stats" className={link}>
        stats
      </NavLink>
      <button
        type="button"
        onClick={() => setTheme(theme === "dark" ? "light" : theme === "light" ? "system" : "dark")}
        className="ml-auto px-2 py-1 font-mono text-2xs text-text-faint hover:text-text"
        title="Cycle theme: system, dark, light"
      >
        {theme}
      </button>
    </nav>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="flex h-screen flex-col">
          <Nav />
          <div className="min-h-0 flex-1">
            <Routes>
              <Route path="/" element={<Navigate to="/chat" replace />} />
              <Route path="/postings" element={<Postings />} />
              <Route path="/chat" element={<Chat />} />
              <Route path="/inbox" element={<Inbox />} />
              <Route path="/letters/:id" element={<Letters />} />
              <Route path="/stats" element={<Stats />} />
              <Route
                path="*"
                element={
                  <div className="p-8 text-xs text-text-muted">
                    No such page.{" "}
                    <NavLink to="/postings" className="text-signal underline-offset-2 hover:underline">
                      Back to postings
                    </NavLink>
                  </div>
                }
              />
            </Routes>
          </div>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
