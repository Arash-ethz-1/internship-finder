# Progress

Running state of the project. `plan.md` is the spec (what we intend to build);
this file is the log (what is actually built, what is next, and what we decided
along the way that the spec does not say).

**Last updated:** 2026-09-01, session 4. Phase 10 is built. Every phase in
`plan.md` is now done.

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

**Session 4, 2026-09-01: Phase 10 built, the last one.** Gmail sync, matching,
classification, the `/inbox` review queue, `cli sync-email`. 290 main tests (57
new) and 68 exercise tests pass; ruff, tsc, eslint and the frontend build are
clean. `check.py` still reads 9/9.

Phase 10's check was run end to end against a throwaway database: sync fetched,
matched and classified without changing a single `applications` row;
suggestions appeared at `GET /api/inbox`; accepting one moved `applied` ->
`rejected` and wrote a history note reading
`from email: "Your application to Figma" — no-reply@figma.com (gmail:18f2a9c)`;
a re-run examined nothing twice. The `/inbox` page was served and the Vite
proxy verified, but **it has not been looked at in a browser** — the Chrome
extension would not connect. Worth eyeballing once.

**Still blocked on the same thing as last session: there are no API keys.**
`backend/.env` now lists all four variables and every value is still empty.
Nothing downstream of retrieval has ever run against real data.

1. **`VOYAGE_API_KEY`** and **`ANTHROPIC_API_KEY`**, as before.
   *Worth deciding before the first `cli embed`:* `voyage-3.5` has no free
   allowance, but `voyage-4` is the same $0.06/M **and** carries 200M free
   tokens, with the same default dimension of 1024. The corpus is ~7.9M tokens,
   so the whole thing is free on `voyage-4` and about $0.55 on `voyage-3.5`.
   Switching later forces a full re-embed; right now there are zero vectors, so
   the switch is free. One line: `EMBEDDING_MODEL=voyage-4`.
2. **`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`**, optional, for `sync-email`
   only. Google Cloud project -> enable the Gmail API -> OAuth client of type
   "Desktop app". Steps are in `.env.example`.
3. **`data/eval/queries.jsonl` still does not exist.** `run_eval` works; the
   labels are the author's judgement. A few dozen lines of
   `{"query": ..., "relevant_posting_ids": [...]}` is what turns tuning from
   vibes into measurement.

Then, in order:

```
cd backend
uv run python -m agent_app.cli embed     # 5,149 postings -> chunks -> vectors
uv run python -m agent_app.cli ingest-profile
uv run python -m agent_app.cli eval      # the first real retrieval number
uv run python try_chunking.py --all      # chunking health over 200 postings
```

**The author still intends to rewrite `run_agent` himself** — *"i will rewrite
it tomorrow with fresh soul."* Treat `core/agent.py` as a placeholder, and do
not polish it unasked.

Problem statements are in **`backend/exercises/README.md`**, and the exercise
suite is the regression test for anything rewritten.

`python dev.py` then <http://localhost:5173>. Vite binds to `localhost`, not
`127.0.0.1` — checking the wrong one looks like the server is down.

---

## Phase status

| # | Phase | Owner | Status | Commit |
|---|---|---|---|---|
| 1 | Scaffold | Claude | ✅ done | `872e4a5` |
| 2 | Ingestion | Claude | ✅ done | `403a5c8` |
| 3 | Core stubs (Category B signatures) | Claude | ✅ done | `49a8989` |
| 3.5 | `chunk_posting`, `chunk_profile_doc` | Arash + Claude | ✅ done 2026-08-31 | |
| 4 | Embeddings plumbing | Claude | ✅ done (end-to-end check needs an API key) | |
| 5 | Profile corpus | Claude | ✅ done; runs, needs write-ups in `profile/` | |
| 6 | Letter drafting | Claude | ✅ done | |
| 7 | API | Claude | ✅ done | |
| 8 | Frontend | Claude | ✅ done | |
| 9 | CLI and CI | Claude | ✅ done | |
| 10 | Application tracking from email | Claude | ✅ done 2026-09-01 | |
| 2.5 | Company discovery | Claude | ✅ done (crawl path unverified live) | |
| — | Lever EU API host fix | Claude | ✅ done | |

**Every phase in `plan.md` is now done.** What is left is not code: API keys,
a labelled eval set, real write-ups in `profile/`, and the author's own rewrite
of `run_agent`.

All nine reserved functions are now written: `chunk_posting`,
`chunk_profile_doc`, `dense_scores`, `bm25_scores`, `fuse`, `search`,
`run_agent`, the four `TOOL_SCHEMAS` descriptions, `recall_at_k` and
`run_eval`.

`tests/test_category_b.py` is deleted — it asserted every one of them still
raised, and its own docstring said to delete a case once implemented. The
"stops at the unwritten Category B" tests across `test_api.py`, `test_cli.py`,
`test_letters.py`, `test_core_scaffolding.py` and `test_profile.py` were
rewritten to assert the real behaviour instead: an empty index returns no
hits, a missing key is a clean error, a letter with nothing to ground it in is
refused.

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
9. **Claude wrote all nine Category B functions on 2026-08-31**, at the
   author's request, under the "give me your best version" exception. The
   exercises in `backend/exercises/` stay as they are: they are now the
   regression tests for the author's own rewrites. Rule 1 of CLAUDE.md no
   longer has anything to protect unless the author re-stubs a function.
10. **Phase 10 uses the OAuth loopback flow, not the device flow** (decided
    2026-09-01). `plan.md` names "OAuth device flow". Google restricts that
    flow to a fixed scope list — `openid`, `email`, `profile`, two Drive
    scopes, two YouTube ones — and **no Gmail scope is on it**, so a device
    flow implementation would fail at the authorisation request. What is built
    is the flow Google documents for desktop apps: a loopback redirect to
    `127.0.0.1` on an ephemeral port, with PKCE. Every property the plan
    actually asked for is unchanged — `gmail.readonly` and nothing else, the
    refresh token in `data/` and gitignored, nothing ever sent. This is a
    constraint of Google's, not a preference.

---

## Deviations from the letter of plan.md

Small, deliberate, and worth knowing before someone "corrects" them.

- **`ingest/chunks.py`** is a sixth module in that folder, added 2026-09-01.
  Nothing ever called `chunk_posting`: Phase 2 ends at "the body is stored",
  Phase 4 begins at "chunks without a vector_row get one", and the step between
  them belonged to no phase — so `chunks` stayed empty and every posting was
  invisible to search. `chunk_pending_postings()` closes it, and both `cli
  ingest` and `cli embed` call it. A posting is pending when it has no chunk
  rows at all, which makes it self-healing: the existing 5,149 got chunked on
  the next run, and a body change still works because the upsert drops the old
  rows. `tests/test_chunk_postings.py` is the regression guard.
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
- **`inbox/` is a new top-level package**, a sibling of `ingest/` rather than
  part of it. Both pull from an external source, but this one has its own
  OAuth, its own vendor, and a rule the job boards do not have: it only ever
  suggests.
- **`email_matches` has three columns the plan's schema does not list.**
  `sender` — a review queue that cannot show who an email is from is not
  reviewable, and the matcher needs the domain. `snippet` — the classifier
  reads it. `dismissed` — "the user rejects this suggestion" needs somewhere
  to be recorded, or the same email is offered forever. A CHECK constraint
  refuses a row that is both accepted and dismissed.
- **No Google client libraries.** Gmail is two GETs and the OAuth exchange is
  one POST, so `httpx` (already a dependency) and the standard library cover
  Phase 10. Three extra packages to hold mailbox credentials is the wrong
  trade, and `plan.md` asks to prefer stdlib.
- **`POST /api/inbox/{id}/accept` takes optional `posting_id` and `status`.**
  The matcher refuses to guess between two applications at one company, so an
  unmatched suggestion needs the user to say which posting before it can be
  accepted at all; and the person who read the email is a better classifier
  than the model, so they can override the status. Also
  `POST /api/inbox/{id}/dismiss`, which is the "or rejects" half of the plan's
  sentence and had no route named for it.
- **There is no route that runs a mailbox sync.** Fetching mail is slow,
  network-bound and holds credentials; it belongs to `cli sync-email`, not to
  a dashboard button that makes a page load wait on Google.
- **An unrecognised sender never reaches the model.** `plan.md` says one model
  call per candidate email; an email from a company with no application is not
  a candidate, and classifying it would be money spent to learn nothing.

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
uv run python -m agent_app.cli sync-email --login        # once, then without
```

The dashboard at <http://localhost:5173> is real: `/postings` lists 5,149
postings with working filters, keyboard navigation and status changes that
persist; `/stats` shows the pipeline; `/inbox` is the email review queue, with
the pending count in the nav. `/chat` and `/letters/:id` work once the keys are
in and the corpus is embedded.

**Data as of the last ingest run:** 5,149 postings from 22 companies across all
3 sources. 67 `intern`, 77 `newgrad`, 5,005 `unknown`. Chunked on 2026-09-01:
**34,008 chunks**, 6.6 per posting, none embedded yet — `cli embed` still needs
`VOYAGE_API_KEY`. **297 main tests and 68 exercise tests pass.**

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
- **The classifier's prompt has never met the real model.** Like the agent's
  `SYSTEM_PROMPT`, `inbox/classify.py`'s is a first guess. The thing to watch
  once it runs on a real mailbox is whether confidences spread out or pile up
  at 0.9 — `MIN_CONFIDENCE` and the "`other` is a first-class answer" framing
  are the two dials.
- **`/inbox` has not been seen in a browser.** It serves and the proxy works;
  the Chrome extension would not connect to look at it.
- **`core/agent.py` is Claude's version, and the author plans to replace it.**
  Its `SYSTEM_PROMPT` in particular is a first guess that has never been run
  against the real model.

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
