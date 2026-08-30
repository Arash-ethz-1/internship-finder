# Internship agent

A local agentic app over internship postings: batch ingest from public job-board
APIs, hybrid retrieval, an LLM agent with tools, a React dashboard, and a
motivational-letter drafter grounded in my own project history.

This is a learning project. [`plan.md`](plan.md) is the spec and takes
precedence over this file.

## Delegation boundary

Some functions are deliberately left as `raise NotImplementedError` and are
written by hand by the author — chunking, retrieval, the agent loop, the tool
descriptions, and evaluation. See the Category B table in `plan.md`. If the app
raises `NotImplementedError`, that is the design working, not a bug.

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
| `ANTHROPIC_API_KEY` | the agent, letter drafting | <https://console.anthropic.com/settings/keys> |
| `VOYAGE_API_KEY` | embeddings | <https://dashboard.voyageai.com/api-keys> |

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
(Phase 6), and `chat` / `status` / `eval` (Phase 9).

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
client. `fastapi` + `uvicorn` — the API layer. `pytest`, `ruff` — tests and
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

No vector database, no agent framework, no chart library.
