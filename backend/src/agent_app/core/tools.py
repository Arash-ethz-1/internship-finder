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

from typing import Any

from ..db import STATUSES, Posting, now_iso
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


def search_postings(query: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Hybrid search over posting chunks.

    Thin wrapper over :func:`agent_app.core.retrieval.search`, which is
    Category B — this will raise ``NotImplementedError`` until that is written.
    """
    parsed = SearchFilters.from_dict(filters)
    hits = retrieval.search(query, parsed)
    return [hit.to_dict() for hit in hits]


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


def list_shortlist(status: str | None = None) -> list[dict[str, Any]]:
    """List postings that have an application status, optionally filtered."""
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
        "name": "search_postings",
        "description": (
            "Find job postings by meaning and by keyword, over the text of the postings "
            "themselves. Use this whenever the question is about which postings exist — "
            "'any ML internships in Zurich', 'who is hiring for Rust' — or when you have "
            "no posting_id yet. Returns the matching excerpt from each posting with its "
            "posting_id and a relevance score, best first, not the full posting: follow "
            "up with get_posting when you need the whole body, the deadline, or the "
            "current application status. Searching is cheap; guessing is not. If the "
            "first query returns nothing useful, try again with different words before "
            "concluding that nothing matches."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to look for, in natural language. Describe the role the "
                        "way a posting would ('machine learning internship, PyTorch') "
                        "rather than as a command ('find me an ML job'). Put attributes "
                        "that have their own filter — company, city, level — in filters "
                        "instead, and keep this to the subject matter."
                    ),
                },
                "filters": {
                    "type": "object",
                    "description": (
                        "Hard constraints applied before scoring. Everything here is an "
                        "exact match, so use it only for what the person actually asked "
                        "for: a filter that is merely a good guess silently hides "
                        "postings they wanted to see."
                    ),
                    "properties": {
                        "company": {"type": "string"},
                        "level": {"type": "string", "enum": ["intern", "newgrad", "unknown"]},
                        "location": {"type": "string"},
                        "remote": {"type": "boolean"},
                        "status": {"type": "string", "enum": [*STATUSES, "untriaged"]},
                        "posted_after": {"type": "string"},
                    },
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
            "to', 'anything still just interested?') rather than search_postings, which "
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
    "search_postings": search_postings,
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
