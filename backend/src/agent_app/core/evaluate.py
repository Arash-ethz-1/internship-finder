"""Measuring whether retrieval is any good.

**Category B**: :func:`recall_at_k` and :func:`run_eval`. Loading the eval set
is Category A and complete.

Without this the rest is vibes. Changing the chunk size, adding a stemmer, or
re-weighting the fusion all *feel* like improvements; this is the only thing
that says whether they were. Build the eval set first and tune second.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

NOT_IMPLEMENTED = "Category B — author writes this by hand"

DEFAULT_K_VALUES = (1, 5, 10, 20)

# How many chunks to retrieve per unit of k. Hits are chunks and recall is
# measured over postings, so asking for exactly k chunks would measure a
# shorter list than it looks.
CHUNK_OVERSAMPLE = 5


@dataclass(frozen=True)
class EvalQuery:
    """One labelled query: a question, and the postings that should come back.

    Labels are the author's judgement. There is no way around hand-labelling
    a few dozen of these — "relevant" is not something the corpus can tell you.
    """

    query: str
    relevant_posting_ids: tuple[str, ...]
    note: str = ""


@dataclass(frozen=True)
class EvalResult:
    """What one evaluation run measured."""

    n_queries: int
    recall: dict[int, float]  # k -> mean recall@k
    per_query: dict[str, dict[int, float]]

    def format(self) -> str:
        """A one-line-per-k summary for ``cli eval``."""
        lines = [f"{self.n_queries} queries"]
        for k in sorted(self.recall):
            lines.append(f"  recall@{k:<3} {self.recall[k]:.3f}")
        return "\n".join(lines)


def load_eval_set(path: Path) -> list[EvalQuery]:
    """Read a JSONL eval set. Category A.

    One JSON object per line::

        {"query": "remote ML internships in Europe",
         "relevant_posting_ids": ["greenhouse:123", "lever:abc"],
         "note": "optional, why these count"}

    Lines that are blank or start with ``#`` are skipped, so the file can be
    annotated while you build it up.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"No eval set at {path}. Create it with one JSON object per line: "
            '{"query": "...", "relevant_posting_ids": ["source:id", ...]}'
        )

    queries: list[EvalQuery] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            raw = json.loads(stripped)
        except ValueError as exc:
            raise ValueError(f"{path.name} line {number} is not valid JSON: {exc}") from exc
        if not raw.get("query"):
            raise ValueError(f"{path.name} line {number} has no 'query'")
        queries.append(
            EvalQuery(
                query=raw["query"],
                relevant_posting_ids=tuple(raw.get("relevant_posting_ids") or ()),
                note=raw.get("note", ""),
            )
        )
    return queries


# --- Category B ------------------------------------------------------------


def recall_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    """Fraction of the relevant items that appear in the top ``k`` retrieved.

    ``retrieved`` is posting ids in rank order, best first. ``relevant`` is the
    labelled set for that query.

    A repeated id counts once and takes one slot: duplicates are collapsed
    before the top ``k`` is taken, so a posting that matched on five chunks
    cannot fill the whole window. An empty ``relevant`` list scores 0.0 —
    there was nothing to find, and 1.0 would quietly reward a query nobody
    labelled.

    Category B, written by Claude at the author's request on 2026-08-31.
    """
    wanted = set(relevant)
    if not wanted or k <= 0:
        return 0.0
    seen = dict.fromkeys(retrieved)  # de-duplicated, order preserved
    found = wanted.intersection(list(seen)[:k])
    return len(found) / len(wanted)


def agent_posting_ids(query: str, max_searches: int | None = None) -> list[str]:
    """Posting ids one full agent turn surfaced, best first. Category A.

    The vantage point :func:`run_eval` lacks by default. ``run_eval`` scores
    ``retrieval.search`` directly, which is the right instrument for chunking
    and fusion changes and is *blind to the agent*: re-searching, several
    phrasings and taste filtering all live in the loop above ``search``, so a
    perfect implementation of them moves that number by exactly zero.

    Ordering is a judgement call and worth stating plainly, because getting it
    wrong flatters or libels the agent by several points of recall. Results
    from *later* searches rank ahead of earlier ones, each search keeping its
    own internal order. The reasoning: the agent re-queried because it judged
    the previous results wrong, so its last hypothesis is its considered
    answer, and ranking a discarded first guess ahead of it would punish the
    behaviour this phase exists to add. The union is kept rather than only the
    final search, because every posting a search returns really does land in
    the person's grid.

    ``max_searches`` is the A/B switch for Phase 11's first step. Passing 1
    gives the single-shot baseline the phase is measured against: same tools,
    same filters, same fused phrasings, one search. The difference between
    that and the default is the loop, isolated from everything else that
    changed at the same time.
    """
    from .agent import DEFAULT_MAX_SEARCHES, collect_result, run_agent

    budget = DEFAULT_MAX_SEARCHES if max_searches is None else max_searches
    result = collect_result(run_agent(query, [], max_searches=budget))
    ids: list[str] = []
    for call in reversed(result.trace):
        if call.name != "find_postings" or not isinstance(call.output, list):
            continue
        for row in call.output:
            # Screened-out rows travel back in the same list so the person can
            # see what was removed. They are not results, and counting them
            # here would measure the unscreened list and report the screen as
            # having done nothing.
            if not isinstance(row, dict) or row.get("screened_out"):
                continue
            if row.get("posting_id"):
                ids.append(str(row["posting_id"]))
    return list(dict.fromkeys(ids))


def run_eval(
    queries: list[EvalQuery],
    k_values: tuple[int, ...] = DEFAULT_K_VALUES,
    *,
    through_agent: bool = False,
    max_searches: int | None = None,
) -> EvalResult:
    """Run every query through search and report mean recall at each k.

    Calls :func:`agent_app.core.retrieval.search` for each query, takes the
    posting ids from the hits in rank order, and scores them with
    :func:`recall_at_k`.

    With ``through_agent=True`` it instead runs each query through
    :func:`agent_posting_ids` — a whole agent turn, tools and all — and scores
    what the agent actually surfaced. That is the only mode in which the
    Phase 11 work is visible at all; the default measures the retrieval layer
    alone and cannot see the loop above it.

    Search returns *chunk* hits while the labels are *posting* ids, so chunks
    are collapsed to their parent posting, keeping the best rank of each.
    Because of that collapse the search asks for more chunks than the largest
    ``k``: at the median of six chunks per posting, a top-10 chunk list can
    easily be two postings.

    Category B, written by Claude at the author's request on 2026-08-31.
    """
    from . import retrieval  # deferred: importing this module must stay cheap

    k_values = tuple(sorted(k_values))
    per_query: dict[str, dict[int, float]] = {}
    depth = max(k_values, default=0) * CHUNK_OVERSAMPLE

    for query in queries:
        if through_agent:
            # Real model calls and real tool calls, one turn per query. Slow
            # and not free, which is why it is opt-in.
            postings = agent_posting_ids(query.query, max_searches)
        else:
            hits = retrieval.search(query.query, retrieval.SearchFilters(), depth)
            postings = list(dict.fromkeys(h.posting_id for h in hits if h.posting_id))
        per_query[query.query] = {
            k: recall_at_k(postings, list(query.relevant_posting_ids), k) for k in k_values
        }

    recall = {
        k: (sum(scores[k] for scores in per_query.values()) / len(per_query) if per_query else 0.0)
        for k in k_values
    }
    return EvalResult(n_queries=len(queries), recall=recall, per_query=per_query)
