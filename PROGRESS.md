# Progress

Running state of the project. `plan.md` is the spec (what we intend to build);
this file is the log (what is actually built, what is next, and what we decided
along the way that the spec does not say).

**Last updated:** 2026-08-30, end of Phase 4 and 5.

---

## Checkpoint — 2026-08-30, session 2

Phases 1, 2, 2.5, 3, 6 and 7 done, plus the Lever EU fix. **187 tests pass,
ruff clean.** 5,149 postings from 22 companies.

Discovery verified live through `--from file`: 15 niche robotics names in,
7 verified out, spread across all three boards (ANYbotics on Lever, Skydio on
Ashby, Agility on Greenhouse) — the cross-board search is what found them. A
second run cost **zero** HTTP requests, which is the cache working.

**Still unverified:** `discover --from crawl`. `index.commoncrawl.org` has been
unreachable across two sessions (connect timeout from any client, not a code
problem — the same query returned 1,610 Greenhouse and 1,920 Ashby tokens
before it went down). Retry when it is back:

```
uv run python -m agent_app.cli discover --from crawl --source ashby --limit 40
```

## Resume here

**Arash:** open `backend/src/agent_app/core/chunking.py` and write
`chunk_posting`. It is the first Category B function and everything downstream
waits on it. The docstring lists what must hold.

**Claude:** nothing left. Every phase that can be built without a Category B
body is built. The remaining work is the nine reserved functions.

The moment `chunk_posting` exists, this runs end to end:

```
cd backend
uv run python -m agent_app.cli embed     # chunks -> vectors.npy
uv run python -m agent_app.cli status    # confirms the counts line up
```

and once `search` follows, `/letters/:id` and the retrieval trace light up.

`python dev.py` then <http://localhost:5173>. Note Vite binds to `localhost`,
not `127.0.0.1` — checking the wrong one looks like the server is down.

The `/chat` route renders its error state rather than a live agent, and
`/letters/:id` reports 501, until `run_agent` and `retrieval.search` exist.
That is correct behaviour, not breakage.

Two phases were added to `plan.md` on 2026-08-30 at the author's request:
**Phase 2.5** (company discovery) and **Phase 10** (application tracking from
email, previously a v2 non-goal).

---

## Phase status

| # | Phase | Owner | Status | Commit |
|---|---|---|---|---|
| 1 | Scaffold | Claude | ✅ done | `872e4a5` |
| 2 | Ingestion | Claude | ✅ done | `403a5c8` |
| 3 | Core stubs (Category B signatures) | Claude | ✅ done | `49a8989` |
| 3.5 | `chunk_posting`, `chunk_profile_doc` | **Arash** | ⬜ **your turn — start here** | |
| 4 | Embeddings plumbing | Claude | ✅ done (end-to-end check needs chunks) | |
| 5 | Profile corpus | Claude | ✅ format + ingestion path; needs chunk_profile_doc to run | |
| 6 | Letter drafting | Claude | ✅ done | |
| 7 | API | Claude | ✅ done | |
| 8 | Frontend | Claude | ✅ done | |
| 9 | CLI and CI | Claude | ✅ done | |
| 10 | Application tracking from email | Claude | ⬜ new, added 2026-08-30 | |
| 2.5 | Company discovery | Claude | ✅ done (crawl path unverified live) | |
| — | Lever EU API host fix | Claude | ✅ done | |

Category B functions the author writes by hand, none of them started:
`chunk_posting`, `chunk_profile_doc`, `search`, `dense_scores`, `bm25_scores`,
`fuse`, `run_agent`, the four `TOOL_SCHEMAS` descriptions, `recall_at_k`,
`run_eval`.

**Everything else is done.** Each one has a caller waiting for it, a test
asserting it still raises, and an error message naming it when something tries
to run. Suggested order: `chunk_posting` (unblocks embedding), then
`dense_scores` / `bm25_scores` / `fuse` / `search` (unblocks letters, eval and
the trace), then `run_agent` (unblocks `/chat`).

---

## Amendments to plan.md

Decided in conversation on 2026-08-30. These override `plan.md` where they
conflict. Do not "fix" them back.

1. **`run_agent` is a generator**, not a blocking call:
   `run_agent(user_message, history, max_iters=12) -> Iterator[AgentEvent]`,
   with `AgentResult` carried by the final event. Overrides the Phase 3
   signature. One function serves both the SSE route and the CLI REPL.
   Agreed simplification: yield one text chunk per loop iteration rather than
   per-token deltas — same wire format, much simpler loop.
2. **The author writes chunking before Phase 4.** Phase 4's check is
   meaningless on an empty chunks table.
3. **Embeddings: Voyage AI** (`voyage-3.5`, dim 1024, `VOYAGE_API_KEY`).
   Anthropic has no embeddings endpoint, so this is a second vendor.
4. **Ambient state via `runtime.py`.** The Category B signatures take no
   database or provider, so `get_db()` (and later `get_provider()`,
   `get_vectors()`) are how they reach dependencies. Connections are
   thread-local, because FastAPI serves sync endpoints from a threadpool.
5. **`component_scores` keys are `"dense"` and `"bm25"`**, and the two values
   sum to `score`. That is what makes the stacked bar in the retrieval trace
   honest rather than decorative.
6. **No network in tests.** Recorded JSON fixtures under `tests/fixtures/`.
   CI needs no API keys.
7. **`dev.py` replaces the Makefile/justfile** — neither `make` nor `just` is
   installed on this machine.
8. **Untriaged is a real state.** A posting with no `applications` row is not
   the same as `interested`, and gets its own filter.

---

## Deviations from the letter of plan.md

Small, deliberate, and worth knowing before someone "corrects" them.

- **`ingest/runner.py`** is a fifth module in a folder the plan lists four for.
  The HTTP client, upserts and orchestration had to live somewhere.
- **`Posting` lives in `db.py`**, not `ingest/`. Both halves need it, and this
  stops `core/` importing from `ingest/`.
- **`postings.body_hash`** is not in the plan's schema. It is how re-ingestion
  tells an edited posting from an unchanged one, so embeddings are not thrown
  away needlessly.
- **Partial unique index on `chunks.vector_row`** — two chunks sharing a row in
  `vectors.npy` is silent corruption; the database refuses it.
- **Geist via `@fontsource-variable/geist`**, not the `geist` npm package. The
  official one is Next.js-specific and does not work under Vite. Font families
  are `"Geist Variable"` and `"Geist Mono Variable"`.
- **Phase 1 shipped tests** where the plan allowed an empty suite.
- **`cli` reconfigures stdout to UTF-8.** Windows consoles default to cp1252
  and `status` died with UnicodeEncodeError partway through its own output.

---

## What actually works right now

```
python dev.py                 # API on :8000 and Vite on :5173
python dev.py test            # ruff + pytest
python dev.py lint            # ruff + format + tsc + eslint

cd backend
uv run python -m agent_app.cli --help
uv run python -m agent_app.cli ingest                    # fetch every board
uv run python -m agent_app.cli discover --from file --file names.txt
uv run python -m agent_app.cli status                    # the pipeline
uv run python -m agent_app.cli companies                 # what was verified
```

The dashboard at <http://localhost:5173> is real: `/postings` lists 5,149
postings with working filters, keyboard navigation and status changes that
persist; `/stats` shows the pipeline. `/chat` and `/letters/:id` render honest
error states, because they need the Category B functions.

**Data as of the last ingest run:** 5,149 postings from 22 companies across all
3 sources. 67 `intern`, 77 `newgrad`, 5,005 `unknown`. 0 chunks, because
`chunk_posting` is not written. **204 tests pass.**

---

## Open with the author

- **`discover --from crawl` is still unverified live.** Common Crawl has been
  unreachable across two sessions. Everything else in discovery is verified.
- **`.gitattributes`** — git warns `LF will be replaced by CRLF` on every
  commit. A one-line `* text=auto` would silence it, but it rewrites every
  file's line endings and would show up as "all files changed". Not added
  unprompted.
- **The project lives under OneDrive**, where every file is a cloud
  placeholder. `.vscode/settings.json` stops VS Code watching the 12k ignored
  dependency files, which was causing phantom "uncommitted changes". The
  permanent fix is moving the project out of OneDrive.
- **Whether discovery leftovers get an agentic retry loop** (design discussed,
  not decided).

---

## Gotchas that cost time once

- **The level heuristic must not trust the body.** Senior postings routinely
  carry a disclaimer like *"if you are an intern or new grad, do not apply
  here"*. A naive keyword scan mislabelled 111 of 4,844 postings. The title
  matches broadly; the body only counts when a phrase asserts the role.
- **LLM knowledge of ATS vendors is stale.** 12 of 43 probed board tokens
  404'd — not invented companies, but companies that migrated platforms.
  Always verify a token with a real request before trusting it.
- **PowerShell mangles `git commit -m` with a here-string** containing double
  quotes. Write the message to a file and use `git commit -F`.
- **VS Code needs the project interpreter** (`backend\.venv\Scripts\python.exe`)
  or every `import fastapi` shows a false error.
