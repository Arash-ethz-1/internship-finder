# Internship agent — build plan

A local agentic app over internship postings. Batch ingest, hybrid retrieval,
an LLM agent with tools, a Streamlit dashboard, and a motivational-letter
drafter grounded in my own project history.

This is a **learning project**. That constraint shapes the whole plan and is
not negotiable.

---

## READ THIS FIRST: the delegation boundary

There are two categories of code in this repo.

### Category A — you implement fully

Ingestion, storage, embedding API plumbing, CLI, dashboard, packaging, CI.
Write these well and completely. I have shipped this kind of code before and
gain nothing from writing it again.

### Category B — you DO NOT implement

Create the module, the imports, the full type-annotated signature, and a
docstring explaining what the function must do. Then:

```python
raise NotImplementedError("Category B — author writes this by hand")
```

**Do not fill in the body. Do not add a working implementation "as a
starting point". Do not implement it in a helper function and call it from
the stub.** If a Category A component needs a Category B function to run,
import it and let it raise. I will fill these in myself.

The Category B list is exact:

| File | Function | Why it's mine |
|---|---|---|
| `core/chunking.py` | `chunk_posting`, `chunk_profile_doc` | Every retrieval number depends on this choice |
| `core/retrieval.py` | `search`, `dense_scores`, `bm25_scores`, `fuse` | The core concept |
| `core/agent.py` | `run_agent` | The agent loop is the thing I am here to learn |
| `core/tools.py` | the four `TOOL_SCHEMAS` descriptions | Tool descriptions are the real interface |
| `core/evaluate.py` | `recall_at_k`, `run_eval` | Where the measurement lives |

Everything else in those same files (dataclasses, constants, the tool
dispatch table wiring, file I/O helpers) is Category A and should be
complete.

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
        chunking.py         # Category B bodies
        retrieval.py        # Category B bodies
        agent.py            # Category B bodies
        tools.py            # Category B descriptions
        evaluate.py         # Category B bodies
        embeddings.py       # Category A
        letters.py          # Category A except the retrieval call
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

### Phase 2 — Ingestion (Category A, implement fully)

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

### Phase 3 — Core stubs (Category B)

Create these files complete except for the listed bodies.

`core/chunking.py`:

```python
@dataclass(frozen=True)
class Chunk:
    text: str
    ordinal: int

def chunk_posting(posting: Posting, max_chars: int = 1200) -> list[Chunk]:
    """Split a posting body into retrievable chunks.

    Category B — author implements.
    """
    raise NotImplementedError("Category B — author writes this by hand")
```

`core/retrieval.py` — same treatment for:

```python
def dense_scores(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray: ...
def bm25_scores(query: str, corpus_tokens: list[list[str]]) -> np.ndarray: ...
def fuse(score_lists: list[np.ndarray], k: int = 60) -> np.ndarray: ...
def search(query: str, filters: SearchFilters, k: int = 10) -> list[SearchHit]: ...
```

`SearchFilters` and `SearchHit` are Category A dataclasses — define them
fully. `SearchHit` must carry `chunk_id`, `posting_id`, `score`, `text`, and
a `component_scores: dict[str, float]` so the dashboard can show the trace.

`core/agent.py`:

```python
def run_agent(
    user_message: str,
    history: list[dict],
    max_iters: int = 12,
) -> AgentResult:
    """Run the tool-use loop until the model returns a final answer.

    Category B — author implements.
    """
    raise NotImplementedError("Category B — author writes this by hand")
```

`AgentResult` is Category A: define it with `text`, `history`, and
`trace: list[ToolCall]` where `ToolCall` records name, input, output, and
duration. The dashboard reads this.

`core/tools.py` — define the four Python functions fully (they are thin
wrappers over `retrieval.search` and `db`), but leave every `description`
field in `TOOL_SCHEMAS` as the literal string `"TODO: author writes this"`:

```
search_postings(query: str, filters: dict) -> list[dict]
get_posting(posting_id: str) -> dict
update_status(posting_id: str, status: str, note: str = "") -> dict
list_shortlist(status: str | None = None) -> list[dict]
```

`update_status` must validate the status against the allowed set and write a
`status_history` row. That validation is Category A.

**Check:** everything imports, `ruff` passes, and calling any Category B
function raises `NotImplementedError` with the expected message. Write a test
asserting exactly that, so a future accidental implementation is caught.

### Phase 4 — Embeddings plumbing (Category A)

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

This is plumbing. Implement it fully. It does not touch chunking or search.

**Check:** `cli embed` produces `vectors.npy` with row count equal to the
chunks table. Second run is a no-op and prints "0 pending".

### Phase 5 — Profile corpus (Category A)

`profile/` holds markdown files, one per project (e.g.
`distributed-attention.md`, `gnn-maze-solver.md`, `pyblio.md`). Add a README
in that folder explaining the expected format and a single example file with
placeholder content.

Ingest them into `chunks` with `profile_doc` set, using `chunk_profile_doc`
(Category B) and the same embedding path.

**Check:** `cli ingest-profile` chunks and embeds the folder; chunks table
shows both kinds.

### Phase 6 — Letter drafting (Category A, except the retrieval call)

`core/letters.py`:

Given a `posting_id`, call `retrieval.search` restricted to profile chunks to
get the top 3 most relevant pieces of my history, then assemble a prompt and
call the model to draft a motivational letter. Write the output to
`data/letters/{posting_id}.md` and set `applications.letter_path`.

The prompt assembly, file writing, and DB update are yours. The retrieval
call is just a call into Category B.

The prompt must instruct the model to ground every claim in the retrieved
chunks and to leave an explicit `[TODO: ...]` marker rather than inventing a
detail it does not have. A letter that fabricates experience is worse than
no letter.

**Check:** `cli draft-letter <posting_id>` writes a file. It will fail until
retrieval is implemented; that is expected and correct.

### Phase 7 — API (Category A, implement fully)

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

`run_agent` is Category B and does not exist yet, so define the streaming
contract as a generator interface the route consumes, and let the import
raise. Do not implement a fake streaming loop to make the endpoint testable.

Search hits crossing the wire must carry `component_scores` intact. The
frontend cannot draw the trace without them.

CORS allows the Vite dev origin. No auth.

**Check:** `/docs` renders every endpoint. `GET /api/postings` and
`/api/stats` return real data. `POST /api/chat` returns a clean 501 with a
message naming the unimplemented function.

### Phase 8 — Frontend (Category A, implement fully)

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

### Phase 9 — CLI and CI (Category A)

`cli.py` with `argparse` subcommands: `ingest`, `ingest-profile`, `embed`,
`chat` (a REPL over `run_agent`), `status`, `draft-letter`, `eval`.

A root `Makefile` or `justfile` with `dev` (both servers), `ingest`, `embed`,
`test`, `lint`.

GitHub Actions: ruff + pytest for backend, tsc + eslint + build for frontend,
on push. No deploy.

**Check:** `--help` documents every subcommand. One command starts both
servers. CI is green.

---

## Non-goals

Do not build these, do not scaffold them, do not leave TODOs for them:

- Automated submission of applications to any job board
- Browser automation of any kind (no Playwright, no Selenium)
- Gmail or any email integration (planned for v2, but not now)
- A vector database, an ANN index, or a reranker
- Authentication, multi-user support, or deployment
- Any agent framework or orchestration library

---

## Working agreement

- Small commits, one per phase, conventional commit messages.
- Stop after each phase and report what you did and what the check produced.
  Do not run ahead through multiple phases.
- If a Category A task appears to require implementing a Category B function
  to be testable, stop and ask rather than implementing it.
- Prefer stdlib. Every added dependency needs a one-line justification in the
  PR description.
- Type-annotate everything. `ruff` config should enable `E`, `F`, `I`, `UP`,
  and `B`.