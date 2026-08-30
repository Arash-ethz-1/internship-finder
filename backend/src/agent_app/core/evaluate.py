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

    Watch for: an empty ``relevant`` list (dividing by zero), duplicate posting
    ids in ``retrieved`` because several chunks of the same posting matched —
    decide whether that counts once or twice, and be consistent — and ``k``
    larger than ``len(retrieved)``.

    Category B — author implements.
    """
    raise NotImplementedError(NOT_IMPLEMENTED)


def run_eval(queries: list[EvalQuery], k_values: tuple[int, ...] = DEFAULT_K_VALUES) -> EvalResult:
    """Run every query through search and report mean recall at each k.

    Calls :func:`agent_app.core.retrieval.search` for each query, takes the
    posting ids from the hits in rank order, and scores them with
    :func:`recall_at_k`.

    Search returns *chunk* hits while the labels are *posting* ids, so
    collapsing chunks to their parent posting — keeping best rank per posting —
    is part of the job here.

    Category B — author implements.
    """
    raise NotImplementedError(NOT_IMPLEMENTED)
