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
5. Sound like the 21-year-old student who actually did this work, writing to \
someone they respect. Direct and specific, not formal and not slick. Short \
sentences are fine. Contractions are fine. Say "I built X and it did Y", not \
"I leveraged X to drive Y". Nothing about being thrilled, excited, passionate, \
or eager; no "I am confident that my skills align"; no synergies, no \
deliverables, no "spearheaded". If a sentence sounds like a consultant wrote \
it, or like someone fifteen years further into a career, rewrite it plainer.
6. Never use em dashes or en dashes. Use a comma, a full stop, or restructure \
the sentence. Do not use semicolons to get around this either.

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


REVISE_SYSTEM_PROMPT = """You revise motivational letters for internship applications, following \
one instruction from the applicant.

You will be given the current letter, the job posting, and the same verbatim extracts from the \
applicant's own project write-ups that the letter was drafted from. Those extracts are still the \
ONLY facts you know about the applicant.

Rules, in order of importance:

1. Do what the instruction asks, and nothing else. If it says make it shorter, do not also \
change the tone. An unrequested improvement is a regression: the applicant has read this letter \
and is asking for one change to it.
2. Never invent a detail, even to fill a gap your edit opens. Every claim about the applicant \
must still be traceable to an extract. Keep any [TODO: ...] marker that is still unanswered, and \
add a new one rather than inventing a fact.
3. Preserve the concrete specifics -- project names, technologies, what was actually built. When \
shortening, cut adjectives, throat-clearing and restatements of the job description first. The \
specifics are what make the letter worth sending.
4. Keep the register: a 21-year-old student writing directly to someone they respect. Specific, \
not formal and not slick; contractions and short sentences are fine. Nothing about being \
thrilled, excited or passionate, no "my skills align", no synergies or deliverables. If a \
sentence sounds like a consultant wrote it, make it plainer.
5. Never use em dashes or en dashes, and do not reach for a semicolon instead. A comma, a full \
stop, or a restructured sentence. If the letter you were given contains one, fix it even when \
that was not what the instruction asked for.

Return only the revised letter body. No commentary on what you changed, no subject line, no \
signature block."""

REVISE_USER_PROMPT = """# The role

Company: {company}
Title: {title}
Location: {location}

{body}

# Extracts from my own project write-ups

These are still the only facts you have about me.

{extracts}

# The current letter

{letter}

# What I want changed

{instruction}

Return the revised letter."""


class LetterError(RuntimeError):
    """Drafting could not be completed, and something about the input is why.

    An empty ``profile/``, a posting that cannot be grounded, an empty
    response. These are states the person can do something about.
    """


class ModelBusy(RuntimeError):
    """The model was unavailable. Nothing is wrong with the request.

    Kept separate from :class:`LetterError` because the two want opposite
    answers: one says fix something, the other says press the button again.
    A 529 used to arrive as a 500 with a traceback, which says neither.
    """


# The SDK's own default is 2. Drafting a letter is a single deliberate click,
# so it is worth waiting through a busy minute rather than handing back an
# error the person can only respond to by clicking again.
MODEL_RETRIES = 4
MODEL_TIMEOUT = 120.0


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
    """Ask the model for the letter body.

    The SDK retries overloads and rate limits on its own with backoff; what is
    added here is a longer budget and a translation, so that "the service is
    busy" and "your profile is empty" do not reach the dashboard as the same
    unexplained failure.
    """
    from anthropic import Anthropic, APIConnectionError, APIStatusError

    client = Anthropic(
        api_key=settings.require_anthropic_key(),
        max_retries=MODEL_RETRIES,
        timeout=MODEL_TIMEOUT,
    )
    try:
        message = client.messages.create(
            model=settings.letter_model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except APIStatusError as exc:
        if exc.status_code in (408, 409, 429) or exc.status_code >= 500:
            raise ModelBusy(
                f"The model is busy (HTTP {exc.status_code}) and did not answer after "
                f"{MODEL_RETRIES} retries. Nothing is wrong with the posting or your "
                "profile — try again in a moment."
            ) from exc
        raise LetterError(f"The model refused the request: HTTP {exc.status_code}") from exc
    except APIConnectionError as exc:
        raise ModelBusy(f"Could not reach the model: {exc}") from exc

    text = "".join(block.text for block in message.content if block.type == "text").strip()
    if not text:
        raise LetterError("The model returned an empty letter")
    return text


# An em or en dash with whatever spacing the model put around it.
_DASH = re.compile(r"\s*[—–]\s*")


def plain_dashes(text: str) -> str:
    """Replace em and en dashes with ordinary punctuation.

    The prompt asks for this and mostly gets it, but "mostly" is the wrong
    standard for something the author will read every line of: one stray dash
    in a letter is exactly the tell that it was not typed by a person. So the
    rule is enforced here as well, where it cannot be talked out of.

    A dash between two words becomes a comma, which is what it was standing in
    for. Between two digits it becomes a hyphen, because "2026-2027" and
    "250-350" are ranges rather than clauses and a comma there is nonsense.
    Next to punctuation it becomes a space, since ", ," reads worse than the
    dash did.
    """

    def replace(match: re.Match[str]) -> str:
        start, end = match.span()
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        if before.isdigit() and after.isdigit():
            return "-"
        if before.isalnum() and after.isalnum():
            return ", "
        return " " if before and after else ""

    return re.sub(r" +", " ", _DASH.sub(replace, text)).strip()


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
    text = plain_dashes(call_model(settings, build_prompt(posting, hits)))

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


def build_revision_prompt(
    posting: Posting, hits: list[SearchHit], letter: str, instruction: str
) -> str:
    """Assemble the prompt for one revision.

    The grounding extracts go back in unchanged. A revision that could not see
    them would have nothing to check a claim against, so "make it shorter"
    would be free to shorten by inventing a crisper fact.
    """
    return REVISE_USER_PROMPT.format(
        company=posting.company,
        title=posting.title,
        location=posting.location or "not stated",
        body=posting.body[:MAX_POSTING_CHARS],
        extracts=format_extracts(hits),
        letter=letter.strip(),
        instruction=instruction.strip(),
    )


def revise_letter(
    posting_id: str,
    instruction: str,
    letter: str | None = None,
    k: int = DEFAULT_PROFILE_CHUNKS,
) -> Letter:
    """Apply one instruction to an existing draft.

    ``letter`` is the text to revise, which is the editor's current contents
    rather than what is on disk -- the person may have edited it by hand
    before asking for a change, and revising the saved version would silently
    throw that away. It falls back to the file when not given.

    Regenerating from scratch was the only option before this, and it is the
    wrong shape for the job: "make it shorter" is a change to *this* letter,
    not a request for a different one.
    """
    if not instruction.strip():
        raise LetterError("A revision needs an instruction saying what to change")

    settings = get_settings()
    settings.ensure_dirs()
    conn = get_db()

    row = conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
    if row is None:
        raise KeyError(f"No posting with id {posting_id!r}")
    posting = Posting.from_row(row)

    current = letter if letter is not None else read_letter(posting_id)
    if not (current or "").strip():
        raise LetterError("There is no letter to revise yet; draft one first")

    hits = find_grounding(posting, k=k)
    text = plain_dashes(
        call_model(settings, build_revision_prompt(posting, hits, current or "", instruction))
    )

    path = letter_path(settings, posting_id)
    path.write_text(text, encoding="utf-8")

    todos = TODO_PATTERN.findall(text)
    with conn:
        conn.execute(
            "UPDATE applications SET letter_path = ?, updated_at = ? WHERE posting_id = ?",
            (str(path), now_iso(), posting_id),
        )

    return Letter(posting_id=posting_id, text=text, path=path, grounding=hits, todos=todos)


def read_letter(posting_id: str) -> str | None:
    """Return an existing draft, or ``None`` if there is not one."""
    path = letter_path(get_settings(), posting_id)
    return path.read_text(encoding="utf-8") if path.exists() else None
