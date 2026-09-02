"""A precomputed inverted index, so BM25 stops re-reading the corpus per query.

Category A. This module decides nothing about retrieval: :func:`retrieval.bm25_scores`
remains the definition of what a BM25 score *is*, and `tests/test_bm25_index.py`
asserts that this index reproduces it exactly. What lives here is only the
observation that almost none of that work depends on the query.

Measured on the real corpus, 135,851 chunks, before this existed::

    load_candidates    2.0 s     every chunk's text out of SQLite
    tokenize          16.0 s     19.7 million tokens, per query
    bm25_scores        7.8 s     135,851 Counters, per query
    dense_scores       0.4 s
    query embedding    0.2 s

Only the last two lines are query-dependent. Term frequencies, document
lengths, the average length and each term's document frequency are properties
of the corpus, so they are computed once, written to ``data/bm25.npz``, and
read back in milliseconds.

The layout is CSR: terms are sorted, ``offsets[i]:offsets[i + 1]`` slices the
postings arrays for term ``i``. Two flat numpy arrays instead of 135,851
dictionaries is most of the win — the rest is not touching a document that
contains none of the query's terms.

**One deliberate change of behaviour.** ``bm25_scores`` derives ``N``, the
average document length and every IDF from the corpus it is handed, which in
:func:`retrieval.search` is the *filtered* candidate set. This index derives
them from the whole corpus instead. That is the standard choice — a term's
rarity is a property of the collection, not of whatever filter is set — and it
stops the same query scoring differently depending on whether a company filter
happens to be on. With no filters the two agree exactly, which is what the
test pins down.
"""

from __future__ import annotations

import logging
import sqlite3
from array import array
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..config import Settings, get_settings
from .retrieval import BM25_B, BM25_K1, tokenize

log = logging.getLogger(__name__)

# Bumped when the on-disk layout changes, so an old file is rebuilt rather
# than misread.
INDEX_VERSION = 1


@dataclass(frozen=True)
class Bm25Index:
    """Every corpus-dependent quantity BM25 needs, computed once.

    ``chunk_ids[i]`` is the chunk at row ``i``; ``lengths[i]`` its token count.
    ``postings_doc[offsets[t] : offsets[t + 1]]`` are the rows containing term
    ``terms[t]``, and ``postings_tf`` the matching term frequencies.
    """

    chunk_ids: np.ndarray
    lengths: np.ndarray
    terms: list[str]
    offsets: np.ndarray
    postings_doc: np.ndarray
    postings_tf: np.ndarray

    @property
    def n_docs(self) -> int:
        return int(self.chunk_ids.shape[0])

    @property
    def max_chunk_id(self) -> int:
        return int(self.chunk_ids[-1]) if self.n_docs else 0

    def matches(self, conn: sqlite3.Connection) -> bool:
        """Is this index still a description of the chunks table?

        Count and highest id together catch every insert and delete. An *edit*
        that leaves both unchanged slips through; re-chunking a posting drops
        and reinserts its rows, which moves the maximum, so in this codebase
        that case does not arise. `cli embed` rebuilds unconditionally anyway.
        """
        row = conn.execute("SELECT count(*), coalesce(max(id), 0) FROM chunks").fetchone()
        return (int(row[0]), int(row[1])) == (self.n_docs, self.max_chunk_id)

    def scores(self, query: str, chunk_ids: list[int] | np.ndarray) -> np.ndarray:
        """BM25 for ``query`` over ``chunk_ids``, in the order given.

        A chunk this index has never seen scores 0.0 rather than raising: a
        posting ingested since the last rebuild should be missing from the
        keyword half of the ranking, not break the search.
        """
        wanted = np.asarray(chunk_ids, dtype=np.int64)
        if self.n_docs == 0 or wanted.size == 0:
            return np.zeros(wanted.shape[0], dtype=np.float64)

        average_length = float(self.lengths.mean()) or 1.0
        # The length penalty does not depend on the term, so it is hoisted out.
        length_norm = BM25_K1 * (1 - BM25_B + BM25_B * self.lengths / average_length)

        full = np.zeros(self.n_docs, dtype=np.float64)
        for term in sorted(set(tokenize(query))):  # sorted: float addition order
            position = self._find(term)
            if position < 0:
                continue
            start, end = int(self.offsets[position]), int(self.offsets[position + 1])
            docs = self.postings_doc[start:end]
            frequencies = self.postings_tf[start:end].astype(np.float64)
            containing = docs.shape[0]
            idf = np.log(1 + (self.n_docs - containing + 0.5) / (containing + 0.5))
            full[docs] += idf * frequencies * (BM25_K1 + 1) / (frequencies + length_norm[docs])

        # chunk_ids is sorted, so the candidate rows are a searchsorted away
        # rather than a dictionary lookup per candidate.
        rows = np.searchsorted(self.chunk_ids, wanted)
        rows = np.clip(rows, 0, self.n_docs - 1)
        present = self.chunk_ids[rows] == wanted
        out = np.zeros(wanted.shape[0], dtype=np.float64)
        out[present] = full[rows[present]]
        return out

    def _find(self, term: str) -> int:
        """Row of ``term`` in the vocabulary, or -1.

        The lookup table is built on first use and kept on the instance. The
        dataclass is frozen — it describes a file — so ``object.__setattr__``
        is how a cache gets attached to it.
        """
        lookup: dict[str, int] | None = getattr(self, "_lookup", None)
        if lookup is None:
            lookup = {term: position for position, term in enumerate(self.terms)}
            object.__setattr__(self, "_lookup", lookup)
        return lookup.get(term, -1)


def build_index(conn: sqlite3.Connection) -> Bm25Index:
    """Read every chunk once and invert it.

    Term frequencies accumulate into one ``array('i')`` per term rather than a
    list of tuples: at twelve million postings the difference between a Python
    tuple and four bytes is over a gigabyte.
    """
    ids = array("q")
    lengths = array("i")
    docs_of: dict[str, array] = {}
    freqs_of: dict[str, array] = {}

    row_index = 0
    for row in conn.execute("SELECT id, text FROM chunks ORDER BY id"):
        tokens = tokenize(row["text"])
        ids.append(int(row["id"]))
        lengths.append(len(tokens))
        for term, count in Counter(tokens).items():
            if term not in docs_of:
                docs_of[term] = array("i")
                freqs_of[term] = array("i")
            docs_of[term].append(row_index)
            freqs_of[term].append(count)
        row_index += 1
        if row_index % 20000 == 0:
            log.info("indexed %d chunks", row_index)

    terms = sorted(docs_of)
    offsets = np.zeros(len(terms) + 1, dtype=np.int64)
    for position, term in enumerate(terms):
        offsets[position + 1] = offsets[position] + len(docs_of[term])

    total = int(offsets[-1])
    postings_doc = np.empty(total, dtype=np.int32)
    postings_tf = np.empty(total, dtype=np.int32)
    for position, term in enumerate(terms):
        start, end = int(offsets[position]), int(offsets[position + 1])
        postings_doc[start:end] = np.frombuffer(docs_of[term], dtype=np.int32)
        postings_tf[start:end] = np.frombuffer(freqs_of[term], dtype=np.int32)

    return Bm25Index(
        chunk_ids=np.frombuffer(ids, dtype=np.int64).copy(),
        lengths=np.frombuffer(lengths, dtype=np.int32).astype(np.float64),
        terms=terms,
        offsets=offsets,
        postings_doc=postings_doc,
        postings_tf=postings_tf,
    )


def save_index(index: Bm25Index, path: Path) -> None:
    """Write the index beside the database.

    The vocabulary goes out as one newline-joined blob. A numpy array of
    200,000 unicode strings pads every one of them to the longest, which for
    this corpus is most of the file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        version=INDEX_VERSION,
        chunk_ids=index.chunk_ids,
        lengths=index.lengths.astype(np.int32),
        vocabulary=np.frombuffer("\n".join(index.terms).encode("utf-8"), dtype=np.uint8),
        offsets=index.offsets,
        postings_doc=index.postings_doc,
        postings_tf=index.postings_tf,
    )


def load_index(path: Path) -> Bm25Index | None:
    """Read the index back, or return None if it is absent or unreadable."""
    if not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as payload:
            if int(payload["version"]) != INDEX_VERSION:
                log.info("bm25 index is version %s, rebuilding", payload["version"])
                return None
            blob = payload["vocabulary"].tobytes().decode("utf-8")
            return Bm25Index(
                chunk_ids=payload["chunk_ids"],
                lengths=payload["lengths"].astype(np.float64),
                terms=blob.split("\n") if blob else [],
                offsets=payload["offsets"],
                postings_doc=payload["postings_doc"],
                postings_tf=payload["postings_tf"],
            )
    except (ValueError, KeyError, OSError, UnicodeDecodeError):
        log.warning("bm25 index at %s is unreadable, rebuilding", path)
        return None


def get_or_build(
    conn: sqlite3.Connection,
    settings: Settings | None = None,
    *,
    force: bool = False,
) -> Bm25Index:
    """The index for this database, from disk when it is still current.

    Rebuilding reads and tokenises the whole corpus, so it is not something to
    do on a hunch — but a silently stale index is worse than a slow one, and
    the staleness check is two integers out of SQLite.
    """
    settings = settings or get_settings()
    path = settings.bm25_index_path

    if not force:
        index = load_index(path)
        if index is not None and index.matches(conn):
            return index

    log.info("building the bm25 index")
    index = build_index(conn)
    save_index(index, path)
    return index
