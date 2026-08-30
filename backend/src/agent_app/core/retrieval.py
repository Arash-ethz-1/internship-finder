"""Hybrid dense + BM25 retrieval.

**Category B**: :func:`dense_scores`, :func:`bm25_scores`, :func:`fuse` and
:func:`search`. Everything else here — the filter and hit dataclasses, the
tokenizer, the candidate loader — is Category A and complete.

The idea the project is built around: two search methods that fail in
different directions. Dense search compares meaning through embeddings, so it
finds "PyTorch" for a query about deep learning frameworks but can miss an
exact term. BM25 is keyword scoring, so it nails exact terms and is blind to
synonyms. Fusing their rankings covers each one's blind spot, and keeping the
two contributions separate is what lets the dashboard show *why* a result
ranked where it did.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..db import LEVELS, STATUSES

NOT_IMPLEMENTED = "Category B — author writes this by hand"

# The reciprocal-rank-fusion constant. 60 is the value from the original RRF
# paper and a reasonable default; it damps how much the very top ranks
# dominate the fused score.
DEFAULT_RRF_K = 60

# The keys of `SearchHit.component_scores`. The frontend draws one stacked bar
# segment per key, so these strings are a wire contract: changing them changes
# the dashboard.
COMPONENT_DENSE = "dense"
COMPONENT_BM25 = "bm25"

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\+\+|#)?")


def tokenize(text: str) -> list[str]:
    """Split text into lowercase terms for BM25.

    Category A, but only because :func:`bm25_scores` takes tokens rather than
    raw text, so something has to produce them. It is still a retrieval
    decision: there is no stemming here, so "engineering" and "engineer" are
    different terms. Swap in a stemmer and re-run ``cli eval`` to see whether
    it helps — that is exactly the kind of experiment the eval harness is for.
    """
    return _TOKEN_RE.findall(text.lower())


@dataclass(frozen=True)
class SearchFilters:
    """Which chunks a search is allowed to return.

    ``kind`` is the important one: ``"posting"`` searches job postings,
    ``"profile"`` searches the author's own project write-ups (what the letter
    drafter needs), and ``"any"`` searches both.
    """

    kind: str = "posting"  # posting | profile | any
    company: str | None = None
    level: str | None = None  # intern | newgrad | unknown
    location: str | None = None  # substring match
    remote: bool | None = None
    status: str | None = None  # an applications.status, or "untriaged"
    posted_after: str | None = None  # UTC ISO-8601
    posting_ids: tuple[str, ...] = ()  # restrict to specific postings

    KINDS = ("posting", "profile", "any")

    def __post_init__(self) -> None:
        if self.kind not in self.KINDS:
            raise ValueError(f"kind must be one of {self.KINDS}, got {self.kind!r}")
        if self.level is not None and self.level not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}, got {self.level!r}")
        if self.status is not None and self.status not in (*STATUSES, "untriaged"):
            raise ValueError(f"unknown status {self.status!r}")

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> SearchFilters:
        """Build filters from an untrusted dict, ignoring keys we do not know.

        The agent passes filters as a plain dict, and a model will occasionally
        invent a field. Unknown keys are dropped rather than raising, so one
        hallucinated filter name does not fail the whole tool call.
        """
        if not raw:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}
        cleaned = {k: v for k, v in raw.items() if k in known}
        if "posting_ids" in cleaned and cleaned["posting_ids"] is not None:
            cleaned["posting_ids"] = tuple(cleaned["posting_ids"])
        return cls(**cleaned)


@dataclass(frozen=True)
class SearchHit:
    """One retrieved chunk and the arithmetic behind its rank.

    ``component_scores`` must sum to ``score``. That is what makes the stacked
    bar in the retrieval trace honest: each segment is a real contribution,
    not a decorative proportion.
    """

    chunk_id: int
    posting_id: str | None
    profile_doc: str | None
    ordinal: int
    text: str
    score: float
    rank: int
    component_scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API. Keys mirror the Pydantic schema in Phase 7."""
        return {
            "chunk_id": self.chunk_id,
            "posting_id": self.posting_id,
            "profile_doc": self.profile_doc,
            "ordinal": self.ordinal,
            "text": self.text,
            "score": self.score,
            "rank": self.rank,
            "component_scores": dict(self.component_scores),
        }


@dataclass(frozen=True)
class Candidate:
    """A chunk that passed the filters and is eligible to be scored."""

    chunk_id: int
    posting_id: str | None
    profile_doc: str | None
    ordinal: int
    text: str
    vector_row: int | None


def candidate_sql(filters: SearchFilters) -> tuple[str, list[Any]]:
    """Build the SQL that narrows chunks down to what the filters allow.

    Category A. Split out from :func:`search` so the filtering is testable on
    its own and so the Category B work is purely about scoring.
    """
    where: list[str] = []
    params: list[Any] = []

    if filters.kind == "posting":
        where.append("c.posting_id IS NOT NULL")
    elif filters.kind == "profile":
        where.append("c.profile_doc IS NOT NULL")

    if filters.company:
        where.append("p.company = ?")
        params.append(filters.company)
    if filters.level:
        where.append("p.level = ?")
        params.append(filters.level)
    if filters.location:
        where.append("p.location LIKE ?")
        params.append(f"%{filters.location}%")
    if filters.remote is not None:
        where.append("p.remote = ?")
        params.append(int(filters.remote))
    if filters.posted_after:
        where.append("p.posted_at >= ?")
        params.append(filters.posted_after)
    if filters.posting_ids:
        marks = ",".join("?" * len(filters.posting_ids))
        where.append(f"c.posting_id IN ({marks})")
        params.extend(filters.posting_ids)
    if filters.status == "untriaged":
        where.append("a.posting_id IS NULL")
    elif filters.status:
        where.append("a.status = ?")
        params.append(filters.status)

    sql = (
        "SELECT c.id, c.posting_id, c.profile_doc, c.ordinal, c.text, c.vector_row "
        "FROM chunks c "
        "LEFT JOIN postings p ON p.id = c.posting_id "
        "LEFT JOIN applications a ON a.posting_id = c.posting_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql + " ORDER BY c.id", params


def load_candidates(conn: sqlite3.Connection, filters: SearchFilters) -> list[Candidate]:
    """Fetch every chunk the filters allow. Category A."""
    sql, params = candidate_sql(filters)
    return [
        Candidate(
            chunk_id=row["id"],
            posting_id=row["posting_id"],
            profile_doc=row["profile_doc"],
            ordinal=row["ordinal"],
            text=row["text"],
            vector_row=row["vector_row"],
        )
        for row in conn.execute(sql, params)
    ]


# --- Category B ------------------------------------------------------------


def dense_scores(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Score every row of ``matrix`` against ``query_vec``.

    ``query_vec`` has shape ``(dim,)``; ``matrix`` has shape ``(n, dim)``.
    Returns shape ``(n,)``, one score per row, higher meaning more similar.

    Brute-force cosine over the whole array is the right call at this corpus
    size — a few thousand chunks is microseconds of numpy, and an ANN index
    would add a dependency and hide the arithmetic.

    Watch for: whether the vectors arrive already L2-normalised (if so this is
    one dot product), and what to return when ``matrix`` is empty.

    Category B — author implements.
    """
    raise NotImplementedError(NOT_IMPLEMENTED)


def bm25_scores(query: str, corpus_tokens: list[list[str]]) -> np.ndarray:
    """Score a tokenised corpus against a query with BM25.

    ``corpus_tokens[i]`` is the token list for candidate ``i`` (build it with
    :func:`tokenize`). Returns shape ``(len(corpus_tokens),)``.

    BM25 needs term frequency, document frequency, document length and the
    average document length, plus the ``k1`` and ``b`` constants. Write it by
    hand — no ``rank_bm25`` dependency — because the point is to know what
    those constants do.

    Watch for: an empty corpus, a query whose terms appear in no document,
    and terms that appear in *every* document (the IDF term can go negative
    with the textbook formula).

    Category B — author implements.
    """
    raise NotImplementedError(NOT_IMPLEMENTED)


def fuse(score_lists: list[np.ndarray], k: int = DEFAULT_RRF_K) -> np.ndarray:
    """Combine several score arrays into one, by rank rather than by value.

    Every array in ``score_lists`` has the same length and covers the same
    candidates in the same order. Returns one fused score per candidate.

    Fusing raw scores directly does not work: cosine similarity lives in
    roughly [-1, 1] while BM25 is unbounded, so whichever has the larger
    numbers would dominate. Reciprocal rank fusion sidesteps that by using
    only each candidate's *position* in each ranking — typically
    ``sum(1 / (k + rank))`` across the lists.

    Note for the caller: :func:`search` also needs each component's separate
    contribution for ``SearchHit.component_scores``, and those must sum to the
    fused score. With RRF each list's ``1 / (k + rank)`` term is exactly that
    contribution, so the decomposition falls out of the same arithmetic.

    Category B — author implements.
    """
    raise NotImplementedError(NOT_IMPLEMENTED)


def search(query: str, filters: SearchFilters, k: int = 10) -> list[SearchHit]:
    """Run the hybrid search and return the top ``k`` hits, best first.

    The orchestration this is expected to do:

    1. ``conn = get_db()``; ``candidates = load_candidates(conn, filters)``
       (both Category A and ready to use)
    2. embed ``query`` with ``runtime.get_provider()``
    3. :func:`dense_scores` over the candidates' rows of
       ``runtime.get_vectors()``
    4. :func:`bm25_scores` over ``[tokenize(c.text) for c in candidates]``
    5. :func:`fuse` the two
    6. take the top ``k`` and build :class:`SearchHit` objects, filling
       ``component_scores`` with ``{COMPONENT_DENSE: …, COMPONENT_BM25: …}``
       such that the two values sum to ``score``, and ``rank`` starting at 1

    Takes no connection or provider by design: this signature is fixed by the
    plan, so dependencies come from :mod:`agent_app.runtime`.

    Watch for: candidates whose ``vector_row`` is ``None`` because they have
    not been embedded yet, and an empty candidate set.

    Category B — author implements.
    """
    raise NotImplementedError(NOT_IMPLEMENTED)
