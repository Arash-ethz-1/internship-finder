# What this project can do

A map of the built system, for a session that has not seen it before.
Written 2026-09-04 against commit `e3c35b0`.

**Read order stays the same:** `plan.md` is the spec, `PROGRESS.md` is the log
and its amendments outrank the spec, `CLAUDE.md` is the working agreement.
This file is neither spec nor log, it is the inventory. Where it disagrees with
`PROGRESS.md`, `PROGRESS.md` is newer by construction.

---

## In one paragraph

A local agentic app over internship postings. It ingests from four job board
APIs into SQLite, chunks and embeds every posting, searches it with hybrid
dense + BM25 retrieval, drives that search from a Claude tool-calling agent,
drafts motivational letters grounded in the author's own project write-ups, and
reads Gmail to suggest status changes for applications already sent. Four
surfaces in a React dashboard, plus a CLI that does everything the dashboard
does and several things it does not.

No vector database, no agent framework, no scraping. Postings come from public
board APIs or are typed in by hand.

---

## The capabilities

### 1. Ingest, `agent_app/ingest/`

* Four board vendors: **Greenhouse, Lever, Ashby, Personio**. Each is a module
  that knows only its own vendor's quirks; `runner.py` orchestrates. Personio
  is the only European one, is XML rather than JSON (its JSON endpoint omits
  the description), and answers 429 rather than 404 for an unknown token.
* **Idempotent.** `body_hash` distinguishes an edited posting from an unchanged
  one, so re-ingesting does not throw embeddings away.
* **Closed postings.** `reconcile_closed` is a set difference against the
  board's whole list, so it needs no extra request. It sets `closed_at`, never
  deletes, and never fires on a failed or empty fetch. Relisted postings reopen.
* **Company discovery** (`discovery.py`): `--from crawl` (Common Crawl),
  `--from llm`, `--from file`. A board token is real only when it answers 200,
  and failures are recorded so a candidate is never re-checked. Four other
  vendors were probed and rejected (Recruitee, SmartRecruiters, Workable, Join),
  each with its reason recorded next to `BOARD_SOURCES` in `db.py`.
* **Manual postings** (`manual.py`): LinkedIn and company-site roles typed in by
  hand. An ordinary row, chunked inline on create and on any body edit, exempt
  from `reconcile_closed`, and the only kind that is editable.
* **Level heuristic** (`normalize.py`): the title matches broadly, the body only
  counts when a phrase asserts the role, because senior postings routinely say
  "do not apply if you are an intern".
* **Chunking** (`ingest/chunks.py` calling `core/chunking.py`) runs as part of
  both `cli ingest` and `cli embed`, so a new posting is keyword-searchable
  immediately.

### 2. Places, `core/locations.py` and `ingest/locations.py`

Resolves a board's raw location prose into city / ISO country / region against
an offline table (499 cities, 78 countries). A lookup table rather than a
geocoder, because a confident wrong geocode is worse than an honest `None`.
Results go in `posting_locations`, a table rather than columns, because
"London; Berlin" is two places. **97.7% of the corpus resolves.**
`cli locations --unresolved` is the worklist; `--rebuild` re-runs everything
after the tables are widened.

### 3. Embeddings, `core/embeddings.py` and `cluster/`

* **Local by default.** `EMBEDDING_PROVIDER=local` runs a multilingual ONNX
  model through `fastembed`: no key, no cost. `voyage` selects the API path.
* Queries embed in about 130 ms locally. Documents run at about 1.7 chunks a
  second, so bulk work goes elsewhere: `cli embed --export` writes JSONL,
  `cluster/embed_chunks.py` turns it into an `.npz` on a GPU box, and
  `cli embed --import` appends the vectors. The database never leaves the
  laptop and `embed_chunks.py` imports nothing from `agent_app`.
* Vectors are one numpy array in `data/vectors.npy`, one row per chunk, guarded
  by a partial unique index on `chunks.vector_row`. Changing provider, model or
  dimension invalidates the file, and the app refuses to mix vector spaces.
* Safe to interrupt and re-run: anything with a vector is skipped.

### 4. Retrieval, `core/retrieval.py` and `core/bm25_index.py`

* Hybrid: cosine `dense_scores` plus `bm25_scores`, combined by reciprocal rank
  in `fuse`. `component_scores` keys are `"dense"` and `"bm25"` and the two sum
  to `score`, which is what makes the trace bar honest rather than decorative.
* `SearchFilters` covers query, company, level, source, remote, **region and
  country** (validated against the real tables), and excludes closed postings by
  default.
* **`data/bm25.npz`** is a precomputed CSR inverted index, 65,688 terms over
  135,934 chunks. It is why a search takes about 2 s instead of 26 s.
  `bm25_scores` itself is untouched and still the definition;
  `tests/test_bm25_index.py` asserts bit-identical agreement.
* Text is fetched only for the k rows about to be returned.

### 5. The agent, `core/agent.py` and `core/tools.py`

`run_agent(message, history, max_iters=12)` is a **generator** yielding
`TextEvent` / `ToolCallEvent` / `ToolResultEvent` / `DoneEvent`. One function
serves both the SSE route and the CLI REPL.

Four tools: **`find_postings`**, **`get_posting`**, **`update_status`**,
**`list_shortlist`**. Every filter is a top-level schema property, because a
nested object was regularly mis-streamed. `find_postings` writes a `found` row
for what it returns, so results persist and are not offered twice, and it
filters on `undecided` (no row, or `found`), because scrolling past a result is
not a decision.

### 6. Letters, `core/letters.py`

Grounded in `profile/` write-ups retrieved from the same chunk store.
**Refuses to draft when there is nothing to ground in**, and that refusal is the
feature. Revision (`/revise`) applies one instruction to an existing draft with
the grounding extracts unchanged, so an edit cannot invent a fact to fill the
gap it opened, and it sends the editor's contents rather than the file. Letters
run on their own model (`LETTER_MODEL`, Sonnet 5), separate from the agent loop.
Both prompts ask for a 21-year-old's voice; em and en dashes are banned in the
prompt and also normalised in code by `plain_dashes`. `ModelBusy` maps a 529 to
a 503 with `Retry-After` rather than a traceback.

### 7. Inbox, `agent_app/inbox/`

Gmail OAuth (loopback plus PKCE, `gmail.readonly` only, `format=metadata` so no
body is ever fetched or stored), matching against postings by sender domain and
subject, then one Claude call per candidate to classify rejection / interview /
offer / other with a confidence.

**It only ever writes suggestions.** Accepting one in `/inbox` is what moves an
application, and `status_history` records which email caused it. An
unrecognised sender never reaches the model. `--include-sent` exists so the
pipeline can be tested from one mailbox; the default keeps `-from:me`.

### 8. Evaluation, `core/evaluate.py`

`recall_at_k` and `run_eval` over `data/eval/queries.jsonl`. That file now
exists: **six labelled queries**, written 2026-09-03, deliberately described in
its own header as a seatbelt rather than a measurement instrument, so good
evidence for "no worse" and weak evidence for any claim of improvement. It
includes both recorded retrieval complaints (`AI internships in Europe`,
`machine learning internship in Zurich`) and a quant-research mirror query, so a
change that wins by blurring ML into quant shows up as a loss.

### 9. Surfaces

**API** (FastAPI, under `/api`): postings list / detail / create / edit /
delete, filters, stats, application status patch / clear / bulk, `POST /chat`
(SSE), letters draft and revise, profile list / read / write / delete, inbox
list / accept / dismiss, and `GET` / `POST /inbox/sync` (a background job
returning 202).

**Frontend** (Vite, React 19, TypeScript, Tailwind v4) at `localhost:5173`, and
it must be `localhost`, not `127.0.0.1`:

| route | what it does |
|---|---|
| `/chat` | the agent, streamed: markdown answers, ordered text and tool blocks, a transcript that survives navigation, a retrieval trace with stacked component bars |
| `/postings` | virtualised grid, collapsible filter rail (filters remembered in localStorage), keyboard status changes 1 to 5, detail panel, add-a-posting dialog |
| `/letters/:id` | draft, edit, revise |
| `/profile` | edit, add and delete the write-ups letters are grounded in; saving re-chunks and embeds inline |
| `/inbox` | the email review queue, filterable by classification and confidence, with a `check mail` button |
| `/stats` | the pipeline |

**CLI** (`uv run python -m agent_app.cli`): `init-db`, `ingest`, `discover`,
`locations`, `companies`, `embed` (plus `--export` and `--import`),
`ingest-profile`, `draft-letter`, `status`, `eval`, `chat`, `sync-email`.

**Task runner**: `python dev.py` (api plus web), `dev.py test`, `dev.py lint`.

### 10. Data model, `db.py`

`postings`, `posting_locations`, `chunks`, `applications`, `status_history`,
`companies`, `email_matches`. Statuses: `found`, `not_relevant`, `interested`,
`applied`, `rejected`, `interviewing`, `offer`, `declined`. **Untriaged is the
absence of a row**, not a status. `not_relevant` (you passed on them) is
deliberately distinct from `rejected` (they passed on you). `ready_to_submit` is
retired. `migrate()` runs on every connection: it adds missing columns, drops
stale indexes, and moves retired statuses with a history entry.

---

## Where it stands, 2026-09-04

```
24,533 postings · 582 companies · 4 sources
135,934 chunks, all 135,934 embedded        <- the Personio backlog is cleared
25,653 parsed places, 97.7% resolved
422 backend tests green · 69 exercise tests green · ruff, tsc, eslint clean
a search takes about 2 s
```

Triage so far: 252 `found`, 30 `not_relevant`, 3 `interested`, 1 `applied`,
1 `rejected`. Two email suggestions are waiting in `/inbox`. Six real letters
are in `data/letters/`. `profile/` holds four real write-ups.

---

## The last changes (session 7, 2026-09-03)

Five commits, newest first:

1. **`e3c35b0` letters get their own model, and a voice that sounds 21.**
   `LETTER_MODEL` was split from `AGENT_MODEL`: a leftover `AGENT_MODEL=haiku`
   had quietly been writing every letter on the cheapest model for weeks.
   Letters now run on Sonnet 5; the chat loop, discovery and the classifier on
   Haiku 4.5. Both prompts ban consultant register, and dashes are stripped in
   code as well as asked for in the prompt. Verified on a real Sonnet 5 draft:
   314 words, zero dashes, nothing invented, and it emitted a `[TODO]` rather
   than guessing a date.
2. **`7017685` the postings grid went from ten seconds to one.**
   `list_postings` 6.89 s to 0.47 s, `stats()` 3.48 s to 0.65 s. Two causes:
   `idx_postings_closed` misled the query planner into a temp B-tree sort
   (replaced by `idx_postings_open_recent`, with the old one dropped in
   `migrate()`), and the grid was selecting `p.*`, moving about 26 MB of posting
   bodies it never renders. Also added `idx_chunks_unembedded`. **This commit is
   also where Phase 11 was added to `plan.md`.**
3. **`0e56d52` the filter rail collapses.** Backslash toggles it, and the closed
   strip still shows the active filter count, because a collapsed rail must not
   hide the fact that the grid is filtered.
4. **`851bff2` profile write-ups can be deleted and named in the page**, and the
   add-posting dialog is genuinely bounded (`max-h-full` resolves against the
   *padded* box, and `dvh` keeps the footer reachable).
5. **`f52d850` three real bugs.** A manual posting was never chunked, so it was
   invisible to both halves of search while the dialog claimed otherwise; the
   agent had no access to the location layer at all, because `region` and
   `country` had been added to the grid's filters and never to retrieval's; and
   the tool schema's nested filter object was regularly mis-streamed. Plus
   `pending_embedding` in `/api/stats`.

---

## In flight: Phase 11, agentic retrieval

The author's complaint: *"the agent is doing the least while searching, all it
does is turn 'find ML research in Zurich' into query plus location, and a normal
retrieval system with a filter box would do that too."* `plan.md` Phase 11 has
five ordered steps: read your own results and search again; fuse several
phrasings; `corpus_stats` so the agent can say "that is the ceiling";
`search_profile` so the query knows who is asking; and use the triage history as
relevance labels.

**`backend/tests/test_agentic_search.py` is untracked and red, 11 failing tests,
on purpose.** It is the spec written ahead of the code, and it pins: `search_many`
with one phrasing equals plain `search`; fusing several phrasings keeps
`component_scores` summing to `score`; a cap on the number of phrasings;
`find_postings` forwarding every phrasing; `corpus_stats` counting exactly what
search can reach and shrinking as you triage; `past_decisions` never returning
`found`; `search_profile` reading only the profile; and a `max_searches` budget
that is a real ceiling and still ends the turn cleanly. `run_agent` does not yet
take `max_searches`.

Run the rest of the suite with `--ignore=tests/test_agentic_search.py` while this
is unfinished.

---

## Things a new session should not re-derive

* **`core/agent.py` is Claude's version and the author intends to rewrite it**,
  in his words *"i will rewrite it tomorrow with fresh soul."* Do not polish it
  unasked. Its `SYSTEM_PROMPT` is a first guess.
* **`backend/exercises/` is a problem book, not the test suite.** Nine functions
  the author reserved to write by hand; all nine are now written. The tests
  there are the regression suite for any rewrite. Never move them into `tests/`,
  and never "fix" a failing one by writing the function.
* **Nothing built since session 4 has been seen in a browser.** `/inbox`,
  `/profile`, the region and country rail groups, the closed-posting chips, the
  add-posting dialog, the revision box and the collapsing rail all compile and
  serve, and the Chrome extension has refused to connect three sessions running.
  This is the largest untested surface in the project.
* **`discover --from crawl` has never been verified live**, because Common Crawl
  has been unreachable.
* **The ML research employers in Zurich (Google, Meta, Apple, the ETH chairs)
  use none of these four ATSes**, so a thin result there is the corpus ceiling
  rather than a ranking bug.
* **PowerShell mangles `git commit -m` with a here-string** containing double
  quotes. Write the message to a file and use `git commit -F`.
* **The project lives under OneDrive**, where `data/` is 200 MB of vectors plus
  one cache file per embedding. Moving it out is the real fix.
