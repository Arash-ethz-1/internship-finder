"""Postings you enter by hand.

Everything else in this package pulls from a board. This is the one source
where you are the board: a LinkedIn listing, a role on a company's own careers
page, something a friend forwarded. None of those are reachable through a
public ATS feed, and PLAN.md rules out scraping, so without this they cannot be
tracked at all -- which means `/stats` quietly understates your real pipeline
and the email matcher has no posting to attach a reply to.

A manual posting is an ordinary row in ``postings``. It is chunked, embedded
and searched like any other, so it shows up in `find_postings` alongside the
boards. Two things make it different, both consequences of nobody else owning
it:

* ``source`` is ``manual``, so :func:`agent_app.ingest.runner.reconcile_closed`
  never sees it. A board that has never heard of this posting must not be able
  to declare it gone.
* it is the only kind of posting that can be edited or deleted here, because
  it is the only kind whose text is not owned by an upstream feed. Editing a
  board posting would just be undone by the next ingest.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass

from ..db import MANUAL_SOURCE, Posting, get_posting, now_iso
from .locations import index_posting
from .normalize import body_hash, infer_level, infer_remote, iso_from_string, normalize_text

log = logging.getLogger(__name__)

# A slug from the company name plus a short random suffix. The company name
# alone is not unique -- you will add two Google roles -- and a bare uuid makes
# every id in the grid unreadable.
_SLUG = re.compile(r"[^a-z0-9]+")


class ManualPostingError(ValueError):
    """The posting cannot be created or edited as described."""


@dataclass(frozen=True)
class ManualPosting:
    """What the caller supplies. Everything else is derived."""

    company: str
    title: str
    url: str
    body: str = ""
    location: str | None = None
    posted_at: str | None = None
    deadline: str | None = None
    level: str | None = None
    remote: bool | None = None


def make_manual_id(company: str) -> str:
    """A readable, unique id: ``manual:google-3f9a2c``."""
    slug = _SLUG.sub("-", company.strip().casefold()).strip("-") or "posting"
    return f"{MANUAL_SOURCE}:{slug[:40]}-{uuid.uuid4().hex[:6]}"


def _validate(draft: ManualPosting) -> None:
    """Refuse a posting that cannot be linked to or read.

    The same three fields `build_posting` requires of a board, for the same
    reason: without them the row is unusable rather than merely thin.
    """
    missing = [
        name
        for name, value in (("company", draft.company), ("title", draft.title), ("url", draft.url))
        if not (value or "").strip()
    ]
    if missing:
        raise ManualPostingError(f"a posting needs {', '.join(missing)}")


def _to_posting(posting_id: str, draft: ManualPosting) -> Posting:
    body = normalize_text(draft.body)
    location = (draft.location or "").strip() or None
    return Posting(
        id=posting_id,
        source=MANUAL_SOURCE,
        company=draft.company.strip(),
        title=draft.title.strip(),
        location=location,
        remote=infer_remote(location, draft.remote),
        url=draft.url.strip(),
        body=body,
        body_hash=body_hash(body),
        posted_at=iso_from_string(draft.posted_at) or now_iso(),
        deadline=iso_from_string(draft.deadline),
        # An explicit level wins. Otherwise fall back to the same heuristic the
        # boards get, which is right often enough and honest when it is not.
        level=draft.level or infer_level(draft.title, body),
    )


def _chunk(conn: sqlite3.Connection, posting: Posting) -> int:
    """Split a manual posting's body into chunks, replacing any it had.

    Done inline rather than left to the next ``cli embed`` because chunking is
    local and free, and a posting you just typed in and then cannot find would
    look broken. It is what makes the posting keyword-searchable immediately:
    :func:`agent_app.core.retrieval.search` scores a chunk with no
    ``vector_row`` as zero on the dense side but still ranks it on BM25.

    Embedding stays where it is. That one costs, so it waits for ``cli embed``
    along with everything else -- which is the difference the dialog reports
    rather than hides.
    """
    from ..core.chunking import chunk_posting

    conn.execute("DELETE FROM chunks WHERE posting_id = ?", (posting.id,))
    chunks = chunk_posting(posting)
    if chunks:
        conn.executemany(
            "INSERT INTO chunks (posting_id, ordinal, text) VALUES (?, ?, ?)",
            [(posting.id, chunk.ordinal, chunk.text) for chunk in chunks],
        )
    return len(chunks)


def create(conn: sqlite3.Connection, draft: ManualPosting) -> Posting:
    """Add a posting you entered yourself, chunked and located immediately."""
    _validate(draft)
    posting = _to_posting(make_manual_id(draft.company), draft)

    seen = now_iso()
    with conn:
        conn.execute(
            "INSERT INTO postings (id, source, company, title, location, remote, url, body, "
            "body_hash, posted_at, deadline, level, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                posting.id,
                posting.source,
                posting.company,
                posting.title,
                posting.location,
                int(posting.remote),
                posting.url,
                posting.body,
                posting.body_hash,
                posting.posted_at,
                posting.deadline,
                posting.level,
                seen,
                seen,
            ),
        )
        index_posting(conn, posting.id, posting.location)
        chunks = _chunk(conn, posting)

    log.info(
        "added manual posting %s (%s), %d chunk(s) awaiting embedding",
        posting.id,
        posting.company,
        chunks,
    )
    return posting


def update(conn: sqlite3.Connection, posting_id: str, draft: ManualPosting) -> Posting:
    """Edit a manual posting in place.

    Chunks are rebuilt when the body changes, and only then. The old ones
    describe text that no longer exists, so leaving them means searching a
    posting that is not there any more -- the same reason ``upsert_postings``
    drops a board posting's chunks. Rebuilding immediately rather than waiting
    for ``cli embed`` keeps the edited text findable by keyword straight away;
    the new chunks have no vectors until the next embed, which is the honest
    half of the trade.

    An edit that only changes the title or the location leaves the chunks
    alone, so a typo fix does not cost a re-embedding.
    """
    existing = get_posting(conn, posting_id)
    if existing is None:
        raise KeyError(f"No posting with id {posting_id!r}")
    if existing.source != MANUAL_SOURCE:
        raise ManualPostingError(
            f"{posting_id} came from {existing.source}; only manual postings can be edited"
        )

    _validate(draft)
    posting = _to_posting(posting_id, draft)

    with conn:
        conn.execute(
            "UPDATE postings SET company = ?, title = ?, location = ?, remote = ?, url = ?, "
            "body = ?, body_hash = ?, posted_at = ?, deadline = ?, level = ?, last_seen = ? "
            "WHERE id = ?",
            (
                posting.company,
                posting.title,
                posting.location,
                int(posting.remote),
                posting.url,
                posting.body,
                posting.body_hash,
                posting.posted_at,
                posting.deadline,
                posting.level,
                now_iso(),
                posting_id,
            ),
        )
        if posting.body_hash != existing.body_hash:
            _chunk(conn, posting)
        index_posting(conn, posting_id, posting.location)

    return posting


def delete(conn: sqlite3.Connection, posting_id: str) -> None:
    """Remove a manual posting and everything hanging off it.

    Only manual postings, and only on an explicit request. The cascade takes
    the chunks, the application and its history with it -- which is the
    difference between this and closing a board posting, where the history is
    the whole reason the row stays.
    """
    existing = get_posting(conn, posting_id)
    if existing is None:
        raise KeyError(f"No posting with id {posting_id!r}")
    if existing.source != MANUAL_SOURCE:
        raise ManualPostingError(
            f"{posting_id} came from {existing.source}; it would come back on the next ingest"
        )

    with conn:
        conn.execute("DELETE FROM postings WHERE id = ?", (posting_id,))
