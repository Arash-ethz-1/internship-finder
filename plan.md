# Internship agent — build plan

A local agentic app over internship postings. Batch ingest, hybrid retrieval,
an LLM agent with tools, a Streamlit dashboard, and a motivational-letter
drafter grounded in my own project history.

This is a **learning project**. That constraint shapes the whole plan and is
not negotiable.

---

## Stack

Backend:

- Python 3.11+, `uv` for dependency management
- SQLite via stdlib `sqlite3` (no ORM)
- `numpy` for vectors, stored as a single `.npy` file alongside the DB
- `fastapi` + `uvicorn` for the API
- `pytest` for tests, `ruff` for lint and format
- Anthropic API for the agent, any embedding API for vectors (make the
  embedding provider swappable behind one interface)

Frontend:

- Vite + React 19 + TypeScript
- Tailwind CSS v4
- Radix UI primitives for behaviour only (dialog, dropdown, tooltip,
  popover). Style them from the tokens below. Do not install shadcn/ui and
  ship its default styling; the whole point is that this does not look like
  every other dashboard.
- TanStack Query for server state
- TanStack Table for the postings grid (headless, we style it)
- No chart library in v1. The only charts needed are horizontal score bars,
  which are `div`s with a width percentage.

No vector database. No LangChain, LlamaIndex, or any agent framework.
Brute-force cosine over a numpy array is correct at this corpus size and
using a framework would defeat the purpose of the project.

---

## Design direction

This is a **screener**, not a CRM. It is a dense, keyboard-driven decision
tool that one person uses to triage a few hundred postings quickly. Design it
like an operations console: information-first, quiet, fast. Every pixel spent
on decoration is a pixel not spent on rows.

Explicitly avoid the default AI-dashboard look: rounded card grids on a slate
background with a purple accent, a stat-tile row across the top, and a
gradient hero. Also avoid the cream-background-plus-serif look. Both are
defaults rather than choices.

**Palette.** Neutral ground, one signal accent, and status colours that carry
real meaning rather than decorating.

```
--ink        #14161B   text, primary surfaces in dark mode
--graphite   #232630   borders and dividers in dark mode
--paper      #FBFBF9   page background in light mode
--rule       #E6E5E1   hairlines in light mode
--signal     #2F6F5E   the single accent: action required, active state
```

Status colours are a separate, muted ramp and are the only other colour on
the page: `interested` neutral, `ready_to_submit` signal, `applied` blue-grey,
`interviewing` amber, `offer` green, `rejected` faded to 45% opacity. A row's
status should be readable from across the room without reading the label.

**Type.** Geist Sans for UI, Geist Mono for all data: scores, IDs, dates,
counts, company names in the grid. The sans/mono split is not decorative, it
encodes what is prose and what is data. Set a tight type scale, 12/13/14/16/20,
and use weight rather than size for hierarchy. No Inter.

**Layout.** Full-bleed, no centred max-width container, no cards around the
grid. A persistent left rail for filters, a dense table filling the rest, and
a right panel that slides in for detail. Row height 36px. Hairline dividers,
`border-radius` no greater than 4px anywhere.

**Signature element.** The retrieval trace. This is the one place to spend
effort and boldness. When the agent runs a search, show each hit as a row with
its score decomposed into a stacked horizontal bar: dense contribution and
BM25 contribution in different treatments, with the fused rank beside it.
Nobody else's job tracker shows you why a result ranked where it did. This is
the thing an interviewer will remember, and it is the visual proof that the
retrieval layer is real rather than a wrapper around an API.

**Motion.** One orchestrated moment only: tool calls streaming into the trace
panel one at a time as the agent loop runs, each row fading in as its result
arrives. Nothing else animates. Respect `prefers-reduced-motion`.

Ship the quality floor without announcing it: keyboard navigation through the
grid (`j`/`k`, `Enter` to open, `1`-`6` to set status), visible focus rings,
responsive down to a tablet. Phone layout is not required.

---

## Data model

```
postings
  id TEXT PRIMARY KEY          -- "{source}:{external_id}"
  source TEXT                  -- greenhouse | lever | ashby
  company TEXT
  title TEXT
  location TEXT
  remote INTEGER
  url TEXT
  body TEXT                    -- plain text, HTML stripped
  posted_at TEXT
  deadline TEXT                -- nullable
  level TEXT                   -- intern | newgrad | unknown
  first_seen TEXT
  last_seen TEXT

chunks
  id INTEGER PRIMARY KEY
  posting_id TEXT              -- FK, nullable
  profile_doc TEXT             -- nullable; set for profile chunks
  ordinal INTEGER
  text TEXT
  vector_row INTEGER           -- row index into vectors.npy

applications
  posting_id TEXT PRIMARY KEY
  status TEXT                  -- interested | ready_to_submit | applied
                               -- | rejected | interviewing | offer | declined
  note TEXT
  letter_path TEXT             -- nullable
  updated_at TEXT

status_history
  id INTEGER PRIMARY KEY
  posting_id TEXT
  from_status TEXT
  to_status TEXT
  note TEXT
  changed_at TEXT
```

Exactly one of `posting_id` / `profile_doc` is set on a chunk. Enforce with a
CHECK constraint.

Vectors live in `data/vectors.npy`, shape `(n_chunks, dim)`, float32. Chunk
row `i` maps to `vectors[chunks.vector_row]`. Provide a `rebuild_vectors()`
that compacts the array and reassigns `vector_row` after deletions.

---

## Phases

Each phase must end in a runnable, verifiable state. Do not start a phase
before the previous one passes its check.

### Phase 1 — Scaffold

Repo layout:

```
internship-agent/
  backend/
    pyproject.toml
    companies.toml
    src/agent_app/
      __init__.py
      config.py
      db.py
      ingest/
        __init__.py
        greenhouse.py
        lever.py
        ashby.py
        normalize.py
      core/
        chunking.py
        retrieval.py
        agent.py
        tools.py
        evaluate.py
        embeddings.py
        letters.py
      api/
        __init__.py
        main.py
        routes_postings.py
        routes_chat.py
        routes_letters.py
        schemas.py
      cli.py
    tests/
  frontend/
    package.json
    vite.config.ts
    index.html
    src/
      main.tsx
      App.tsx
      api/client.ts
      components/
      routes/
      styles/tokens.css
  profile/                  # my project write-ups, markdown, gitignored
  data/                     # sqlite + npy, gitignored
  README.md
```

`.env` for API keys via `python-dotenv`. `config.py` reads it and fails loudly
with a clear message if a key is missing.

**Check:** `uv run ruff check .` and `uv run pytest` both pass on an empty
test suite.

### Phase 2 — Ingestion

Public JSON endpoints, no scraping, no HTML parsing beyond stripping tags
from description fields:

- Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
- Lever: `https://api.lever.co/v0/postings/{company}?mode=json`
- Ashby: `https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true`

`companies.toml` lists companies grouped by source, with the board token and
a display name. Seed it with 5 real examples per source and a comment showing
how to find a company's token.

`normalize.py` maps each source's response onto the `postings` schema.
Greenhouse content is HTML-escaped HTML: unescape, then strip tags. Infer
`level` with a keyword heuristic over title and body (`intern`, `internship`,
`praktikum`, `working student`, `thesis`) and default to `unknown` rather than
guessing.

Ingestion is idempotent: upsert on `id`, update `last_seen` every run, never
duplicate. Postings that disappear from a board are kept, not deleted.

Be polite: 1 request/second, a real User-Agent, retry twice on 5xx with
backoff, and skip a company with a logged warning rather than crashing the
run if its board 404s.

**Check:** `uv run python -m agent_app.cli ingest` populates SQLite from at
least 3 companies across 2 sources. Running it twice does not change the row
count. Print a summary table of company, fetched, new, updated.

### Phase 2.5 — Company discovery

Fifteen hand-written companies is a fixture, not a job search. This phase
replaces `companies.toml` as the source of truth with a `companies` table that
grows.

```
companies
  source TEXT                  -- greenhouse | lever | ashby
  token TEXT
  name TEXT                    -- from the board itself, not guessed
  status TEXT                  -- verified | dead | unresolved
  job_count INTEGER
  api_host TEXT                -- which host answered (Lever has US and EU)
  discovered_by TEXT           -- seed | crawl | llm | file
  first_verified TEXT
  last_checked TEXT
  PRIMARY KEY (source, token)
```

The rule that makes this worth building: **the model proposes, HTTP disposes.**
No token is ever trusted because a model said it; a token is real when the
board returns 200. Equally important, failures are recorded as
`dead`/`unresolved` so the same candidate is never checked twice.

Three candidate sources, all feeding the same verifier:

- **`--from crawl`** — the Common Crawl URL index is a de-facto directory. One
  query for `boards.greenhouse.io/*` yields ~1,600 distinct tokens and
  `jobs.ashbyhq.com/*` ~1,900. Needs no API key. Lever is only partly covered
  because `jobs.lever.co` is disallowed in robots.txt, though
  `jobs.eu.lever.co` is indexed.
- **`--from llm`** — a single Claude call for company *names* matching a
  description ("robotics companies in Switzerland that hire interns"). This is
  the only source that can target a niche. Ask for names, never for tokens:
  names are reliable, and a model's knowledge of which ATS a company uses goes
  stale as companies migrate.
- **`--from file`** — one company name per line. Keeps the whole pipeline
  usable with no API key at all.

Names arrive as prose, so derive token candidates mechanically
(`"Match Group"` → `matchgroup`, `match-group`, `match`) and try each against
all three boards. Take the display name from the board's own response
(`GET /v1/boards/{token}` returns `{"name": "Stripe"}`) rather than from the
model.

Politeness rules from Phase 2 apply unchanged: 1 request/second, real
User-Agent, retries, and a 404 is data rather than an error.

**Check:** `cli discover --from crawl --source ashby --limit 50` adds verified
companies and records the failures. Running it again checks zero of the same
candidates. `cli ingest` reads the table and picks up the new companies.

### Phase 3 — Retrieval and agent core

The heart of the project. Four modules, each with a small, well-tested
surface.

`core/chunking.py` — how a posting is split is what every later retrieval
number rests on, so keep it explicit and cheap to re-tune:

```python
@dataclass(frozen=True)
class Chunk:
    text: str
    ordinal: int

def chunk_posting(posting: Posting, max_chars: int = 1200) -> list[Chunk]: ...
def chunk_profile_doc(text: str, max_chars: int = 1200) -> list[Chunk]: ...
```

Split on structure first — headings, blank lines, list boundaries — and fall
back to a hard character cut only when a single block exceeds `max_chars`. A
chunk that ends mid-sentence retrieves worse than one that ends at a heading.

`core/retrieval.py`:

```python
def dense_scores(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray: ...
def bm25_scores(query: str, corpus_tokens: list[list[str]]) -> np.ndarray: ...
def fuse(score_lists: list[np.ndarray], k: int = 60) -> np.ndarray: ...
def search(query: str, filters: SearchFilters, k: int = 10) -> list[SearchHit]: ...
```

Dense and BM25 fail in different directions. Embeddings compare meaning, so
they find "PyTorch" for a query about deep learning frameworks and can miss an
exact term; BM25 nails exact terms and is blind to synonyms. Fuse the two
rankings with reciprocal rank fusion, and keep the two contributions separate
all the way out to the API: `SearchHit` carries `chunk_id`, `posting_id`,
`score`, `text`, and `component_scores: dict[str, float]`, which is what the
dashboard draws the trace from. `SearchFilters` covers company, level,
location, remote and status.

`core/agent.py`:

```python
def run_agent(
    user_message: str,
    history: list[dict],
    max_iters: int = 12,
) -> AgentResult: ...
```

The tool-use loop, run until the model returns a final answer or `max_iters`
is reached. `AgentResult` has `text`, `history`, and `trace: list[ToolCall]`,
where `ToolCall` records name, input, output and duration — the dashboard
reads that trace.

`core/tools.py` — four thin wrappers over `retrieval.search` and `db`:

```
search_postings(query: str, filters: dict) -> list[dict]
get_posting(posting_id: str) -> dict
update_status(posting_id: str, status: str, note: str = "") -> dict
list_shortlist(status: str | None = None) -> list[dict]
```

Write the `TOOL_SCHEMAS` descriptions with as much care as the code. They are
the entire interface the model has to this app: a filter the description does
not explain is a filter the model never uses, and one it explains badly is a
filter the model misuses silently.

`update_status` validates the status against the allowed set and writes a
`status_history` row.

`core/evaluate.py` — where the measurement lives:

```python
def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float: ...
def run_eval(queries_path: Path, k: int = 10) -> EvalReport: ...
```

The labelled set is `data/eval/queries.jsonl`, one
`{"query": ..., "relevant_posting_ids": [...]}` per line, written by hand.
Without it, every choice about chunk size, fusion and the dense/BM25 balance
is an argument rather than a measurement.

**Check:** unit tests over the pure functions on small synthetic inputs —
`dense_scores` against a hand-computed cosine, `bm25_scores` against a known
ranking, `fuse` against a worked RRF example — and `search` returning hits
whose `component_scores` sum to `score`. `ruff` passes.

### Phase 4 — Embeddings plumbing

`core/embeddings.py`:

```python
class EmbeddingProvider(Protocol):
    dim: int
    def embed(self, texts: list[str]) -> np.ndarray: ...
```

Implement one concrete provider. Handle batching (64 per request), retry with
exponential backoff on rate limits, and an on-disk cache keyed by
`sha256(model + text)` in `data/embed_cache/` so re-runs during development
cost nothing.

`embed_all_pending()` finds chunks with no `vector_row`, embeds them, appends
to `vectors.npy`, and updates the rows in one transaction.

This is plumbing, and it is independent of chunking and search — it embeds
whatever text the chunks table happens to hold.

**Check:** `cli embed` produces `vectors.npy` with row count equal to the
chunks table. Second run is a no-op and prints "0 pending".

### Phase 5 — Profile corpus

`profile/` holds markdown files, one per project (e.g.
`distributed-attention.md`, `gnn-maze-solver.md`, `pyblio.md`). Add a README
in that folder explaining the expected format and a single example file with
placeholder content.

Ingest them into `chunks` with `profile_doc` set, using `chunk_profile_doc`
and the same embedding path.

**Check:** `cli ingest-profile` chunks and embeds the folder; chunks table
shows both kinds.

### Phase 6 — Letter drafting

`core/letters.py`:

Given a `posting_id`, call `retrieval.search` restricted to profile chunks to
get the top 3 most relevant pieces of my history, then assemble a prompt and
call the model to draft a motivational letter. Write the output to
`data/letters/{posting_id}.md` and set `applications.letter_path`.

The prompt must instruct the model to ground every claim in the retrieved
chunks and to leave an explicit `[TODO: ...]` marker rather than inventing a
detail it does not have. A letter that fabricates experience is worse than
no letter.

**Check:** `cli draft-letter <posting_id>` writes a file, and every claim in
it traces back to one of the retrieved chunks.

### Phase 7 — API

FastAPI over the existing `core/` and `db.py`. Pydantic models in
`schemas.py`. No business logic in routes; they call into `core/` and
serialise.

```
GET   /api/postings          list + filter + sort, paginated
GET   /api/postings/{id}     full posting + application state + history
PATCH /api/applications/{id} set status, note
POST  /api/letters/{id}      draft a letter, returns text + retrieved chunks
GET   /api/stats             counts by status, company, source, recency
POST  /api/chat              agent turn, streams SSE
```

The chat endpoint is the important one. It must **stream**, not block for
fifteen seconds and return a blob. Emit Server-Sent Events as the loop runs:

```
event: tool_call    data: {"name": "...", "input": {...}}
event: tool_result  data: {"name": "...", "hits": [...], "ms": 142}
event: text         data: {"delta": "..."}
event: done         data: {"iters": 4}
```

Make `run_agent` a generator that yields these events, so one function serves
both the SSE route and the CLI REPL and neither carries a loop of its own.

Search hits crossing the wire must carry `component_scores` intact. The
frontend cannot draw the trace without them.

CORS allows the Vite dev origin. No auth.

**Check:** `/docs` renders every endpoint. `GET /api/postings` and
`/api/stats` return real data. `POST /api/chat` streams a whole agent turn,
with tool calls arriving as they are issued rather than all at the end.

### Phase 8 — Frontend

Follow the design direction section exactly. Derive every colour and type
decision from those tokens; do not introduce new ones.

Four routes:

1. **`/postings`** — the primary view and the one that must feel fast.
   TanStack Table, virtualised, 36px rows. Left rail filters: company, level,
   location, remote, status, free text. Inline status cycling without a modal.
   Keyboard nav per the design direction. Clicking a row opens the detail
   panel on the right rather than navigating away.
2. **`/chat`** — the agent. Consume the SSE stream and render the trace live:
   each tool call appears as it is issued, then fills in with its result. For
   `search_postings` results, render the score decomposition bars. This is the
   signature element; build it first within this phase, not last.
3. **`/letters/{id}`** — the draft alongside the profile chunks it was
   grounded in, with each chunk's retrieval score. Editable textarea, copy
   button, regenerate.
4. **`/stats`** — the pipeline: counts by status shown as a single horizontal
   stacked bar, plus a compact table by company. No pie charts.

Empty and error states are written, not defaults: an empty postings table
says what to run to populate it. An error names what failed and what to do.

`api/client.ts` is the only place that knows about URLs. Every response type
mirrors a Pydantic schema; generate or hand-write the types, but keep them in
one file.

**Check:** `npm run dev` with the API running shows real postings, filters
and keyboard nav work, status changes persist through a reload, and `/chat`
displays a clear error state rather than a blank screen.

### Phase 9 — CLI and CI

`cli.py` with `argparse` subcommands: `ingest`, `ingest-profile`, `embed`,
`chat` (a REPL over `run_agent`), `status`, `draft-letter`, `eval`.

A root `Makefile` or `justfile` with `dev` (both servers), `ingest`, `embed`,
`test`, `lint`.

GitHub Actions: ruff + pytest for backend, tsc + eslint + build for frontend,
on push. No deploy.

**Check:** `--help` documents every subcommand. One command starts both
servers. CI is green.

### Phase 10 — Application tracking from email

Previously a v2 non-goal, pulled into scope. The pipeline already records
status by hand; this closes the loop by reading the replies.

Gmail API, read-only scope (`gmail.readonly`), OAuth device flow with the
refresh token stored in `data/` and gitignored. No other mailbox provider, no
IMAP passwords, and nothing is ever sent.

```
email_matches
  id INTEGER PRIMARY KEY
  message_id TEXT UNIQUE       -- Gmail's id; makes re-runs idempotent
  posting_id TEXT              -- nullable until matched
  company_guess TEXT
  received_at TEXT
  subject TEXT
  classification TEXT          -- rejection | interview | offer | other
  confidence REAL
  suggested_status TEXT
  applied INTEGER DEFAULT 0    -- has the user accepted this suggestion
  created_at TEXT
```

Three steps, each fallible and each therefore separate:

1. **Fetch.** Messages newer than the earliest `applications.updated_at`,
   subject and snippet only. Never the full body unless matched — no reason to
   pull an entire inbox into a local database.
2. **Match to a posting.** Companies almost never quote a posting id, so this
   is fuzzy: sender domain against the company's board URL, then company name
   against the subject. An unmatched email is stored with `posting_id NULL`
   rather than guessed at.
3. **Classify.** One model call per candidate email returning
   `rejection | interview | offer | other` plus a confidence. Cheap, and the
   only step that needs judgement.

**Nothing is applied automatically.** The classifier produces a *suggestion*
that the user accepts or rejects in the dashboard, and accepting is what writes
the `applications` row and its `status_history` entry with a note naming the
email. A wrongly auto-applied `rejected` is worse than no automation at all:
you stop checking a company that actually wanted to interview you. The
`status_history` note records that the change came from an email, so an
automated mistake is always traceable.

New route `GET /api/inbox` lists pending suggestions; `POST /api/inbox/{id}/accept`
applies one. A fourth dashboard surface shows them as a review queue.

**Check:** `cli sync-email` fetches, matches and classifies without changing a
single `applications` row. Suggestions appear in the dashboard, and accepting
one moves the status and writes history naming the message.

---

## Non-goals

Do not build these, do not scaffold them, do not leave TODOs for them:

- Automated submission of applications to any job board
- Browser automation of any kind (no Playwright, no Selenium)
- A vector database, an ANN index, or a reranker
- Authentication, multi-user support, or deployment
- Any agent framework or orchestration library

---

## Working agreement

- Small commits, one per phase, conventional commit messages.
- Stop after each phase and report what you did and what the check produced.
  Do not run ahead through multiple phases.
- Prefer stdlib. Every added dependency needs a one-line justification in the
  PR description.
- Type-annotate everything. `ruff` config should enable `E`, `F`, `I`, `UP`,
  and `B`.