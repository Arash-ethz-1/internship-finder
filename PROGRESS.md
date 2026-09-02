# Progress

Running state of the project. `plan.md` is the spec (what we intend to build);
this file is the log (what is actually built, what is next, and what we decided
along the way that the spec does not say).

**Last updated:** 2026-09-02, session 5. Every phase in `plan.md` is done.
Embeddings now run locally with no API key, and the bulk run is set up to
happen on the TIK cluster.

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

## Session 5, 2026-09-02: embeddings without a key, and off this machine

**`EMBEDDING_PROVIDER=local` is the new default.** A small multilingual ONNX
model runs through `fastembed`, so `cli embed` needs no key and re-embedding
while tuning chunking costs nothing. `EMBEDDING_PROVIDER=voyage` still selects
the API path, and choosing a provider now also chooses its model and dimension
unless `EMBEDDING_MODEL` / `EMBEDDING_DIM` say otherwise.

**Measured on this laptop (i5-1235U), and this is the number that decided the
rest of the session:**

| | |
|---|---|
| one query | **130 ms** — fine, this stays local |
| documents | **1.7 chunks/s** |
| the corpus, 135,871 chunks | **~22 hours** |

So the bulk embedding goes to the cluster and the queries stay here.
`cli embed --export` writes the pending chunks as JSONL, `cluster/embed_chunks.py`
turns that into an `.npz` wherever the compute is, and `cli embed --import`
appends the vectors and assigns `vector_row`. The database never leaves this
machine and the repository never reaches the cluster; `embed_chunks.py` imports
nothing from `agent_app` and needs only `fastembed` and `numpy`.
`cluster/README.md` has the conda setup, the `sbatch` job and the etiquette
notes; `cluster/job.sh` needs `TODO_USERNAME` replaced in three places.

**Verified end to end on real data**, at 20 chunks: export -> `embed_chunks.py`
-> import -> a hybrid search returning hits with both `dense` and `bm25`
contributions. Re-importing the same file reported `0 imported, 20 already had
a vector`. Those 20 vectors are still in `data/vectors.npy`; the next export
picks up the remaining 135,851.

**The corpus grew since session 4** and nobody wrote it down: **24,516
postings from 575 companies, 135,871 chunks**, not the 5,149/34,008 recorded
below. German is 0.3% of chunks, not the third that was assumed — though the
German ones are `Praktikum` and `Werkstudent` postings, which is exactly what
this search is for, so the model stayed multilingual.

### Search: 26 s -> 2 s

Measured before, per query, on 135,851 candidates:

```
load_candidates    2.0 s     every chunk's text out of SQLite
tokenize          16.0 s     19.7 million tokens, again
bm25_scores        7.8 s     135,851 Counters, again
dense_scores       0.4 s
query embedding    0.2 s
```

Only the last two lines depend on the query. `core/bm25_index.py` (Category A,
new) precomputes the rest into a CSR inverted index in `data/bm25.npz` — 65,688
terms over 135,871 chunks, 109 MB, loads in 0.4 s — and `search` now
(a) loads candidates without their text, (b) scores the keyword half against
the index, (c) fetches text for the k rows it is about to return.

**`bm25_scores` is untouched and still the definition.** It is one of the
author's exercises; the index only had to reproduce it.
`tests/test_bm25_index.py` asserts bit-identical agreement across five queries,
so if the author rewrites `bm25_scores`, that test is what says whether the
fast path still matches.

One deliberate behaviour change: IDF and average document length now come from
the whole corpus rather than the filtered candidate set. That is the standard
choice, and it stops the same query scoring differently because a company
filter happens to be on. With no filters the two are identical, which is what
the test pins.

The index is built by `cli embed`, and rebuilt automatically when its chunk
count or highest chunk id no longer matches the table.

### Found on the way, not fixed

- **`data/` lives inside OneDrive.** `vectors.npy` will be ~200 MB and the
  embed cache is one file per vector. Setting `DATA_DIR` somewhere outside
  OneDrive would save a lot of syncing. The cluster round trip sidesteps the
  cache entirely, which is part of why it is the better path anyway.
- **Query and document embeddings use the same prefix.** `search` calls
  `provider.embed([query])`, and the Protocol has one method, so a provider
  cannot tell a query from a document. Fine for the default model, which wants
  no prefix at all; E5 models get the symmetric `"query: "` on both sides,
  which their authors document but which is a little worse than the
  asymmetric pair. Voyage has had the same wart all along — it sends
  `input_type: "document"` for queries too. Fixing both properly is one extra
  method on the Protocol and one changed line inside `search`. That line is
  the author's.

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

**No embedding key is needed any more** — that was session 5's point.
`ANTHROPIC_API_KEY` is set in `backend/.env`; the agent, letters and the email
classifier use it.

1. **`VOYAGE_API_KEY`** is now optional and only read when
   `EMBEDDING_PROVIDER=voyage`. Superseded by the local provider.
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

# Done on 2026-09-02. Repeat this after a big ingest; see cluster/README.md.
# uv run python -m agent_app.cli embed --export ../data/pending.jsonl
# ... sbatch job.sh pending.jsonl vectors.npz ...
# uv run python -m agent_app.cli embed --import ../data/vectors.npz

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
| 4 | Embeddings plumbing | Claude | ✅ done; local provider + cluster round trip verified 2026-09-02 | |
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

## Session 5 continued: four things found by using it

1. **`not_relevant` is a new status.** "not for me" in the chat result list
   wrote `rejected`, which in this schema means *a company turned you down*.
   Thirty postings had gone `found -> rejected` without an application ever
   being sent, so the pipeline read as thirty rejections and — worse — those
   postings were candidates for the email matcher to attach a real rejection
   letter to. `not_relevant` is excluded from `TRACKED_STATUSES` for exactly
   that reason. The thirty rows were migrated with a `status_history` entry
   recording the correction, so the change is traceable rather than silent.
   `applications.status` has no CHECK constraint, so this was not a migration.

2. **The chat survives navigation.** The transcript lived in `useState` inside
   a route, so opening a posting the agent had just found threw the
   conversation away. It now lives in `state/chatSession.ts`, outside React's
   tree, and so does the agent loop — a turn started before you navigate keeps
   streaming and is simply there when you come back. Not persisted to disk:
   this survives navigation, not a reload. A "new conversation" control had to
   come with it, since the empty state was otherwise unreachable.

3. **The agent's answer is rendered as markdown.** It was arriving as
   `**bold**` and `1.` in a `whitespace-pre-wrap` paragraph. `react-markdown`,
   with every element mapped to this app's own classes.

4. **The inbox can be narrowed and is legible.** `GET /api/inbox` takes
   `classification` and `min_confidence`; the page has both as controls and
   says how many rows they are hiding, because `pending` is the unfiltered
   count. Defaults stay permissive on purpose — a queue that silently hides
   what the classifier read is one you cannot learn to trust. The rows
   themselves were 12px grey throughout with a hairline between them; the
   subject is now at reading size, the classification is a chip in the status
   ramp's colours, and the accept/dismiss controls sit on their own ground.

**Status labels are readable now.** `rejected` and `declined` rendered the
whole chip at 45% opacity, so the one thing you wanted to check at a glance was
the hardest thing on the page to read. The dot still recedes — that is
`plan.md`'s across-the-room signal — but the text sits on a tinted ground at
full strength.

### Retrieval, after using it in anger

Two complaints, two different answers:

- **"ML research internships in Zurich" returned quant and robotics roles.**
  Not a ranking failure: those seven *were* every intern-labelled Zurich
  posting in the database. 199 Zurich postings from 15 companies is the
  ceiling, and the ML-research employers in Zurich — Google, Meta, Apple,
  Microsoft, IBM Research, Disney Research, the ETH chairs — use none of the
  three ATSes, so `plan.md`'s no-scraping rule puts them permanently out of
  reach. `discover --from llm` aimed at Swiss companies is the only lever.
- **"AI internships in Europe" was a real failure.** "AI" is a filler word in
  2026 postings — "we are an AI-friendly company", "AI-first development" —
  so it matches boilerplate everywhere. Measured directly: the query
  `machine learning research internship` returns IMC Amsterdam, Perplexity
  London, Cohere and Point72 at ranks 1-8; `AI internship` returns none of
  them. Also, "Europe" cannot be expressed: `SearchFilters.location` is a
  substring match, so the agent silently dropped it. That is the tool
  descriptions' problem, and they are Category B.

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
   *Superseded 2026-09-02:* the default is now `EMBEDDING_PROVIDER=local`,
   a `fastembed` ONNX model on this machine, needing no key. Voyage is still
   built and still selectable; it is no longer the way in. See amendment 11.
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
11. **Bulk embedding runs off this machine** (decided 2026-09-02). The laptop
    manages 1.7 chunks a second, or ~22 hours for the corpus, so `cli embed`
    grew `--export` and `--import` and the work happens on the ETH TIK
    cluster. Queries stay local at 130 ms. `plan.md`'s Phase 4 says "implement
    one concrete provider"; there are now two, plus a transfer format, because
    the check it asks for — `vectors.npy` with a row per chunk — is not
    reachable in one sitting otherwise.

    The same library runs on both ends on purpose. Tokenizer, pooling and
    normalisation are as much a part of a vector as the weights are, so
    reimplementing the pipeline with `sentence-transformers` on the cluster
    would look fine and quietly rank worse.

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

- **`backend/cluster/`** is not in `plan.md`'s layout, added 2026-09-02. It
  holds `embed_chunks.py`, an `sbatch` script and a README, and it is the one
  place in the repo that is meant to be copied somewhere else and run without
  the rest. `embed_chunks.py` deliberately imports nothing from `agent_app`;
  `tests/test_cluster_export.py` is what stops the two ends of the file format
  drifting apart, including the prefix table they both keep.
- **`core/bm25_index.py`** is not in `plan.md`'s file list, added 2026-09-02.
  `plan.md` puts BM25 in `retrieval.py` and it is still there; this holds only
  the corpus-derived tables that BM25 reads, which are cache rather than
  retrieval logic. Keeping it out of `retrieval.py` also keeps the author's
  Category B file free of a hundred lines of index plumbing.
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
uv run python -m agent_app.cli embed --export ../data/pending.jsonl
uv run python -m agent_app.cli embed --import ../data/vectors.npz
uv run python -m agent_app.cli discover --from file --file names.txt
uv run python -m agent_app.cli status                    # the pipeline
uv run python -m agent_app.cli companies                 # what was verified
uv run python -m agent_app.cli sync-email --login        # once, then without
```

The dashboard at <http://localhost:5173> is real: `/postings` lists all 24,516
postings with working filters, keyboard navigation and status changes that
persist; `/stats` shows the pipeline; `/inbox` is the email review queue, with
the pending count in the nav. `/chat` and `/letters/:id` work once the keys are
in and the corpus is embedded.

**Data as of 2026-09-02:** **24,516 postings from 575 companies** across all 3
sources, **135,871 chunks**, 5.5 per posting, **all embedded**. The cluster run
did 135,851 chunks in 1.8 minutes at 1,254/s on an RTX 2080 Ti — the same work
the laptop wanted 22 hours for. **348 main tests and 69 exercise tests pass**;
ruff clean. A search takes about 2 seconds.

The 5,149-postings-from-22-companies figure recorded on 2026-09-01 was
overtaken by a later ingest run that nobody logged. Anything quoting it is
stale, including the phase-4 cost estimates.

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
