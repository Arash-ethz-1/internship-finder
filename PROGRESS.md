# Progress

Running state of the project. `plan.md` is the spec (what we intend to build);
this file is the log (what is actually built, what is next, and what we decided
along the way that the spec does not say).

**Last updated:** 2026-08-30, end of Phase 2.

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

**Claude:** Phase 8, the frontend. The API it consumes is complete and
serving real data. Phases 4 and 5 cannot start until `chunk_posting` exists.

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
| 4 | Embeddings plumbing | Claude | ⬜ blocked on 3.5 | |
| 5 | Profile corpus | Claude | ⬜ blocked on 3.5 | |
| 6 | Letter drafting | Claude | ✅ done | |
| 7 | API | Claude | ✅ done | |
| 8 | Frontend | Claude | ⬜ **next for Claude** | |
| 9 | CLI and CI | Claude | ⬜ | |
| 10 | Application tracking from email | Claude | ⬜ new, added 2026-08-30 | |
| 2.5 | Company discovery | Claude | ✅ done (crawl path unverified live) | |
| — | Lever EU API host fix | Claude | ✅ done | |

Category B functions the author writes by hand, none of them started:
`chunk_posting`, `chunk_profile_doc`, `search`, `dense_scores`, `bm25_scores`,
`fuse`, `run_agent`, the four `TOOL_SCHEMAS` descriptions, `recall_at_k`,
`run_eval`.

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

---

## What actually works right now

```
uv run python -m agent_app.cli init-db     # create data/postings.db
uv run python -m agent_app.cli ingest      # fetch all 15 boards
uv run python -m agent_app.cli ingest --source ashby --company OpenAI
python dev.py api                          # /api/health and /docs only
python dev.py test                         # ruff + pytest
```

Nothing else exists yet. `/api/postings`, `/chat`, search, embeddings, the
dashboard: none of it.

**Data as of the last ingest run:** 4,844 postings from 15 companies across
all 3 sources. Levels: 59 `intern`, 74 `newgrad`, 4,711 `unknown`. 1,982
flagged remote. 0 with a deadline (no board publishes one). 74 tests pass.

---

## Open with the author

- **Company discovery** — replacing the hardcoded `companies.toml` with an
  LLM-proposes / HTTP-verifies pipeline plus a `companies` table that caches
  hits *and* misses so nothing is re-checked. Agreed in principle; timing
  agreed as "after Phase 3". Design settled: model supplies company *names*
  (reliable), code derives token slugs, HTTP verifies across all three boards
  (because companies migrate ATS vendors — that broke 8 of 13 Lever guesses).
  Not yet decided: whether the leftovers get an agentic retry loop.
- **`.gitattributes`** — git warns `LF will be replaced by CRLF` on every
  commit. A one-line `* text=auto` would silence it. Not added unprompted.

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
