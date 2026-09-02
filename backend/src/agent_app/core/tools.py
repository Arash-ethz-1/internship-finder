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
from typing import Any

from ..db import STATUSES, TRACKED_STATUSES, Posting, now_iso
from ..runtime import get_db
from . import retrieval
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


def find_postings(
    query: str,
    filters: dict[str, Any] | None = None,
    limit: int = DEFAULT_FIND_LIMIT,
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

    Two rules make the list usable across several searches:

    * only *undecided* postings are considered — never triaged, or surfaced by
      an earlier search and not judged since. Anything you actually decided
      something about is never offered twice; anything you merely walked past
      comes back, because walking past a result is not a decision;
    * every posting returned is recorded as ``found``, with a history entry
      naming the query that surfaced it.

    That recording is what makes the list persist. It is deliberately not a
    judgement: ``found`` says a search surfaced this, nothing more, and the
    person still decides whether it becomes ``interested``.
    """
    limit = max(1, min(int(limit), 100))
    parsed = SearchFilters.from_dict({**(filters or {}), "kind": "posting", "status": "undecided"})
    hits = retrieval.search(query, parsed, k=limit * FIND_OVERSAMPLE)

    # Collapse chunks onto their posting, keeping each posting's best rank.
    best: dict[str, Any] = {}
    for hit in hits:
        if hit.posting_id and hit.posting_id not in best:
            best[hit.posting_id] = hit
        if len(best) >= limit:
            break

    conn = get_db()
    found_at = now_iso()
    out: list[dict[str, Any]] = []
    for rank, (posting_id, hit) in enumerate(best.items(), start=1):
        row = conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
        if row is None:  # pragma: no cover - a chunk outliving its posting
            continue
        data = _posting_dict(Posting.from_row(row))
        data["rank"] = rank
        data["score"] = hit.score
        data["component_scores"] = dict(hit.component_scores)
        data["excerpt"] = hit.text
        data["status"] = "found"
        out.append(data)

    _record_found(conn, [d["posting_id"] for d in out], query, found_at)
    return out


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


def list_shortlist(status: str | None = None) -> list[dict[str, Any]]:
    """List postings the person is actually pursuing, optionally filtered.

    ``found`` is excluded unless asked for by name. A search can surface
    hundreds of postings and record every one of them; returning those here
    would bury the handful the person has actually decided something about,
    which is the only thing this tool is for.
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
    return out


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
            "no other. Postings already in the pipeline are excluded "
            "automatically, so a second search never returns the same posting twice, and "
            "everything returned is remembered. Do not list the results back in prose: "
            "the person is already looking at them. Say how many came back and what they "
            "have in common, and stop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to look for, in natural language, phrased the way a posting "
                        "would be written ('machine learning research internship, PyTorch') "
                        "rather than as a command. Put company, city, level and remote in "
                        "filters instead of here."
                    ),
                },
                "filters": {
                    "type": "object",
                    "description": (
                        "Hard constraints applied before scoring. Exact matches, so use "
                        "only what the person actually asked for. Note that location is a "
                        "substring of what the board wrote, so a city works and a continent "
                        "does not — for 'in Europe', leave location empty and say it in "
                        "the query instead."
                    ),
                    "properties": {
                        "company": {"type": "string"},
                        "level": {"type": "string", "enum": ["intern", "newgrad", "unknown"]},
                        "location": {"type": "string"},
                        "remote": {"type": "boolean"},
                        "posted_after": {"type": "string"},
                    },
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
            "posting summary plus its status and note, without the body."
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
