# Progress

Running state of the project. `plan.md` is the spec (what we intend to build);
this file is the log (what is actually built, what is next, and what we decided
along the way that the spec does not say).

**Last updated:** 2026-09-05, session 8: Phase 12, the screen. The agent no
longer just re-searches when a list is wrong -- it removes the rows that are a
different kind of job, and shows them folded away rather than deleting them.
Its check is not met and the reason is the metric; read the session 8 note
before touching it. Earlier the same day: docs, including one temporary note on
letter framing directly below.

---

## TEMPORARY: the letter framing in the docs, 2026-09-05

**This is meant to be reverted, and nothing in the code changed.**

`README.md` and `VIDEO-SCRIPT.md` no longer lead with "drafts motivational
letters". They describe the same feature as working out which of your projects
to mention for a posting, showing the extracts it grounded in, and helping with
phrasing. The reason is the demo video and the public repo: a recruiter who
lands here while holding an application from the author should not read the
headline as "this person generates the letters he sends me".

What was changed, all prose:

* `README.md` opening sentence, the "Tells you what to mention" bullet (was
  "Drafts letters"), the `/profile` gloss, and the two `draft-letter` lines.
* `VIDEO-SCRIPT.md` section 4, retitled "What to mention" and reshot around the
  `grounded in` extracts panel rather than the letter body. (That file is
  untracked; the change lives only on the author's machine.)

What did NOT change, and is why the framing is selective rather than false:
`core/letters.py`, `routes_letters.py`, the `draft-letter` command, the
`/letters/:id` route and `LETTER_MODEL` are all untouched and still say
"letter". Anyone reading the source sees exactly what it does.

**Restore after the video is recorded.** `git log -- README.md` finds this
change; the previous wording is one `git revert` or `git show` away. The
matching `VIDEO-SCRIPT.md` edit is not in git at all -- that file is local only
now, see `.gitignore` -- so revert it by hand.

---

## Session 8, 2026-09-05: Phase 12, the screen

Phase 11 let the agent search again. It still could not **remove a row**: the
list was the fused ranking exactly as retrieval produced it, so a quant trading
role sat in an "ML research internship" list because it genuinely is
quantitative research written in the same words. New `core/screen.py`: one
cheap-model call per search reads the candidates back against the request and
says which are a different kind of job.

**It works, visibly.** "machine learning research internship" with
`level=intern` kept 10 ML roles and folded away 10 -- Virtu, Jump Trading x4,
Tower, WorldQuant, Point72, all quant trading. That is the complaint this phase
was opened for, gone.

**The check as written in `plan.md` fails, and the metric is the wrong one.**

| through the agent, 4 searches | recall@5 | recall@10 | recall@20 |
|---|---|---|---|
| `SCREEN_RESULTS=0` | 0.575 | 0.750 | 0.851 |
| screen on | 0.575 | **0.694** | 0.812 |

Recall went *down*, and a direct measurement says the drop is real rather than
run-to-run noise: putting each labelled query's own relevant postings in front
of the screen, it throws away **3 of 40**. But recall@k counts only postings
that should be there and has no term for postings that should not, so a feature
whose entire job is removing wrong results cannot do anything to that number
except lower it. The eval set has no negative labels, so what the screen buys
is currently unmeasurable. Do not read the table as "the screen is worse".

Two prompt rules were measured, not guessed:

* **Say which filters SQL already applied.** The first version forbade judging
  seniority, claiming level was already filtered -- untrue when the agent
  passes no `level`, and the screen dropped senior roles anyway. Now
  `_applied_filters` names the constraints that really ran, and the prompt says
  everything else is the screen's to judge. Made it coherent in both directions.
* **Match strictness to how specific the request is.** 4/40 -> 3/40. A request
  naming a field ("AI internships in Europe") is asking to see the field; one
  naming a kind of work ("ML research internship") licenses dropping other work.
* **Tried and reverted:** "judge the role, never the employer". Sounds right,
  measured worse -- 3/40 -> 5/40. It made the model argue with itself about
  Jump Trading and drop more of it.

### The bug that made it look like it was not running

Caught by the author within minutes of using it. `MAX_TOKENS` was a flat 1000,
but the screen's reply is one JSON row per *dropped* posting, so its length
scales with how much it throws away. That was fine at the `limit=10` used while
building and truncated at the **default** `limit=30`, where the pool is 60: the
answer came back cut mid-string, failed to parse, and the fail-safe path showed
the unscreened list. From outside it looked exactly like the screen was
switched off, and the log said "did not return JSON" -- true, and the wrong
thing to investigate.

`token_budget(count)` now scales the ceiling with the pool, and `call_model`
logs `stop_reason == "max_tokens"` by name. The lesson is not about tokens: a
component whose failure mode is *doing nothing quietly* needs its failures to
be loud, because nobody can see an absence.

### Nothing the screen removes is gone

The load-bearing property, and the author's explicit requirement.

* A screened-out posting is **not** recorded as `found`, so it stays undecided
  and the next search offers it again.
* It comes back in the same tool result, flagged `screened_out` with a reason,
  and the trace folds it into an "N screened out" disclosure -- the same idiom
  the inbox uses for mail it decided was not about an application.
* **Every** drop is reported, not a sample. A cap here would be the one place
  in this app where a posting vanishes with nothing on screen saying it existed.
* Screened-out rows carry no `excerpt` and no `component_scores`, which is
  precisely what keeps them out of `ResultList` and out of bulk triage.
* Failing safe: no key, a timeout, prose instead of JSON, or an index for a row
  the model was never shown all end with the unscreened list. `SCREEN_RESULTS=0`
  turns it off.

### Worth knowing

* **Not the reranker `plan.md` bans.** It never reorders and never rescores;
  kept rows stay in fused order with `component_scores` untouched. It only
  removes. Flagging it because the non-goal is one word away.
* **Cost:** one Haiku call per search, 2-3 s warm (9 s cold once), ~2k prompt
  tokens for 60 candidates. With `max_searches=4` that is up to four extra
  calls a turn.
* `test_agentic_search.py` now sets `SCREEN_RESULTS=0`: the screen calls the
  same SDK those tests replace wholesale, so with it on it eats replies
  scripted for the agent.
* The eval set still has no negative labels. Twenty of the author's own would
  make this measurable; six of Claude's cannot.

---

## Session 7, 2026-09-03: Phase 11, all five steps

The whole phase in one go. `cli eval` on a six-query smoke set, same labels
each time:

| | recall@5 | recall@10 | recall@20 |
|---|---|---|---|
| `retrieval.search` alone | 0.103 | 0.169 | 0.256 |
| agent, 1 search | 0.369 | 0.458 | 0.575 |
| agent, 4 searches | 0.575 | **0.681** | 0.810 |

The middle row is the honest baseline and the reason `--max-searches` exists.
Comparing the agent against raw `search` credits the loop with filters and
tool use it did not do; comparing 1 search against 4 isolates step 1. That is
+49% relative at k=10 for the loop alone.

**What was built, per bullet in `plan.md`:**

1. **Re-searching.** `run_agent` grew `max_searches` (default 4) and
   `BUDGETED_TOOLS`. Past the budget a `find_postings` call is *refused with
   an explanation*, not dropped -- and the refusal still travels through the
   trace, so a turn that ran out looks different from one that chose to stop.
   The system prompt was rewritten around "a first search is a hypothesis".
2. **Several phrasings, fused.** `retrieval.search_many`, and a `queries`
   array on `find_postings`. `component_scores` still holds exactly `dense`
   and `bm25` summing to `score`, because each phrasing's dense contribution
   sums into one and its keyword contribution into the other -- the wire
   contract with the trace panel is untouched.
3. **`corpus_stats`.** Counts over the same `chunks`-joined shape search uses
   (`retrieval.corpus_sql`, sharing `_filter_clauses`), so the ceiling it
   reports is one search can actually reach.
4. **`search_profile`.** The letter drafter's corpus, finally visible to
   `/chat`.
5. **`past_decisions`.** The 30 `not_relevant` rows as negative labels.
   `found` is excluded and that is the entire point: a search records those in
   bulk at about fifty to one against real decisions.

Plus `run_eval(through_agent=True)` and `cli eval --through-agent
[--max-searches N]`.

### Two things measured that contradict the plan's assumptions

- **Bullet 2 costs no extra model calls at all.** `plan.md` gates steps 1 and
  2 together on cost. Wrong for 2: the model emits every phrasing in one tool
  call. And warm, 4 phrasings cost 1.6x one search (0.283s -> 0.454s), not 4x,
  because the candidate load and vector gather are shared. Only bullet 1 costs
  extra model calls.
- **Three of the five steps never needed the eval set.** 3, 4 and 5 are new
  read-only tools, not ranking changes; their correctness is a unit test. The
  gate was real only for 1 and 2.

### Two live failures found by running it, and fixed

- **The agent diagnosed and then asked instead of acting.** First run of "AI
  internships in Europe": it noticed "some of these don't look like AI-focused
  roles" and asked the author to narrow it down. Correct diagnosis, wrong
  move. A paragraph was added telling it that which words the corpus uses is
  its problem, not the person's. After: two searches, then two `corpus_stats`
  calls to check the ceiling.
- **`corpus_stats` was called with a `query` argument.** The model carries
  `find_postings`' shape straight across. It cost a `TypeError` and a wasted
  round trip, so `query` is now accepted and ignored -- a count is over
  constraints, and there was nothing to do with the text either way.

### Worth knowing

- **The eval set is a smoke test, not an instrument.** Six queries, 40 labels,
  Claude's judgement pooled from several phrasings per intent and checked
  against each posting's real title. The author declined to hand-label
  (no time) and delegated tuning. So it is good evidence for "no worse" and
  weak evidence for any claim of improvement -- and weaker still because the
  same party wrote the labels and made the changes. Overwrite the labels
  freely; the file says so at the top.
- **Running the eval mutates `applications`.** `find_postings` records what it
  returns as `found`, so the eval and demo runs took `found` from 213 to 244.
  Not destructive -- `found` is not a decision and `reset_status` undoes it --
  but `cli eval --through-agent` is not read-only and should not be run
  casually against the real database.
- **`core/agent.py` was edited.** The author had reserved `run_agent` for
  himself; asked to do all five bullets, which is impossible without the loop,
  he confirmed by instruction rather than by answering the question. The
  exercise suite still passes, so his own rewrite has an unchanged regression
  test.

## Session 6, 2026-09-02: the extensions

Nine things the author asked for in one session, in three commits. `plan.md`
lost its Category A/B delegation boundary first, at the author's request --
all nine reserved functions were written, so the rule had nothing left to
protect. Phase 3 is now "Retrieval and agent core" and reads as an ordinary
phase.

### 1. Postings can be closed, and it costs nothing to notice

`last_seen` was written on every ingest run and read by nothing, so a posting
pulled from its board in July still ranked first and could still have a letter
drafted for it. Every board here serves a company's *whole* list in one
response, so whatever is absent from that response has been taken down --
`ingest/runner.reconcile_closed` is that set difference and needs no extra
request.

Three rules it must not break, each with a test:

* **never on a failed fetch.** The caller only reaches it after a successful
  parse.
* **never on an empty one.** A board answering 200 with nothing is far more
  often broken than it is a company that fired everyone.
* **never a delete.** An application, its letter and its history all point at
  the posting, and one you applied to is the row you least want to lose.
  `closed_at` is set; the row stays. Relisted postings reopen.

Hidden from the grid by default, with `include closed` / `closed only` in the
rail. A closed row shows "closed" where its status would be: that you can no
longer apply outranks what you had decided about it.

### 2. A location layer, so "Europe" can be asked

`SearchFilters.location` was a substring match over the board's raw prose, so
`tools.py` literally told the model to give up on "Europe" -- and `Zurich`,
`Zürich`, `CH-Zurich` and `Zurich, Switzerland` were four different places.

`core/locations.py` resolves a raw string into city / ISO country / region
against an offline table (499 cities, 78 countries). A lookup table rather than
a geocoder on purpose: the corpus is a few hundred cities, and a
wrong-but-confident geocode is worse than an honest `None`.
`ingest/locations.py` stores the result in `posting_locations` -- a table, not
columns, because "London; Berlin" is two places and flattening it loses one.

**Measured on the real corpus, and tuned against it rather than guessed:**
85.5% resolved on the first pass, then 93.0%, 93.6%, and **97.7%** after
fixing what the failures actually showed -- "Massachusetts - Boston" word
order, `München` vs `Muenchen`, parenthetical asides, metro-area phrasings,
multi-word US state names. 24,516 postings became 25,653 places.

`cli locations --unresolved` is the worklist for going further: the strings the
parser could not resolve, most common first. `--rebuild` re-runs the whole
corpus after widening the tables.

**"Europe + intern" now returns 173 postings.** It could not be expressed at
all before.

### 3. Personio, the first non-US board

Greenhouse, Lever and Ashby are a US-startup corpus, which is why German was
0.3% of chunks. `Praktikum` and `Werkstudent` postings live on Personio.

Two vendor quirks, both handled inside the module because that is where vendor
knowledge belongs:

* **the feed is XML.** Personio's JSON endpoint (`/search.json`) returns every
  field *except* the description -- verified empty on every board tried. A
  posting with no body cannot be chunked, embedded or searched, so `/xml` it
  is, parsed with stdlib ElementTree. `FEED_IS_XML` tells the runner.
* **an unknown token answers 429, not 404**, with an HTML error page. Left
  alone the polite client retries twice and reports a transient failure, so
  discovery would record a company as `unresolved` and re-check it forever.
  `NOT_FOUND_STATUSES` is how a module says otherwise.

**Probed and deliberately not added**, recorded in `db.py` so nobody re-treads
it: Recruitee (every token 404s), SmartRecruiters (200 with an empty list for
*any* token, so a board cannot be verified and a typo looks alive), Workable
(the widget returns the account but zero jobs for every board tried), Join
(422 on the documented path).

Seven Personio boards verified and ingested: 17 postings, 100% located,
multi-office locations like `München; Köln` parsing into two places.

### 4. `ready_to_submit` is retired

It described a state of the author's intent rather than of the world, and
"interested, not yet sent" already covered it. The one real thing it encoded --
the letter is written, it just needs sending -- is
`letter_path IS NOT NULL AND status = 'interested'`, which is a filter.
`db.migrate()` moves surviving rows and writes a `status_history` entry, the
same rule applied when `not_relevant` arrived. Gone from the ramp, the rail,
the stats order and the keyboard bindings, which are now 1-5.

### 5. `search_postings` is gone

Two search tools with synonymous names is the worst possible case for a model
choosing between them from descriptions alone -- and choosing wrong meant
results were never recorded as `found`, so they were offered again forever.
`find_postings` + `get_posting` cover the workflow. Four tools left.

### 6-9. The app surfaces

* **Manual postings** (`ingest/manual.py`, `POST/PUT/DELETE /api/postings`, a
  dialog on the grid). LinkedIn and company-site applications had nowhere to
  live, so `/stats` understated the pipeline and a reply from those companies
  had no posting to be matched against. A manual posting is an ordinary row --
  chunked, embedded, searched -- except that `source = manual` keeps it out of
  `reconcile_closed`, and it is the only kind editable because it is the only
  kind whose text nobody owns upstream.
* **Letter revision** (`POST /api/letters/{id}/revise`, a message box under the
  draft). Regenerating was the only option and it is the wrong shape: "make it
  shorter" is a change to *this* letter. The grounding extracts go back into
  the prompt unchanged, so a revision cannot invent a fact to fill a gap its
  own edit opened. Sends the editor's contents, not the file, because hand
  edits are real.
* **Profile editing** (`/api/profile`, a `/profile` route). Editing
  `profile/` in a text editor and forgetting `cli ingest-profile` left every
  letter grounded in text that no longer existed, with nothing saying so.
  Saving rewrites the file and re-chunks it in one request. Chunk *and*
  embedded counts are both shown, because chunking is immediate and embedding
  is not.
* **Mailbox sync in the app** (`GET/POST /api/inbox/sync` over a background
  job, a `check mail` button). This reverses the 2026-09-01 decision below,
  and deliberately: that reasoning was right that a *request* must not wait on
  Gmail, and a job with a status endpoint holds no request open. The property
  that mattered is untouched and printed next to the button -- a sync only ever
  writes suggestions.

### The postings grid was taking ten seconds

Reported 2026-09-03 as "loading postings... for minutes". Measured, per page
of 5,000 rows:

| | before | after |
|---|---|---|
| `list_postings` | 6.89s | 0.47s |
| `places_for` | 0.31s | 0.10s |
| `stats()` | 3.48s | 0.65s |

Two causes, one of them mine.

**`idx_postings_closed` was actively harmful.** Added the same day for the
closed-posting filter, and almost every posting is open — so as a lookup it
excludes nothing, but the planner chose it anyway and then sorted 5,000 rows in
a temp B-tree, because *nothing indexed `posted_at`* and that is the default
sort. Replaced by `idx_postings_open_recent (closed_at, posted_at DESC)`,
which serves the filter and the ordering from one structure. `migrate()` drops
the old one: a redundant index still costs every write.

**The grid was selecting `p.*`.** Bodies average 5.2 kB and the grid renders
none of them, so a page pulled roughly 26 MB out of SQLite to discard it.
`GRID_COLUMNS` names what the grid actually shows; the detail panel still
fetches the body one posting at a time.

Also `idx_chunks_unembedded`, a partial index over chunks with no vector, so
the `pending_embedding` count in `/api/stats` stops scanning 136,000 rows.

The lesson worth keeping: an index added for a filter can make an unrelated
sort dramatically worse, and `EXPLAIN QUERY PLAN` says so immediately —
`USE TEMP B-TREE FOR ORDER BY` was the whole diagnosis.

### Verified, not just tested

* real database migrated in place; 24,516 postings located, 97.7% resolved
* `region=europe&level=intern` -> 173 postings, through the Vite proxy
* Personio discovery + ingest end to end, 17 German postings with real bodies
* a manual posting: `Praktikum` -> `level=intern`, `Zürich` -> `CH`, findable
  by country filter, deletable
* **a real letter revision against the real model**: 385 words -> 247 on "make
  it about a third shorter, keep every concrete project detail", every
  specific kept (Cybiront, distributed attention, Research Analytics Services),
  no invented facts, no new TODO markers
* 389 backend tests (25 new), ruff clean, tsc clean, eslint 0 errors, build ok

**Not verified: any of this in a browser.** The Chrome extension would not
connect again, the same as session 4. The pages compile, the routes serve and
the API answers through the Vite proxy, but nobody has looked at `/profile`,
the new rail groups or the revision box with their eyes.

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
notes; `cluster/job.sh` takes the cluster login from `$USER`, so it carries no
username and needs no editing.

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

**Phase 12 is built (session 8). Nothing in `plan.md` is unbuilt.**

The next things, in order of value:

1. **Negative labels in the eval set.** This is now the blocker, not a nicety.
   Phase 12 trades recall for precision and the eval set can only see the half
   it costs. `queries.jsonl` needs a `not_relevant_posting_ids` field and a
   precision number beside `recall_at_k`; until then "did the screen help" is
   an opinion. The author's `not_relevant` triage rows are the natural source.
2. **Look at `/chat` in a browser.** The trace panel now renders three new
   result shapes -- `corpus_stats` counts, `past_decisions` rows, profile
   passages with score bars -- and none of them has been seen. This joins the
   long list below of surfaces that compile, serve, and have never been
   looked at.
3. **Tune the free dials.** `DEFAULT_RRF_K` (60), `BM25_K1`/`BM25_B`
   (1.2/0.75), `FIND_OVERSAMPLE` (6), `DEFAULT_MAX_SEARCHES` (4) and the tool
   descriptions all change with no re-embedding; `cli eval` reads in seconds.
   `DEFAULT_MAX_CHARS` and the embedding model do not -- those invalidate all
   135,934 vectors and need a cluster round trip, which makes them bad
   experiments regardless of who runs them.
4. **Better labels.** The smoke set is six queries of Claude's judgement.
   Twenty of the author's would make the number mean something.

**Superseded:** the Personio embedding backlog below is done -- the chunks
table has 0 unembedded rows.

**The old note, kept for the commands:**

```
cd backend
uv run python -m agent_app.cli embed          # 63 chunks, a minute on the laptop
```

Then the German postings are searchable by meaning as well as keyword.

**After that, in rough order of value:**

1. **Look at it in a browser.** `/profile`, the rail's new region/country
   groups, the closed-postings chips and the revision box under the letter
   have never been seen. Everything compiles and serves; nobody has looked.
2. **More European boards.** Personio is one, and it is the smallest of the
   Greenhouse/Lever/Ashby-sized wins available. `cli discover --from llm
   --query "..." --source personio` aimed at Swiss and German employers is the
   cheap next step; the four vendors probed and rejected are listed in `db.py`
   with the reason each failed, so re-investigating one is a known task rather
   than a fresh one.
3. **`cli locations --unresolved`** lists the 586 location strings the parser
   still cannot resolve, most common first. A few lines added to `_CITY_TABLE`
   plus `cli locations --rebuild` moves the number.
4. **`data/eval/queries.jsonl` still does not exist.** Unchanged from session
   5, and now more valuable: chunking, fusion *and* the place filters are all
   things you can only tune against a number.

---

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
| — | Places, closed postings, Personio | Claude | ✅ done 2026-09-02 | `22470fc` |
| — | Manual postings, revision, profile, sync | Claude | ✅ done 2026-09-02 | `5924c86` |
| — | The dashboard for all of it | Claude | ✅ done 2026-09-02 | `e16ad34` |
| 11 | Make the retrieval agentic | Claude | ✅ done 2026-09-03, all five steps | |
| 12 | Screen the result list | Claude | ⚠️ built 2026-09-05, check not met | recall metric cannot see it |

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

5. **The grid's status filter is a checkbox list.** It was one radio button
   with `tracked` — "anything but untriaged" — as one of the options, so
   "everything I have touched except the ones I passed on", which is the view
   you actually work in, could not be expressed. `GET /api/postings` now takes
   a repeatable `status` param, OR-ed; `PostingFilters.status` became
   `statuses: tuple[str, ...]`. Nothing ticked means no constraint, which is
   deliberately not the same as ticking everything: an untriaged posting has
   no status to be in a list. `tracked` stays in the API and is gone from the
   rail — it was a group masquerading as a member. Three presets say the same
   thing without the category error, and the grid opens on `on my list`.

6. **The inbox was folding results away silently.** With the filter on
   "everything" you saw one row, because the eighteen the classifier judged
   not to be about an application have no `suggested_status` and live behind a
   collapsed `<details>`. They were always there; the page now counts them
   next to the filter and opens the section when it is the only thing there is.

7. **A busy model is a 503, not a 500 with a traceback.** `POST /api/letters/`
   hit Anthropic's `529 Overloaded` and the exception went straight through
   uvicorn. `call_model` now raises `ModelBusy` for 408/409/429/5xx and
   connection failures, separate from `LetterError`, because the two want
   opposite answers from the person: one says fix something, the other says
   press the button again. The route maps it to 503 with `Retry-After`, and
   the SDK's retry budget went from its default 2 to 4 — drafting a letter is
   one deliberate click, worth waiting through a busy minute for.
   `inbox/classify.py` already caught broadly, so a sync degrades on its own.

8. **The rail's filters are remembered between visits.** Unchecking `found`
   and finding it checked again after a reload is the app disagreeing with you
   about what you want to look at. `state/postingFilters.ts` keeps them in
   `localStorage` — status, level, source, company, location, remote — and
   deliberately not the free-text box, which is a thing you are doing rather
   than a standing decision. Every remembered key is written, `null` where
   unset, so "clear filters" survives a reload instead of reverting to the
   default. A status saved by an older build that no longer exists is dropped
   on load rather than sent to the API as a 422.

9. **`found` no longer takes a posting out of circulation.** `find_postings`
   filtered on `untriaged`, which is *no application row at all* — and it
   writes a `found` row for everything it returns, so a result you scrolled
   past was never offered again. Walking past a result is not a decision. A
   second pseudo-status, `undecided` (no row, or `found`), is what it filters
   on now; 149 postings and 677 chunks came back into circulation.

10. **A posting can be cleared back to no status.** `DELETE
    /api/applications/{id}` deletes the row rather than moving it to a
    "cleared" status, because untriaged is the absence of a row and that is
    what a search looks for. The `status_history` entry stays, with
    `to_status = 'untriaged'`, so changing your mind is visible instead of
    leaving a gap. Clearing something already clear is a success. The button
    is in the detail panel next to the status keys.

11. **Every stage has its own hue.** `interested` and `applied` were two
    shades of the same grey, so the two halves of the pipeline — considering
    something, and having sent it — looked identical. Now indigo and blue,
    with `rejected` a muted red rather than the same grey as `interested`, and
    lifted variants for a dark ground. This overrides `plan.md`'s "`interested`
    neutral, `applied` blue-grey" at the author's request; the ramp is still
    muted and still does not compete with the signal accent.

12. **The agent's words and its tool calls render in the order they happened.**
    `reduceEvents` collected all text into one blob printed under the results,
    so "let me check the postings" appeared *after* the postings. It now emits
    an ordered list of blocks, one per contiguous run of text and one per tool
    call.

### The email pipeline has now run end to end

Verified 2026-09-02 against the real mailbox. A test rejection naming
Databricks — the one `applied` posting — was matched to
`greenhouse:8732364002`, classified `rejection` at **0.97**, and stored as a
suggestion. `applications` was untouched, which is the property that matters.

Two things the test surfaced:

- **`QUERY_EXCLUSIONS` carries `-from:me`**, so a self-sent test message is
  invisible to `cli sync-email`. That is right in production — your own
  application mail should not be classified — and it means a test has to come
  from elsewhere, or override the query in a one-off script. The shipped code
  was not changed for the test.
- **The match ran on the subject, not the sender domain.** `gmail.com` is on
  the ATS/consumer list and is discarded, so signal 1 never fired. A real
  Databricks rejection would match on the domain instead.

`cli sync-email --include-sent` was added afterwards so this is repeatable
without a second mailbox. It lifts `-from:me` and nothing else; the default is
unchanged, because your own application to a company must never be read as
that company's answer to it.

Three test messages have now gone through, all matched, all classified
correctly:

| | | |
|---|---|---|
| ANYbotics, interview invitation | `interview` | **0.98** -> `interviewing` |
| Databricks, rejection | `rejection` | **0.96** -> `rejected` |
| Notion, rejection | `rejection` | **0.82** -> `rejected` |

The confidences do spread — 0.82 to 0.98 — rather than piling up at one value,
which was the open question. Three samples on unambiguous text is still not
evidence about the hard cases. `applications` was untouched by all three.

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
  *Superseded 2026-09-02 (session 6):* there is now `POST /api/inbox/sync`,
  which starts a background job and returns 202. The objection was to a
  request waiting on Gmail, and a job with a status endpoint does not. `cli
  sync-email` still works and is still the only way to do the OAuth login.
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
uv run python -m agent_app.cli locations                 # parse places
uv run python -m agent_app.cli locations --unresolved    # the worklist
uv run python -m agent_app.cli status                    # the pipeline
uv run python -m agent_app.cli companies                 # what was verified
uv run python -m agent_app.cli sync-email --login        # once, then without
```

The dashboard at <http://localhost:5173> is real: `/postings` lists the
postings with working filters (now including region and country), keyboard
navigation and status changes that persist, and a dialog for adding a posting
by hand; `/profile` edits the write-ups letters are grounded in; `/stats` shows
the pipeline; `/inbox` is the email review queue with a `check mail` button;
`/letters/:id` drafts and revises. `/chat` works once the keys are in.

**Data as of 2026-09-02, session 6:** **24,533 postings from 582 companies**
across 4 sources, **135,934 chunks**, and **25,653 parsed places on 24,516
postings, 97.7% resolved to a country or region**. 6,647 postings in Europe;
"Europe + intern" is 173. Everything is embedded except the 17 Personio
postings ingested at the end of the session (63 chunks). **389 main tests and
69 exercise tests pass**; ruff, tsc and eslint clean. A search takes about
2 seconds.

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
- **Four ATS vendors were probed and rejected on 2026-09-02** — Recruitee,
  SmartRecruiters, Workable, Join. Each failed differently and each is
  recorded in `db.py` next to `BOARD_SOURCES`. Any of them would widen the
  European corpus considerably if the real endpoint were found.
- **Personio publishes no company display name**, so a Personio company is
  named by whatever the caller already had rather than by the board. Every
  other source takes the name from the board's own response, which is the rule
  `plan.md` asks for. Harmless today because names come from the discovery
  input; worth knowing before trusting `companies.name` for Personio.
- **The classifier's prompt has never met the real model.** Like the agent's
  `SYSTEM_PROMPT`, `inbox/classify.py`'s is a first guess. The thing to watch
  once it runs on a real mailbox is whether confidences spread out or pile up
  at 0.9 — `MIN_CONFIDENCE` and the "`other` is a first-class answer" framing
  are the two dials.
- **Nothing built in sessions 4-6 has been seen in a browser.** `/inbox`,
  `/profile`, the rail's region and country groups, the closed-posting chips,
  the add-a-posting dialog and the letter revision box all serve and the Vite
  proxy is verified, but the Chrome extension would not connect in either
  session. This is the largest untested surface in the project.
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
