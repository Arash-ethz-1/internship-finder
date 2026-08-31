"""Drafting a motivational letter grounded in the author's own project history.

The only retrieval it does is one call into
:func:`agent_app.core.retrieval.search`, restricted to profile chunks.

The design constraint that shapes everything here: **a letter that fabricates
experience is worse than no letter.** The model only ever sees retrieved
chunks of the author's real write-ups, and is instructed to leave an explicit
``[TODO: ...]`` marker rather than invent a detail it was not given. A missing
fact you can see is recoverable; a plausible invention you cannot see is not.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..db import Posting, now_iso
from ..runtime import get_db
from . import retrieval
from .retrieval import SearchFilters, SearchHit

log = logging.getLogger(__name__)

# How many pieces of the author's history to ground the letter in. Three is
# enough to be specific without turning the prompt into a CV dump.
DEFAULT_PROFILE_CHUNKS = 3

# Posting bodies run to 6,000 characters on average and the tail is usually
# legal boilerplate. The opening carries the role and requirements.
MAX_POSTING_CHARS = 4000

TODO_PATTERN = re.compile(r"\[TODO:[^\]]*\]")

SYSTEM_PROMPT = """You draft motivational letters for internship applications.

You will be given a job posting and several verbatim extracts from the \
applicant's own write-ups of projects they have actually built. Those extracts \
are the ONLY facts you know about the applicant.

Rules, in order of importance:

1. Ground every claim about the applicant in the extracts. If you write that \
they did something, it must be traceable to an extract.
2. Never invent a detail. No invented grades, dates, employers, tools, metrics, \
team sizes or outcomes. If the letter needs a fact you were not given, write a \
marker like [TODO: name of your current degree programme] and continue. A \
marker is a success, not a failure.
3. Do not restate the job description back at the reader. Connect specific \
things the applicant built to specific things the role needs.
4. Around 250-350 words. Plain paragraphs, no bullet lists, no headers.
5. Sober and concrete. No "I am thrilled to apply", no "passionate about \
leveraging synergies". Write like an engineer explaining why the work fits.

Return only the letter body. No subject line, no addresses, no signature block."""

USER_PROMPT = """# The role

Company: {company}
Title: {title}
Location: {location}

{body}

# Extracts from my own project write-ups

These are the only facts you have about me. Each is a verbatim piece of \
something I wrote about work I actually did.

{extracts}

# Task

Draft the motivational letter."""


class LetterError(RuntimeError):
    """Drafting could not be completed."""


@dataclass(frozen=True)
class Letter:
    """A drafted letter and everything needed to audit it."""

    posting_id: str
    text: str
    path: Path
    grounding: list[SearchHit]
    todos: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API. The grounding travels with the text so the
        dashboard can show the letter beside the chunks it came from."""
        return {
            "posting_id": self.posting_id,
            "text": self.text,
            "path": str(self.path),
            "grounding": [hit.to_dict() for hit in self.grounding],
            "todos": self.todos,
        }


def find_grounding(posting: Posting, k: int = DEFAULT_PROFILE_CHUNKS) -> list[SearchHit]:
    """Retrieve the most relevant pieces of the author's history for this role.

    The query is the posting's title and body, and the search is restricted
    to profile chunks — the author's write-ups, never the postings.
    """
    query = f"{posting.title}\n\n{posting.body[:MAX_POSTING_CHARS]}"
    return retrieval.search(query, SearchFilters(kind="profile"), k=k)


def format_extracts(hits: list[SearchHit]) -> str:
    """Render retrieved chunks for the prompt, each labelled with its source."""
    if not hits:
        raise LetterError(
            "No profile chunks were retrieved, so there is nothing to ground the "
            "letter in. Add write-ups to profile/ and run: cli ingest-profile"
        )
    blocks = []
    for index, hit in enumerate(hits, start=1):
        label = hit.profile_doc or "unknown"
        blocks.append(f"## Extract {index} (from {label})\n\n{hit.text.strip()}")
    return "\n\n".join(blocks)


def build_prompt(posting: Posting, hits: list[SearchHit]) -> str:
    """Assemble the user message. Category A, and the part worth iterating on."""
    return USER_PROMPT.format(
        company=posting.company,
        title=posting.title,
        location=posting.location or "not stated",
        body=posting.body[:MAX_POSTING_CHARS].strip(),
        extracts=format_extracts(hits),
    )


def call_model(settings: Settings, prompt: str) -> str:
    """Ask the model for the letter body."""
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.require_anthropic_key())
    message = client.messages.create(
        model=settings.agent_model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in message.content if block.type == "text").strip()
    if not text:
        raise LetterError("The model returned an empty letter")
    return text


def letter_path(settings: Settings, posting_id: str) -> Path:
    """Where a posting's draft lives. The id contains a colon, which Windows
    will not accept in a filename."""
    return settings.letters_dir / f"{posting_id.replace(':', '_')}.md"


def draft_letter(posting_id: str, k: int = DEFAULT_PROFILE_CHUNKS) -> Letter:
    """Draft a letter for one posting, write it to disk, and record the path.

    Raises ``NotImplementedError`` until :func:`retrieval.search` is written.
    That is the designed behaviour, not a bug.
    """
    settings = get_settings()
    settings.ensure_dirs()
    conn = get_db()

    row = conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
    if row is None:
        raise KeyError(f"No posting with id {posting_id!r}")
    posting = Posting.from_row(row)

    hits = find_grounding(posting, k=k)
    text = call_model(settings, build_prompt(posting, hits))

    path = letter_path(settings, posting_id)
    path.write_text(text, encoding="utf-8")

    todos = TODO_PATTERN.findall(text)
    if todos:
        log.info("%s: %d TODO marker(s) left for the author", posting_id, len(todos))

    now = now_iso()
    with conn:
        # Drafting a letter implies interest, so a posting with no application
        # row gets one rather than the letter_path being dropped on the floor.
        conn.execute(
            "INSERT INTO applications (posting_id, status, note, letter_path, updated_at) "
            "VALUES (?, 'interested', '', ?, ?) "
            "ON CONFLICT(posting_id) DO UPDATE SET "
            "letter_path = excluded.letter_path, updated_at = excluded.updated_at",
            (posting_id, str(path), now),
        )
        if (
            conn.execute(
                "SELECT count(*) FROM status_history WHERE posting_id = ?", (posting_id,)
            ).fetchone()[0]
            == 0
        ):
            conn.execute(
                "INSERT INTO status_history (posting_id, from_status, to_status, note, changed_at) "
                "VALUES (?, NULL, 'interested', 'drafted a letter', ?)",
                (posting_id, now),
            )

    return Letter(posting_id=posting_id, text=text, path=path, grounding=hits, todos=todos)


def read_letter(posting_id: str) -> str | None:
    """Return an existing draft, or ``None`` if there is not one."""
    path = letter_path(get_settings(), posting_id)
    return path.read_text(encoding="utf-8") if path.exists() else None
