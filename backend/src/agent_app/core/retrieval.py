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

import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .. import runtime
from ..db import LEVELS, STATUSES

NOT_IMPLEMENTED = "Category B — author writes this by hand"

# The reciprocal-rank-fusion constant. 60 is the value from the original RRF
# paper and a reasonable default; it damps how much the very top ranks
# dominate the fused score.
DEFAULT_RRF_K = 60

# BM25's two knobs. ``k1`` sets how fast term frequency saturates — the tenth
# occurrence of a word says much less than the second. ``b`` sets how hard long
# documents are penalised: 0 ignores length entirely, 1 normalises fully.
BM25_K1 = 1.2
BM25_B = 0.75

# The keys of `SearchHit.component_scores`. The frontend draws one stacked bar
# segment per key, so these strings are a wire contract: changing them changes
# the dashboard.
# Not statuses in the database: ways of asking about the absence of a decision.
# "untriaged" is no application row at all; "undecided" also admits `found`,
# which records that a search surfaced a posting rather than what you think
# of it.
PSEUDO_STATUSES: tuple[str, ...] = ("untriaged", "undecided")

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
        if self.status is not None and self.status not in (*STATUSES, *PSEUDO_STATUSES):
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


@dataclass(frozen=True)
class CandidateKey:
    """A candidate with everything except its text.

    Scoring never reads the text — the dense side works off ``vector_row`` and
    the keyword side off the precomputed index — and pulling 135 MB out of
    SQLite to then use ten rows of it was two seconds of every search.
    """

    chunk_id: int
    posting_id: str | None
    profile_doc: str | None
    ordinal: int
    vector_row: int | None


def candidate_sql(filters: SearchFilters, *, with_text: bool = True) -> tuple[str, list[Any]]:
    """Build the SQL that narrows chunks down to what the filters allow.

    Category A. Split out from :func:`search` so the filtering is testable on
    its own and so the Category B work is purely about scoring.

    ``with_text=False`` selects the same rows without the one column that
    dominates the transfer.
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
    elif filters.status == "undecided":
        # Never triaged, or surfaced by a search and not judged since. A
        # posting the agent found and you walked past is still undecided, so
        # the next search has to be able to offer it again.
        where.append("(a.posting_id IS NULL OR a.status = 'found')")
    elif filters.status:
        where.append("a.status = ?")
        params.append(filters.status)

    columns = "c.id, c.posting_id, c.profile_doc, c.ordinal, c.vector_row"
    if with_text:
        columns += ", c.text"

    sql = (
        f"SELECT {columns} "
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


def load_candidate_keys(conn: sqlite3.Connection, filters: SearchFilters) -> list[CandidateKey]:
    """Fetch every chunk the filters allow, without its text. Category A."""
    sql, params = candidate_sql(filters, with_text=False)
    return [
        CandidateKey(
            chunk_id=row["id"],
            posting_id=row["posting_id"],
            profile_doc=row["profile_doc"],
            ordinal=row["ordinal"],
            vector_row=row["vector_row"],
        )
        for row in conn.execute(sql, params)
    ]


def load_texts(conn: sqlite3.Connection, chunk_ids: list[int]) -> dict[int, str]:
    """The text of a handful of chunks, by id. Category A.

    Only ever called with the k rows that are about to be returned, so the
    ``IN`` clause stays far away from SQLite's parameter limit.
    """
    if not chunk_ids:
        return {}
    marks = ",".join("?" * len(chunk_ids))
    rows = conn.execute(f"SELECT id, text FROM chunks WHERE id IN ({marks})", chunk_ids)
    return {int(row["id"]): row["text"] for row in rows}


# --- Category B ------------------------------------------------------------


def dense_scores(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Score every row of ``matrix`` against ``query_vec``.

    ``query_vec`` has shape ``(dim,)``; ``matrix`` has shape ``(n, dim)``.
    Returns shape ``(n,)``, one score per row, higher meaning more similar.

    Brute-force cosine over the whole array is the right call at this corpus
    size — a few thousand chunks is microseconds of numpy, and an ANN index
    would add a dependency and hide the arithmetic.

    Vectors are not assumed to be L2-normalised, so the norms are computed
    here. Rows of length zero — and a zero query — score 0.0 rather than
    ``nan``: one ``nan`` would poison every comparison in the ranking.

    Category B, written by Claude at the author's request on 2026-08-31.
    """
    if matrix.size == 0:
        return np.zeros(matrix.shape[0], dtype=np.float32)
    query = np.asarray(query_vec, dtype=np.float32)
    docs = np.asarray(matrix, dtype=np.float32)
    dots = docs @ query
    denominator = np.linalg.norm(docs, axis=1) * np.linalg.norm(query)
    return np.divide(dots, denominator, out=np.zeros_like(dots), where=denominator > 0)


def bm25_scores(query: str, corpus_tokens: list[list[str]]) -> np.ndarray:
    """Score a tokenised corpus against a query with BM25.

    ``corpus_tokens[i]`` is the token list for candidate ``i`` (build it with
    :func:`tokenize`). Returns shape ``(len(corpus_tokens),)``.

    BM25 needs term frequency, document frequency, document length and the
    average document length, plus the ``k1`` and ``b`` constants. Write it by
    hand — no ``rank_bm25`` dependency — because the point is to know what
    those constants do.

    The IDF here is Lucene's variant, ``log(1 + (N - n + 0.5) / (n + 0.5))``,
    which stays positive when a term appears in every document. The textbook
    form goes negative there, which would rank a document *below* one that does
    not contain the term at all.

    Category B, written by Claude at the author's request on 2026-08-31.
    """
    n_docs = len(corpus_tokens)
    if n_docs == 0:
        return np.zeros(0)

    lengths = np.array([len(doc) for doc in corpus_tokens], dtype=np.float64)
    average_length = lengths.mean() or 1.0
    # The length penalty does not depend on the term, so it is hoisted out.
    length_norm = BM25_K1 * (1 - BM25_B + BM25_B * lengths / average_length)
    counts = [Counter(doc) for doc in corpus_tokens]

    scores = np.zeros(n_docs, dtype=np.float64)
    for term in sorted(set(tokenize(query))):  # sorted: float addition order
        frequencies = np.array([doc[term] for doc in counts], dtype=np.float64)
        containing = int(np.count_nonzero(frequencies))
        if containing == 0:
            continue
        idf = math.log(1 + (n_docs - containing + 0.5) / (containing + 0.5))
        scores += idf * frequencies * (BM25_K1 + 1) / (frequencies + length_norm)
    return scores


def rrf_contribution(scores: np.ndarray, k: int = DEFAULT_RRF_K) -> np.ndarray:
    """One ranking's share of the fused score: ``1 / (k + rank)`` per candidate.

    Rank 1 is the highest score. Ties are broken by position, which keeps the
    result deterministic — two chunks with identical scores always resolve the
    same way, so re-running a search never reshuffles the dashboard.
    """
    values = np.asarray(scores, dtype=np.float64)
    order = np.argsort(-values, kind="stable")
    ranks = np.empty(values.shape[0], dtype=np.float64)
    ranks[order] = np.arange(1, values.shape[0] + 1)
    return 1.0 / (k + ranks)


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
    contribution, so the decomposition falls out of the same arithmetic —
    :func:`rrf_contribution` is that half, exposed for exactly this reason.

    Category B, written by Claude at the author's request on 2026-08-31.
    """
    if not score_lists:
        return np.zeros(0)
    fused = np.zeros(len(score_lists[0]), dtype=np.float64)
    for scores in score_lists:
        fused += rrf_contribution(scores, k)
    return fused


def search(query: str, filters: SearchFilters, k: int = 10) -> list[SearchHit]:
    """Run the hybrid search and return the top ``k`` hits, best first.

    The orchestration this is expected to do:

    1. ``conn = get_db()``; ``candidates = load_candidate_keys(conn, filters)``
       (both Category A and ready to use)
    2. embed ``query`` with ``runtime.get_provider()``
    3. :func:`dense_scores` over the candidates' rows of
       ``runtime.get_vectors()``
    4. the keyword half from ``runtime.get_bm25_index()``, which computes
       :func:`bm25_scores`' formula against a precomputed inverted index
    5. :func:`fuse` the two
    6. take the top ``k`` and build :class:`SearchHit` objects, filling
       ``component_scores`` with ``{COMPONENT_DENSE: …, COMPONENT_BM25: …}``
       such that the two values sum to ``score``, and ``rank`` starting at 1

    Takes no connection or provider by design: this signature is fixed by the
    plan, so dependencies come from :mod:`agent_app.runtime`.

    A candidate with no ``vector_row`` — ingested but not yet embedded — scores
    0.0 on the dense side and keeps its BM25 contribution, so a fresh posting
    is findable by keyword the moment it lands rather than invisible until the
    next ``cli embed``.

    Category B, written by Claude at the author's request on 2026-08-31.
    """
    conn = runtime.get_db()
    candidates = load_candidate_keys(conn, filters)
    if not candidates:
        return []

    query_vec = runtime.get_provider().embed([query])[0]
    matrix = runtime.get_vectors()
    rows = np.array([-1 if c.vector_row is None else c.vector_row for c in candidates])
    embedded = (rows >= 0) & (rows < len(matrix))
    dense = np.zeros(len(candidates), dtype=np.float64)
    if embedded.any():
        dense[embedded] = dense_scores(query_vec, matrix[rows[embedded]])
    # The same arithmetic as bm25_scores, over an index built once instead of
    # per query. tests/test_bm25_index.py holds the two to the same numbers.
    keyword = runtime.get_bm25_index().scores(query, [c.chunk_id for c in candidates])

    # Equivalent to fuse([dense, keyword]), kept as halves because
    # component_scores has to show what each retriever contributed.
    parts = {
        COMPONENT_DENSE: rrf_contribution(dense),
        COMPONENT_BM25: rrf_contribution(keyword),
    }
    fused = parts[COMPONENT_DENSE] + parts[COMPONENT_BM25]

    top = np.argsort(-fused, kind="stable")[:k]
    # Text is fetched for the k rows that survived, not the 135,000 that were
    # scored. Nothing above this line needs it.
    texts = load_texts(conn, [candidates[i].chunk_id for i in top])
    return [
        SearchHit(
            chunk_id=candidates[i].chunk_id,
            posting_id=candidates[i].posting_id,
            profile_doc=candidates[i].profile_doc,
            ordinal=candidates[i].ordinal,
            text=texts.get(candidates[i].chunk_id, ""),
            score=float(fused[i]),
            rank=rank,
            component_scores={name: float(part[i]) for name, part in parts.items()},
        )
        for rank, i in enumerate(top, start=1)
    ]
