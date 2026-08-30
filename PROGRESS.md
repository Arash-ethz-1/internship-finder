# Progress

Running state of the project. `plan.md` is the spec (what we intend to build);
this file is the log (what is actually built, what is next, and what we decided
along the way that the spec does not say).

**Last updated:** 2026-08-30, late. Every phase Claude can build is built.

---

## Checkpoint — 2026-08-30, end of session 2

**Every phase that does not need a Category B body is done.** Phases 1, 2, 2.5,
3, 4, 5, 6, 7, 8, 9, plus the Lever EU fix and a whitespace fix in ingestion.
244 tests, ruff clean, CI green on GitHub. 5,149 postings from 22 companies.

What is left is the nine reserved functions, which the author writes.

**Still unverified:** `discover --from crawl`. `index.commoncrawl.org` has been
unreachable across two sessions (connect timeout from any client, not a code
problem — the same query returned 1,610 Greenhouse and 1,920 Ashby tokens
before it went down). Retry when it is back:

```
uv run python -m agent_app.cli discover --from crawl --source ashby --limit 40
```

## Resume here

**The author is working through the nine Category B functions.** Start here:

```
cd backend
uv run python check.py            # the scoreboard: 0/9 done, 68 tests
uv run python check.py 3 -v       # one problem, with the failures
uv run python try_chunking.py     # see chunking output on a real posting
```

Problem statements are in **`backend/exercises/README.md`** — one per function,
with the contract, worked examples, the formulas for BM25 and RRF, and what is
left to the author's judgement.

Suggested order. It is a dependency chain, not a difficulty ramp — `dense_scores`
is the easiest of the nine and depends on nothing, so it is a fine place to
start if `chunk_posting` feels daunting.

| # | Function | Unlocks |
|---|---|---|
| 1 | `chunk_posting` | `cli embed` turns 5,149 postings into vectors |
| 2 | `chunk_profile_doc` | `cli ingest-profile` |
| 3 | `dense_scores` | — (start here for a quick win) |
| 4 | `bm25_scores` | — |
| 5 | `fuse` | — |
| 6 | `search` | **`/letters` works, the score bars render** |
| 7 | `run_agent` | **`/chat` comes alive** |
| 8 | the four tool descriptions | the agent picks tools *well* |
| 9 | `recall_at_k`, `run_eval` | `cli eval` — measurement |

Roughly 170 lines of Python in total, plus a hand-labelled
`data/eval/queries.jsonl` for problem 9.

**Agreed 2026-08-30:** once the author says a function is done, Claude rewrites
it with its own best version and explains the changes. Until then rule 1 in
CLAUDE.md is absolute. This exception is in CLAUDE.md too.

`python dev.py` then <http://localhost:5173>. Vite binds to `localhost`, not
`127.0.0.1` — checking the wrong one looks like the server is down.

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

**Everything else is done.** Each one has a caller waiting for it, a test in
`tests/test_category_b.py` asserting it still raises, an exercise suite in
`exercises/` with 68 tests, and an error message naming it when something tries
to run.

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
