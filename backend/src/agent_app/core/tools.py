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
        "source": posting.source,
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
        "description": TODO_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": TODO_DESCRIPTION,
                },
                "filters": {
                    "type": "object",
                    "description": TODO_DESCRIPTION,
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
        "description": TODO_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "posting_id": {"type": "string", "description": TODO_DESCRIPTION},
            },
            "required": ["posting_id"],
        },
    },
    {
        "name": "update_status",
        "description": TODO_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "posting_id": {"type": "string", "description": TODO_DESCRIPTION},
                "status": {
                    "type": "string",
                    "enum": list(STATUSES),
                    "description": TODO_DESCRIPTION,
                },
                "note": {"type": "string", "description": TODO_DESCRIPTION},
            },
            "required": ["posting_id", "status"],
        },
    },
    {
        "name": "list_shortlist",
        "description": TODO_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": list(STATUSES),
                    "description": TODO_DESCRIPTION,
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
