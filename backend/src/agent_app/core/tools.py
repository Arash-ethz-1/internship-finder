"""The four things the agent is allowed to do.

The Python functions are **Category A** and complete. Every ``description``
field in :data:`TOOL_SCHEMAS` is **Category B** and is left as the literal
string ``"TODO: author writes this"``.

Why the descriptions are reserved: they are the only thing the model reads
when deciding which tool to reach for. The function signature is invisible to
it. A vague description produces an agent that searches when it should have
looked something up, or calls ``update_status`` on a posting it never
retrieved. Tool descriptions are the real interface between you and the model,
and writing them is the closest thing to programming it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Any

from ..config import get_settings
from ..db import STATUSES, TRACKED_STATUSES, Posting, now_iso
from ..runtime import get_db
from . import retrieval, screen
from .locations import REGIONS
from .retrieval import SearchFilters

TODO_DESCRIPTION = "TODO: author writes this"


def _posting_dict(posting: Posting, *, include_body: bool = False) -> dict[str, Any]:
    """Serialise a posting for the model. Bodies are long, so they are opt-in."""
    data: dict[str, Any] = {
        "posting_id": posting.id,
        "company": posting.company,
        "title": posting.title,
        "location": posting.location,
        "remote": posting.remote,
        "level": posting.level,
        "url": posting.url,
        "posted_at": posting.posted_at,
        "deadline": posting.deadline,
        "source": posting.source,
        "first_seen": posting.first_seen,
        "last_seen": posting.last_seen,
    }
    if include_body:
        data["body"] = posting.body
    return data


# --- the four tools --------------------------------------------------------


DEFAULT_FIND_LIMIT = 30

# How many chunk hits to ask for per posting wanted. Hits are chunks and this
# returns postings, so asking for exactly `limit` chunks would collapse to far
# fewer postings than the caller asked for.
FIND_OVERSAMPLE = 6

# How many postings to put in front of the screen for every one shown. The
# screen only removes rows, so without slack a drop shortens the list instead
# of promoting the next candidate down into the gap.
SCREEN_POOL_FACTOR = 2


def find_postings(
    query: str,
    filters: dict[str, Any] | None = None,
    limit: int = DEFAULT_FIND_LIMIT,
    *,
    queries: list[str] | None = None,
    company: str | None = None,
    level: str | None = None,
    location: str | None = None,
    country: str | None = None,
    region: str | None = None,
    remote: bool | None = None,
    posted_after: str | None = None,
) -> list[dict[str, Any]]:
    """Build a working list of postings for the person to act on.

    Whole postings, deduplicated and capped, so the person gets a list they can
    select from and act on -- rather than chunk excerpts, which is what the
    retrieval layer returns underneath.

    This was one of two search tools until 2026-09-02. The other,
    ``search_postings``, returned excerpts for the model to read. Two tools
    with synonymous names is the worst case for a model that has to choose
    between them from the descriptions alone, and choosing wrong meant results
    were never recorded as ``found`` and so were offered again forever. Where
    the model genuinely needs a posting's text it calls :func:`get_posting`.

    Several phrasings can be fused into one ranking with ``queries``; see
    :func:`agent_app.core.retrieval.search_many` for why one phrasing is a
    gamble on vocabulary.

    Every candidate list is read back by :mod:`agent_app.core.screen` before it
    is returned, because retrieval ranks by similarity and similarity is not
    relevance -- a quant trading role scores well on "ML research" while being
    a different job entirely. Screened-out postings come back in the same list,
    flagged and slimmed, rather than vanishing.

    Two rules make the list usable across several searches:

    * only *undecided* postings are considered — never triaged, or surfaced by
      an earlier search and not judged since. Anything you actually decided
      something about is never offered twice; anything you merely walked past
      comes back, because walking past a result is not a decision;
    * every posting *shown* is recorded as ``found``, with a history entry
      naming the query that surfaced it. A screened-out posting is not, so it
      stays undecided and the next search offers it again.

    That recording is what makes the list persist. It is deliberately not a
    judgement: ``found`` says a search surfaced this, nothing more, and the
    person still decides whether it becomes ``interested``.
    """
    limit = max(1, min(int(limit), 100))
    # Filters arrive as flat keyword arguments rather than a nested object.
    # The schema used to ask the model for `{"query": ..., "filters": {...}}`,
    # and a nested object is one more pair of braces to close correctly in a
    # single streamed token sequence -- which it regularly failed to do,
    # recovering only on the retry. `filters` stays accepted so callers inside
    # this codebase (and the tests) can still pass a dict.
    flat = {
        "company": company,
        "level": level,
        "location": location,
        "country": country,
        "region": region,
        "remote": remote,
        "posted_after": posted_after,
    }
    parsed = SearchFilters.from_dict(
        {
            **(filters or {}),
            **{k: v for k, v in flat.items() if v is not None},
            "kind": "posting",
            "status": "undecided",
        }
    )
    # `query` is the primary phrasing and the one recorded against whatever
    # this surfaces; `queries` are alternates fused into the same ranking.
    # search_many falls back to a plain search for a single phrasing, so the
    # one-query path is byte-for-byte what it was before.
    phrasings = [query, *(queries or [])]
    hits = retrieval.search_many(phrasings, parsed, k=limit * FIND_OVERSAMPLE)

    # Collapse chunks onto their posting, keeping each posting's best rank.
    # Screening only removes rows, so it needs more candidates than the caller
    # asked for; with the screen off this is exactly `limit` and the list is
    # what it was before the screen existed.
    pool_size = limit * SCREEN_POOL_FACTOR if get_settings().screen_results else limit
    best: dict[str, Any] = {}
    for hit in hits:
        if hit.posting_id and hit.posting_id not in best:
            best[hit.posting_id] = hit
        if len(best) >= pool_size:
            break

    conn = get_db()
    candidates: list[dict[str, Any]] = []
    for posting_id, hit in best.items():
        row = conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
        if row is None:  # pragma: no cover - a chunk outliving its posting
            continue
        data = _posting_dict(Posting.from_row(row))
        data["score"] = hit.score
        data["component_scores"] = dict(hit.component_scores)
        data["excerpt"] = hit.text
        data["status"] = "found"
        candidates.append(data)

    # The screen sees every phrasing, because each one is part of how this
    # search understood the request. It is the agent's reading of what the
    # person wants, not their own words -- a screen can only be as good as the
    # query the agent wrote.
    verdict = screen.screen(" / ".join(phrasings), candidates, _applied_filters(parsed))

    kept = [c for i, c in enumerate(candidates) if i not in verdict.dropped][:limit]
    for rank, data in enumerate(kept, start=1):
        data["rank"] = rank

    found_at = now_iso()
    _record_found(conn, [d["posting_id"] for d in kept], query, found_at)

    # Dropped postings are deliberately *not* recorded as `found`. They stay
    # undecided, so the next search offers them again: the screen removes rows
    # from one list, and a model reading a title is not grounds for burying a
    # job for good. They travel back slimmed down and flagged, so the person
    # can see what was taken away and why, and reach it in one click.
    #
    # Every drop is reported, not a sample of them. A cap here would be the one
    # place in this app where a posting disappears with nothing on screen to
    # say it ever existed, and a flagged row without its excerpt costs a couple
    # of dozen tokens -- far too little to buy that back.
    return kept + [
        _screened_out(candidates[i], reason) for i, reason in sorted(verdict.dropped.items())
    ]


# Which `SearchFilters` fields the screen is told about, and what to call them
# in a sentence. `kind` and `status` are left out on purpose: both are always
# set by `find_postings` itself and neither is part of what the person asked
# for, so naming them would invite the screen to reason about plumbing.
_FILTER_LABELS = (
    ("level", "seniority"),
    ("company", "company"),
    ("location", "location"),
    ("country", "country"),
    ("region", "region"),
    ("remote", "remote"),
    ("posted_after", "posted after"),
)


def _applied_filters(filters: SearchFilters) -> str:
    """Say which constraints SQL already enforced, in words the screen can use.

    The screen's hardest judgement is what *not* to judge. A filter that ran is
    a question already answered, and a filter that did not is a part of the
    request nothing has checked yet -- and those two need opposite treatment
    from the same list of candidates.
    """
    named = [
        f"{label}={getattr(filters, field)}"
        for field, label in _FILTER_LABELS
        if getattr(filters, field) is not None
    ]
    return ", ".join(named)


def _screened_out(candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    """A dropped posting, reduced to what explains the drop.

    No excerpt and no scores: those are what the frontend keys its result-list
    renderer off, and a screened-out row must not arrive in the list of things
    to act on. It also keeps a rejected list from costing as many tokens as
    the accepted one.
    """
    return {
        "posting_id": candidate["posting_id"],
        "company": candidate["company"],
        "title": candidate["title"],
        "location": candidate["location"],
        "level": candidate["level"],
        "url": candidate["url"],
        "screened_out": True,
        "screen_reason": reason,
    }


def _record_found(
    conn: sqlite3.Connection,
    posting_ids: list[str],
    query: str,
    found_at: str,
) -> None:
    """Mark postings as ``found``, once, with the query that surfaced them.

    Never overwrites an existing row: the search already filtered to untriaged,
    but a concurrent status change between the search and this write would
    otherwise be silently undone. ``INSERT ... ON CONFLICT DO NOTHING`` makes
    that impossible rather than unlikely.
    """
    if not posting_ids:
        return
    note = f"found by search: {query}"[:200]
    with conn:
        for posting_id in posting_ids:
            cursor = conn.execute(
                "INSERT INTO applications (posting_id, status, note, updated_at) "
                "VALUES (?, 'found', ?, ?) ON CONFLICT(posting_id) DO NOTHING",
                (posting_id, note, found_at),
            )
            if cursor.rowcount:
                conn.execute(
                    "INSERT INTO status_history "
                    "(posting_id, from_status, to_status, note, changed_at) "
                    "VALUES (?, NULL, 'found', ?, ?)",
                    (posting_id, note, found_at),
                )


def get_posting(posting_id: str) -> dict[str, Any]:
    """Fetch one posting in full, including its body and application state."""
    conn = get_db()
    row = conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
    if row is None:
        raise KeyError(f"No posting with id {posting_id!r}")

    data = _posting_dict(Posting.from_row(row), include_body=True)

    application = conn.execute(
        "SELECT status, note, letter_path, updated_at FROM applications WHERE posting_id = ?",
        (posting_id,),
    ).fetchone()
    data["status"] = application["status"] if application else "untriaged"
    data["note"] = application["note"] if application else ""
    data["letter_path"] = application["letter_path"] if application else None

    data["history"] = [
        {
            "from_status": h["from_status"],
            "to_status": h["to_status"],
            "note": h["note"],
            "changed_at": h["changed_at"],
        }
        for h in conn.execute(
            "SELECT from_status, to_status, note, changed_at FROM status_history "
            "WHERE posting_id = ? ORDER BY id",
            (posting_id,),
        )
    ]
    return data


def update_status(posting_id: str, status: str, note: str = "") -> dict[str, Any]:
    """Set a posting's application status and record the change.

    Validation is Category A and deliberately strict: the model will
    occasionally invent a status like ``"pending"``, and silently accepting it
    would corrupt the pipeline view. An unknown status raises with the allowed
    set in the message, which the agent can read and retry against.
    """
    if status not in STATUSES:
        raise ValueError(f"Unknown status {status!r}. Allowed: {', '.join(STATUSES)}")

    conn = get_db()
    if conn.execute("SELECT 1 FROM postings WHERE id = ?", (posting_id,)).fetchone() is None:
        raise KeyError(f"No posting with id {posting_id!r}")

    previous = conn.execute(
        "SELECT status FROM applications WHERE posting_id = ?", (posting_id,)
    ).fetchone()
    from_status = previous["status"] if previous else None
    changed_at = now_iso()

    with conn:
        conn.execute(
            "INSERT INTO applications (posting_id, status, note, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(posting_id) DO UPDATE SET "
            "status = excluded.status, note = excluded.note, updated_at = excluded.updated_at",
            (posting_id, status, note, changed_at),
        )
        # Every transition is recorded, including a no-op re-set, so the
        # history is a faithful log of what happened rather than a diff.
        conn.execute(
            "INSERT INTO status_history (posting_id, from_status, to_status, note, changed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (posting_id, from_status, status, note, changed_at),
        )

    return {
        "posting_id": posting_id,
        "from_status": from_status,
        "status": status,
        "note": note,
        "updated_at": changed_at,
    }


def reset_status(posting_id: str, note: str = "") -> dict[str, Any]:
    """Forget everything about a posting and put it back in the pool.

    Deletes the ``applications`` row rather than moving it to some "cleared"
    status, because untriaged is the absence of a row -- that is what
    :func:`find_postings` looks for, and a posting you have genuinely undecided
    should be offered again like any other.

    The history entry stays. It records ``to_status = 'untriaged'``, so
    changing your mind is visible afterwards instead of leaving a gap where a
    decision used to be. Resetting something that has no row is not an error;
    it is already in the state you asked for.
    """
    conn = get_db()
    if conn.execute("SELECT 1 FROM postings WHERE id = ?", (posting_id,)).fetchone() is None:
        raise KeyError(f"No posting with id {posting_id!r}")

    previous = conn.execute(
        "SELECT status FROM applications WHERE posting_id = ?", (posting_id,)
    ).fetchone()
    from_status = previous["status"] if previous else None
    changed_at = now_iso()

    with conn:
        conn.execute("DELETE FROM applications WHERE posting_id = ?", (posting_id,))
        if from_status is not None:
            conn.execute(
                "INSERT INTO status_history (posting_id, from_status, to_status, note, changed_at) "
                "VALUES (?, ?, 'untriaged', ?, ?)",
                (posting_id, from_status, note or "reset to untriaged", changed_at),
            )

    return {
        "posting_id": posting_id,
        "from_status": from_status,
        "status": "untriaged",
        "note": "",
        "updated_at": changed_at,
    }


def list_shortlist(status: str | None = None, *, query: str | None = None) -> list[dict[str, Any]]:
    """List postings the person is actually pursuing, optionally filtered.

    ``found`` is excluded unless asked for by name. A search can surface
    hundreds of postings and record every one of them; returning those here
    would bury the handful the person has actually decided something about,
    which is the only thing this tool is for.

    ``query`` narrows by meaning, using the same two steps as
    :func:`find_postings`: hybrid retrieval to rank, then
    :mod:`agent_app.core.screen` to remove what is a different kind of job.
    Without it, "the ML ones I applied to" was answered by handing the model
    every applied posting and letting it read the titles -- which is fine at
    ten and quietly drops "Applied Scientist Intern" at two hundred, because a
    title does not have to contain the words you searched for.

    Search alone is not enough here and that is the point: over a set this
    small it only *reorders*, so every row still comes back. The screen is what
    turns a ranking into an answer.
    """
    conn = get_db()
    sql = (
        "SELECT p.*, a.status, a.note, a.updated_at FROM applications a "
        "JOIN postings p ON p.id = a.posting_id"
    )
    params: list[Any] = []
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"Unknown status {status!r}. Allowed: {', '.join(STATUSES)}")
        sql += " WHERE a.status = ?"
        params.append(status)
    else:
        marks = ",".join("?" * len(TRACKED_STATUSES))
        sql += f" WHERE a.status IN ({marks})"
        params.extend(TRACKED_STATUSES)
    sql += " ORDER BY a.updated_at DESC"

    out: list[dict[str, Any]] = []
    for row in conn.execute(sql, params):
        data = _posting_dict(Posting.from_row(row))
        data["status"] = row["status"]
        data["note"] = row["note"]
        data["updated_at"] = row["updated_at"]
        out.append(data)
    if not query or not out:
        return out
    return _narrow_by_meaning(out, query, status)


def _narrow_by_meaning(
    rows: list[dict[str, Any]], query: str, status: str | None
) -> list[dict[str, Any]]:
    """Rank a pipeline slice by a query, then screen it, the way search does.

    Two rules make this different from :func:`find_postings`, and both come
    from these postings already having been decided about:

    * nothing is recorded. A `found` row here would overwrite a real decision,
      and there is nothing to record anyway -- the person already knows about
      every one of these.
    * the returned shape is unchanged, so a narrowed shortlist renders exactly
      like an un-narrowed one. Adding scores would push these rows into the
      search-result renderer, which offers "not for me" on a job you already
      applied to.
    """
    by_id = {row["posting_id"]: row for row in rows}
    ids = tuple(by_id)
    hits = retrieval.search(
        query,
        SearchFilters(kind="posting", posting_ids=ids, include_closed=True),
        k=len(ids) * FIND_OVERSAMPLE,
    )
    # Ranked first, then anything retrieval never saw. A posting whose chunks
    # are not embedded yet must not silently vanish from the person's own
    # pipeline just because a query was added.
    excerpts: dict[str, str] = {}
    ordered: list[dict[str, Any]] = []
    for hit in hits:
        if hit.posting_id in by_id and hit.posting_id not in excerpts:
            excerpts[hit.posting_id] = hit.text
            ordered.append(by_id[hit.posting_id])
    ordered += [row for row in rows if row["posting_id"] not in excerpts]

    scope = f"pipeline status={status}" if status else "already in the person's pipeline"
    verdict = screen.screen(
        query,
        [{**row, "excerpt": excerpts.get(row["posting_id"], "")} for row in ordered],
        scope,
    )
    kept = [row for i, row in enumerate(ordered) if i not in verdict.dropped]
    return kept + [
        _screened_out(ordered[i], reason) for i, reason in sorted(verdict.dropped.items())
    ]


# How many companies and places to name in a corpus_stats answer. Enough to
# see the shape of what is there; not so many that the model reads out a
# directory instead of answering.
STATS_BREAKDOWN = 8

# Decisions are the useful signal and there are not many of them, so this can
# afford to be generous. `found` is excluded and is the reason: a search can
# record hundreds of those, and none of them is a judgement.
DEFAULT_DECISIONS_LIMIT = 40


def corpus_stats(
    filters: dict[str, Any] | None = None,
    *,
    query: str | None = None,  # accepted and ignored; see below
    company: str | None = None,
    level: str | None = None,
    location: str | None = None,
    country: str | None = None,
    region: str | None = None,
    remote: bool | None = None,
    posted_after: str | None = None,
) -> dict[str, Any]:
    """How much the corpus holds under these constraints, before ranking.

    The question this exists to answer honestly: when the person asks for ML
    research internships in Zurich and ten come back, did ten get *selected*
    out of many, or are ten all there is? A ranked list looks identical either
    way, and answering the second case as though it were the first is the
    agent implying a judgement it never made.

    Counts postings that are actually retrievable — the same join and the same
    filter semantics search itself uses (:func:`retrieval.corpus_sql`) — so
    the ceiling reported here is a ceiling search can really reach.

    ``undecided`` is the subset the person has not judged yet, which is what
    :func:`find_postings` is allowed to return. It is the number that says
    whether a search has anything left to offer.

    ``query`` is accepted and ignored. The model reaches this tool straight
    after :func:`find_postings` and carries the same argument shape across,
    which was observed costing a ``TypeError`` and a wasted round trip. There
    is nothing to do with the text — a count is over constraints, not over
    relevance — so taking it and dropping it is strictly better than failing.
    Same reasoning as :meth:`SearchFilters.from_dict` ignoring unknown keys.
    """
    flat = {
        "company": company,
        "level": level,
        "location": location,
        "country": country,
        "region": region,
        "remote": remote,
        "posted_after": posted_after,
    }
    parsed = SearchFilters.from_dict(
        {
            **(filters or {}),
            **{k: v for k, v in flat.items() if v is not None},
            "kind": "posting",
        }
    )
    conn = get_db()

    def one(sql: str, params: list[Any]) -> int:
        row = conn.execute(sql, params).fetchone()
        return int(row[0] or 0) if row else 0

    postings = one(*retrieval.corpus_sql(parsed, "COUNT(DISTINCT c.posting_id)"))
    companies = one(*retrieval.corpus_sql(parsed, "COUNT(DISTINCT p.company)"))
    undecided = one(
        *retrieval.corpus_sql(replace(parsed, status="undecided"), "COUNT(DISTINCT c.posting_id)")
    )

    def breakdown(column: str) -> list[dict[str, Any]]:
        sql, params = retrieval.corpus_sql(
            parsed,
            f"{column} AS label, COUNT(DISTINCT c.posting_id) AS n",
            group_by=column,
            order_by="n DESC",
            limit=STATS_BREAKDOWN,
        )
        return [
            {"name": row["label"], "postings": row["n"]}
            for row in conn.execute(sql, params)
            if row["label"]
        ]

    return {
        "postings": postings,
        "companies": companies,
        "undecided": undecided,
        "top_companies": breakdown("p.company"),
        "top_locations": breakdown("p.location"),
        "filters_applied": {k: v for k, v in flat.items() if v is not None},
    }


DEFAULT_PROFILE_LIMIT = 5


def search_profile(query: str, limit: int = DEFAULT_PROFILE_LIMIT) -> list[dict[str, Any]]:
    """Search the person's own project write-ups, not the job postings.

    The same corpus the letter drafter is grounded in, which until now the
    chat agent could not see at all. It is what lets a request like "ML
    research" be expanded into the vocabulary this particular person actually
    has — distributed attention, GNNs, sensor fusion — before it goes anywhere
    near a posting search.
    """
    limit = max(1, min(int(limit), 20))
    hits = retrieval.search(query, SearchFilters(kind="profile"), k=limit)
    # The full SearchHit shape, not a reduced one. The chat trace panel picks
    # its renderer off the output's shape, and anything carrying
    # `component_scores` is drawn as a scored hit -- so a half-populated dict
    # rendered as "#undefined undefined" with a score bar beside it. Returning
    # the real shape makes the profile passages draw with their own score
    # decomposition, the same way the letters page already shows grounding.
    return [{**hit.to_dict(), "document": hit.profile_doc} for hit in hits]


def past_decisions(
    status: str | None = None,
    limit: int = DEFAULT_DECISIONS_LIMIT,
    *,
    query: str | None = None,
) -> list[dict[str, Any]]:
    """What the person has already decided about postings, newest first.

    A labelled relevance set produced by ordinary use rather than by anyone
    sitting down to label anything. ``not_relevant`` rows are the negative
    examples — roles that matched a search well enough to be shown and were
    struck anyway — and they carry information no query text does: they say
    what this person does not want despite it looking like what they asked
    for.

    ``found`` is never included. A search records it in bulk and it means only
    that something was surfaced, so counting it as a decision would drown the
    real ones at a ratio of about fifty to one.

    ``query`` narrows by meaning, exactly as in :func:`list_shortlist`. Both
    return pipeline rows, so a ``query`` on only one of them is a hole: asked
    "which of the ones I passed on were ML research", the model reaches for
    this tool, gets thirty rows, and sorts them out by reading the titles --
    which is the thing the narrowing exists to stop.
    """
    limit = max(1, min(int(limit), 200))
    conn = get_db()
    sql = (
        "SELECT p.*, a.status, a.note, a.updated_at FROM applications a "
        "JOIN postings p ON p.id = a.posting_id WHERE a.status != 'found'"
    )
    params: list[Any] = []
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"Unknown status {status!r}. Allowed: {', '.join(STATUSES)}")
        if status == "found":
            raise ValueError(
                "'found' is not a decision; it only records that a search surfaced a posting"
            )
        sql += " AND a.status = ?"
        params.append(status)
    sql += " ORDER BY a.updated_at DESC LIMIT ?"
    params.append(limit)

    out: list[dict[str, Any]] = []
    for row in conn.execute(sql, params):
        data = _posting_dict(Posting.from_row(row))
        data["status"] = row["status"]
        data["note"] = row["note"]
        data["decided_at"] = row["updated_at"]
        out.append(data)
    if not query or not out:
        return out
    return _narrow_by_meaning(out, query, status)


# --- schemas ---------------------------------------------------------------
#
# The shapes are Category A. Every `description` is Category B.

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "find_postings",
        "description": (
            "Build the person's working list: up to 30 whole postings matching what they "
            "are looking for, shown to them as a list they can select from, open, and "
            "change the status of. Use this whenever they are asking to be shown jobs "
            "rather than asking a question about jobs — 'find me ML research internships "
            "in Zurich', 'show me quant roles'. This is the only way to search; there is "
            "no other. A search is cheap and you are expected to run more than one: look "
            "at the titles that come back, and if they are not the thing that was "
            "asked for, search again with different words rather than presenting them. "
            "Postings the board has taken down are never returned. "
            "Postings already in the pipeline are excluded "
            "automatically, so a second search never returns the same posting twice, and "
            "everything returned is remembered. Results are screened before you see "
            'them, and a row marked "screened_out": true was judged to be a '
            "different kind of job than the one asked for. Those are not results: "
            "they are shown folded away so the person can check the screen, and you "
            "must not present them or count them. Do not list the results back in "
            "prose: the person is already looking at them. Say how many came back and "
            "what they have in common, mention how many were screened out if any were, "
            "and stop. If the screen removed most of what came back, the search words "
            "were wrong rather than the corpus being empty — search again."
        ),
        "input_schema": {
            "type": "object",
            # Every filter is a top-level property rather than a nested
            # `filters` object. The nested form asked the model to close a
            # second pair of braces inside one streamed tool call, which it
            # got wrong often enough to need a retry almost every time.
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to look for, in natural language, phrased the way a posting "
                        "would be written ('machine learning research internship, PyTorch') "
                        "rather than as a command. Keep place, company and level out of "
                        "this — they have their own arguments below, and repeating them "
                        "here only dilutes the ranking."
                    ),
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Two or three *other* ways the same job might be worded, scored "
                        "separately and fused into one ranking. This is the cheapest "
                        "thing you can do to improve a search and you should almost "
                        "always pass it. A posting is written by whoever wrote it: one "
                        "company says 'machine learning research intern', another says "
                        "'deep learning PhD internship', another 'applied scientist "
                        "intern'. Passing only one of those finds only that one. Vary "
                        "the vocabulary, not the meaning — alternates for a different "
                        "job make the ranking worse, not broader."
                    ),
                },
                "region": {
                    "type": "string",
                    "enum": list(REGIONS),
                    "description": (
                        "Continent-scale place. This is how you ask for 'in Europe' or "
                        "'in North America'. It matches a normalised place rather than "
                        "the board's own wording, so it works regardless of how the "
                        "posting spelled the city."
                    ),
                },
                "country": {
                    "type": "string",
                    "description": (
                        "ISO 3166-1 alpha-2 country code — CH for Switzerland, DE for "
                        "Germany, GB for the United Kingdom, US for the United States. "
                        "Use this for 'in Switzerland' rather than putting the country "
                        "in location."
                    ),
                },
                "location": {
                    "type": "string",
                    "description": (
                        "A city, matched as a substring of what the board wrote. Use it "
                        "only for a specific city ('Zurich'); for anything larger use "
                        "country or region, which are exact."
                    ),
                },
                "company": {"type": "string", "description": "Exact company name."},
                "level": {
                    "type": "string",
                    "enum": ["intern", "newgrad", "unknown"],
                    "description": (
                        "Ask for 'intern' whenever the person is looking for an "
                        "internship. Most postings are labelled from their title, and "
                        "'unknown' means the heuristic could not tell rather than that "
                        "the role is senior."
                    ),
                },
                "remote": {"type": "boolean", "description": "Remote roles only."},
                "posted_after": {
                    "type": "string",
                    "description": "UTC ISO-8601 date. Only postings published since then.",
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "How many postings to return, at most 30 unless the person asks "
                        "for more. Fewer and better beats a long list they will not read."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_posting",
        "description": (
            "Read one posting in full by its posting_id: the complete body, the "
            "deadline, the URL, the current application status, and the history of "
            "every status change. Use it once you have an id and need detail that a "
            "search excerpt does not carry — requirements, dates, whether it has "
            "already been applied to. Always read a posting this way before calling "
            "update_status on it. Fails if the id does not exist, which is the signal "
            "that the id was invented or mistyped rather than a reason to try again."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "posting_id": {
                    "type": "string",
                    "description": (
                        "The exact id from a search result or the shortlist, such as "
                        "'greenhouse:4f2a91' or 'lever:7d69cf8a'. Never construct one "
                        "from a company name — ids come from tool output only."
                    ),
                },
            },
            "required": ["posting_id"],
        },
    },
    {
        "name": "update_status",
        "description": (
            "Record where an application stands: mark a posting interested, applied, "
            "rejected, and so on. This writes to the person's real pipeline and every "
            "change is kept in a permanent history, so set a status only when they have "
            "actually asked for it or told you what they did — never to tidy up, and "
            "never on a posting you have not read with get_posting first. Setting the "
            "status a posting already has is recorded as a real event, so do not "
            "re-set one to confirm it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "posting_id": {
                    "type": "string",
                    "description": (
                        "The exact id of the posting to update, from a search result, "
                        "get_posting, or list_shortlist."
                    ),
                },
                "status": {
                    "type": "string",
                    "enum": list(STATUSES),
                    "description": (
                        "The new state of this application. Only the listed values are "
                        "accepted; anything else is rejected with the allowed set in "
                        "the error, so read that message rather than inventing a "
                        "synonym like 'pending' or 'in progress'."
                    ),
                },
                "note": {
                    "type": "string",
                    "description": (
                        "A short line on why the status changed — 'deadline passed', "
                        "'referred by a friend'. It is shown next to the entry in the "
                        "dashboard, so write it for the person to read later, and leave "
                        "it empty rather than filling it with a restatement of the "
                        "status."
                    ),
                },
            },
            "required": ["posting_id", "status"],
        },
    },
    {
        "name": "list_shortlist",
        "description": (
            "List every posting that already has an application status, newest change "
            "first, optionally narrowed to one status. This is the person's pipeline — "
            "use it for questions about what they are tracking ('what have I applied "
            "to', 'anything still just interested?') rather than find_postings, which "
            "searches all postings including the thousands never triaged. Returns the "
            "posting summary plus its status and note, without the body. Pass "
            "`query` when the question is about a *kind* of job inside the pipeline "
            "rather than the whole of it, and do not try to sort that out yourself by "
            "reading the titles: a title does not have to contain the words that "
            "describe it, and 'Applied Scientist Intern' is a machine learning job."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": list(STATUSES),
                    "description": (
                        "Return only postings in this state. Omit it to get the whole "
                        "shortlist, which is usually what a general question about the "
                        "pipeline wants."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Narrow the pipeline to one kind of work, by meaning rather "
                        "than by wording — 'the ML ones I applied to', 'which of my "
                        "interviews are robotics'. Phrase it the way a posting would "
                        "be written. Anything ruled out comes back marked "
                        "`screened_out` with a reason; do not present those, and say "
                        "how many there were. Omit this for a plain question about the "
                        "pipeline: it costs a model call and there is nothing to "
                        "narrow when they asked for all of it."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "corpus_stats",
        "description": (
            "Count what the database holds under a set of constraints, without ranking "
            "anything: how many postings match at all, across how many companies, how "
            "many of them the person has not judged yet, and the largest companies and "
            "cities inside that set. Takes the same place, level and company arguments "
            "as find_postings and means exactly the same thing by them. "
            "Use it whenever the honest answer might be 'there is not much here'. Ten "
            "results selected from four hundred and ten results that are the entire "
            "corpus look identical in a list, and presenting the second as though it "
            "were the first tells the person a judgement was made that was not. Also "
            "use it before saying a search found nothing, so you can say whether the "
            "constraint was too narrow or the corpus is simply empty there — and after "
            "a disappointing search, to check whether a better query could have helped "
            "at all. Counts only postings that search can actually reach. It takes no "
            "query: this counts what matches the constraints, so wording changes "
            "nothing about the answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "enum": list(REGIONS),
                    "description": "Continent-scale place, the way find_postings takes it.",
                },
                "country": {
                    "type": "string",
                    "description": "ISO 3166-1 alpha-2 country code, e.g. CH, DE, GB, US.",
                },
                "location": {
                    "type": "string",
                    "description": "A city, matched as a substring of the board's own wording.",
                },
                "company": {"type": "string", "description": "Exact company name."},
                "level": {
                    "type": "string",
                    "enum": ["intern", "newgrad", "unknown"],
                    "description": "Pass 'intern' when the question is about internships.",
                },
                "remote": {"type": "boolean", "description": "Remote roles only."},
                "posted_after": {
                    "type": "string",
                    "description": "UTC ISO-8601 date. Only postings published since then.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_profile",
        "description": (
            "Search the person's own project write-ups — their background, not the job "
            "postings. Returns the passages of their history that best match a query, "
            "which is the same material their motivational letters are grounded in. "
            "Two uses. First, before searching for jobs on a broad request like 'ML "
            "research' or 'something that fits me', to find the vocabulary they "
            "actually work in and put those words into the search instead of generic "
            "ones. Second, to answer questions about their own experience, or to say "
            "why a posting is or is not a fit for them specifically. Never invent "
            "background: if this returns nothing on a topic, they have not written "
            "about it, and you should say so rather than assume."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to look for in their history, as a topic or skill — "
                        "'distributed training', 'graph neural networks', 'C++'."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "How many passages to return. Default 5, at most 20.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "past_decisions",
        "description": (
            "Read what the person has already decided about postings, newest first: "
            "what they marked not_relevant, interested, applied, and so on, each with "
            "the posting and any note. Postings merely surfaced by a search are never "
            "included, because being shown something is not deciding about it. "
            "This is the closest thing available to knowing their taste, and it is "
            "worth reading before showing a long list of results: the roles they "
            "struck are the ones that matched a search well enough to be shown and "
            "were rejected anyway, which is exactly the mistake you are about to "
            "repeat. If the last twenty they rejected were all quant trading, do not "
            "lead with quant trading. Use it to drop your own false positives before "
            "they reach the person, and say what you dropped and why rather than "
            "silently filtering. It is evidence about their preferences, not a rule: "
            "a single rejection is noise, and a person is allowed to change their mind."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Narrow to one kind of work, by meaning rather than by wording "
                        "— 'which of the ones I passed on were ML research'. Use this "
                        "instead of reading the titles yourself and deciding: a title "
                        "need not contain the words that describe it, and 'Applied "
                        "Scientist Intern' is a machine learning job. What it rules "
                        "out comes back marked `screened_out` with a reason; do not "
                        "present those. Omit it when the question is about the "
                        "decisions themselves rather than about a subject."
                    ),
                },
                "status": {
                    "type": "string",
                    "enum": [s for s in STATUSES if s != "found"],
                    "description": (
                        "Return only decisions of this kind. 'not_relevant' is the one "
                        "that carries taste — roles they were shown and struck. Omit it "
                        "to see every decision in order."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "How many to return. Default 40, at most 200.",
                },
            },
            "required": [],
        },
    },
]

# Name to callable. `run_agent` dispatches through this.
TOOL_FUNCTIONS: dict[str, Any] = {
    "find_postings": find_postings,
    "get_posting": get_posting,
    "update_status": update_status,
    "list_shortlist": list_shortlist,
    "corpus_stats": corpus_stats,
    "search_profile": search_profile,
    "past_decisions": past_decisions,
}


def descriptions_written() -> bool:
    """True once no schema description is still the placeholder.

    The agent will technically run with placeholder descriptions and will
    choose tools badly. ``cli chat`` uses this to warn rather than to block.
    """
    return TODO_DESCRIPTION not in _all_descriptions(TOOL_SCHEMAS)


def _all_descriptions(node: Any) -> list[str]:
    """Every ``description`` string anywhere in the schemas, nested included."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_all_descriptions(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_all_descriptions(item))
    return found
