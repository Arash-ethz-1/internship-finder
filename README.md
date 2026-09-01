# Internship agent

A local agentic app over internship postings: batch ingest from public job-board
APIs, hybrid retrieval, an LLM agent with tools, a React dashboard, and a
motivational-letter drafter grounded in my own project history.

This is a learning project. [`plan.md`](plan.md) is the spec and takes
precedence over this file.

## Delegation boundary

Nine functions — chunking, retrieval, the agent loop, the tool descriptions and
evaluation — were reserved to be written by hand rather than delegated, because
they are the parts worth understanding. See the Category B table in `plan.md`.

All nine are now written and the app runs end to end. The problem statements
survive as a problem book, and their tests as a regression suite for rewriting
any of them:

```bash
cd backend
uv run python check.py          # the scoreboard: 9/9, 68 tests
uv run python check.py 6 -v     # one problem, with its failures
uv run python try_chunking.py   # chunking output on a real posting
```

These tests live outside the main suite, so `pytest` and CI stay green while a
function is being rewritten.

## Setup

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and Node 20+.

```bash
cd backend && uv sync --extra dev     # backend deps
cd ../frontend && npm install         # frontend deps
cp backend/.env.example backend/.env  # then fill in the keys
```

Neither API key is needed to ingest postings or browse them. They are required
only where they are used, and the error names the missing variable.

| Variable | Needed for | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | the agent, letter drafting, email classification | <https://console.anthropic.com/settings/keys> |
| `VOYAGE_API_KEY` | embeddings | <https://dashboard.voyageai.com/api-keys> |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | `sync-email` only | <https://console.cloud.google.com/apis/credentials> |

None of these come with a Claude subscription: the Claude API bills separately
through the Console, and Voyage and Google are other vendors again.

Anthropic has no embeddings endpoint, which is why embeddings are a second
vendor. Changing `EMBEDDING_MODEL` or `EMBEDDING_DIM` invalidates
`data/vectors.npy`; the app refuses to mix vector spaces rather than silently
returning nonsense.

## Running

```bash
python dev.py          # API on :8000 and Vite on :5173
python dev.py api      # API only
python dev.py web      # frontend only
python dev.py test     # ruff check + pytest
python dev.py lint     # ruff + ruff format --check + tsc + eslint
```

`plan.md` asks for a Makefile or justfile; neither `make` nor `just` is
installed on the development machine, so `dev.py` is the task runner. Python is
already a hard requirement, so this adds nothing to install.

## CLI

```bash
cd backend
uv run python -m agent_app.cli --help
uv run python -m agent_app.cli init-db
```

Subcommands are added by the phase that makes them meaningful: `ingest`
(Phase 2), `embed` (Phase 4), `ingest-profile` (Phase 5), `draft-letter`
(Phase 6), `chat` / `status` / `eval` (Phase 9), and `sync-email` (Phase 10).

## First run, in order

Everything below `ingest` needs API keys. `cli status` prints where the
pipeline currently stands at any point.

```bash
cd backend
uv run python -m agent_app.cli ingest          # fetch every board (no keys needed)
uv run python -m agent_app.cli embed           # chunk + embed  -> VOYAGE_API_KEY
uv run python -m agent_app.cli ingest-profile  # chunk + embed profile/  -> VOYAGE_API_KEY
uv run python -m agent_app.cli status          # what is in the database
```

`embed` is safe to re-run: chunks that already have a vector are skipped and
repeated text is served from the on-disk cache, so an interrupted run resumes
for free. It is the one command that spends real money on first use — roughly
six chunks per posting, at about a thousand characters each.

Once postings are embedded, the rest works:

```bash
python dev.py                                        # dashboard on :5173
uv run python -m agent_app.cli chat                  # the agent, in the terminal
uv run python -m agent_app.cli draft-letter <id>     # a grounded letter
uv run python -m agent_app.cli eval                  # retrieval numbers
```

Two of those need content, not code. `draft-letter` refuses unless `profile/`
holds real write-ups — a letter with nothing to ground it in would be invented,
so the refusal is the feature; see `profile/README.md` for the format. And
`eval` reads `data/eval/queries.jsonl`, one hand-labelled line per query:

```json
{"query": "remote ML internships in Europe", "relevant_posting_ids": ["greenhouse:123"], "note": "why these count"}
```

Nothing can label those but a person. A few dozen lines is enough to turn
"this chunk size feels better" into a number that moves.

## Tracking applications from email

Once a few postings are marked applied, `sync-email` reads the replies:

```bash
cd backend
uv run python -m agent_app.cli sync-email --login   # once: opens a browser
uv run python -m agent_app.cli sync-email           # every time after
```

It fetches messages received since your earliest application, matches each to
a posting, and classifies it as a rejection, an interview invitation, an offer,
or something else. Then it **stops**. Every result is a suggestion in the
`/inbox` review queue; accepting one is what moves the application, and the
`status_history` note records which email caused it.

That separation is the whole point. A wrongly auto-applied `rejected` is worse
than no automation at all, because you stop checking a company that actually
wanted to interview you.

Setup is a Google Cloud project with the Gmail API enabled and an OAuth client
of type **Desktop app**; `.env.example` has the steps. Three properties hold by
construction rather than by care:

- the only scope requested is `gmail.readonly`, and there is no code path that
  sends, labels or deletes anything;
- messages are fetched with `format=metadata`, so Gmail never sends a body and
  there is no body to store;
- the refresh token lives in `data/gmail_token.json`, which is gitignored.

`plan.md` specifies the OAuth *device* flow. Google restricts that flow to a
fixed scope list that does not include any Gmail scope, so the flow used is the
one Google documents for desktop apps: a loopback redirect to `127.0.0.1` on an
ephemeral port, with PKCE. Every property the plan asked for is unchanged.

## Layout

```
backend/    Python package `agent_app` — ingest, core, api, cli
frontend/   Vite + React 19 + TypeScript + Tailwind v4
profile/    project write-ups, gitignored except the README and example
data/       sqlite, vectors.npy, embedding cache, drafted letters — gitignored
```

## Dependency justifications

`plan.md` asks for one line per dependency.

**Backend.** `httpx` — one HTTP client for the three job boards and the
embeddings API, so retry and backoff are written once. `numpy` — the vector
store is a single array; brute-force cosine is correct at this corpus size.
`python-dotenv` — keeps keys out of the repo. `anthropic` — the agent's model
client, and the email classifier's. `fastapi` + `uvicorn` — the API layer. `pytest`, `ruff` — tests and
lint. No `voyageai` client: Phase 4 requires hand-written batching and backoff,
so a client that already does that would be redundant.

**Frontend.** `react`, `react-dom`, `vite`, `typescript` — the stack.
`tailwindcss` + `@tailwindcss/vite` — styling from the tokens.
`@tanstack/react-query` — server state. `@tanstack/react-table` — headless
grid. `@tanstack/react-virtual` — a few hundred 36px rows need virtualising
and the table is headless. `react-router` — four routes need a router.
`@radix-ui/*` — dialog, dropdown, tooltip and popover behaviour only, styled
from the tokens. `@fontsource-variable/geist{,-mono}` — self-hosted Geist, so
the app makes no third-party font request. `eslint` and plugins — lint.

No vector database, no agent framework, no chart library. And no Google client
libraries: Gmail is two GETs and the OAuth exchange is one POST, so `httpx` and
the standard library cover Phase 10 without adding three packages to hold the
mailbox credentials.
