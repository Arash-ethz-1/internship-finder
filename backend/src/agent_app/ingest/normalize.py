"""Map each board's JSON onto the ``postings`` schema.

Pure functions only: nothing here touches the network or the database, so
every rule in this file is testable against a saved fixture.

Three jobs:

* turn whatever passes for a description into readable plain text
* work out a posting's ``level`` and whether it is remote
* put timestamps into one format (UTC ISO-8601) regardless of what the board
  handed us
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime
from html.parser import HTMLParser

from ..db import Posting, now_iso

__all__ = [
    "body_hash",
    "build_posting",
    "infer_level",
    "infer_remote",
    "iso_from_epoch_ms",
    "iso_from_string",
    "make_id",
    "normalize_text",
    "now_iso",
    "strip_html",
]

# Tags that should leave a line break behind when they are stripped, so the
# plain text keeps the shape of the original list or paragraph.
_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table"}
)

_WHITESPACE_RUNS = re.compile(r"[ \t]+")
_BLANK_LINE_RUNS = re.compile(r"\n{3,}")

# Titles are terse and deliberate: a single keyword there is a real signal.
# `\b` keeps "intern" from matching "internal" or "international", which is
# the whole reason this is a regex and not a substring check.
_INTERN_TITLE_WORDS = re.compile(
    r"\b("
    r"intern|interns|internship|internships|"
    r"praktikum|praktika|praktikant\w*|"
    r"working student|werkstudent\w*|"
    r"thesis|masterarbeit|bachelorarbeit|"
    r"co-?op"
    r")\b",
    re.IGNORECASE,
)

_NEWGRAD_TITLE_WORDS = re.compile(
    r"\b("
    r"new ?grad\w*|new graduate|recent graduate|university graduate|"
    r"graduate program\w*|grad program\w*|"
    r"entry[- ]level|early career|campus hire|"
    r"absolvent\w*|berufseinsteiger\w*"
    r")\b",
    re.IGNORECASE,
)

# Bodies are long and mention everything. A senior role's description routinely
# contains the word "intern" — in a boilerplate disclaimer ("if you are an
# intern or new grad, do not apply here"), in a list of who the team mentors,
# or in "internal tooling". Matching those turns the level filter into noise,
# so the body is only allowed to decide the level when the wording actually
# asserts that *this* posting is one.
_INTERN_BODY_PHRASES = re.compile(
    r"("
    r"\bas an intern\b|\bthe intern will\b|\bthis internship\b|"
    r"\binterns?(hip)? (position|role|programme?|opportunity)\b|"
    r"\b(summer|winter|spring|fall|autumn|\d{4}) internship\b|"
    r"\bduring (the|your) internship\b|"
    r"\bpraktikum\b|\bpraktikant\w*\b|\bwerkstudent\w*\b|"
    r"\bworking student (position|role)\b|"
    r"\b(master'?s?|bachelor'?s?|diploma) thesis\b|\bmasterarbeit\b|\bbachelorarbeit\b"
    r")",
    re.IGNORECASE,
)

_NEWGRAD_BODY_PHRASES = re.compile(
    r"("
    r"\bnew ?grad\w* (position|role|programme?|opportunity)\b|"
    r"\bgraduate (programme?|scheme)\b|"
    r"\bentry[- ]level (position|role)\b|"
    r"\bfor (recent|new|upcoming) graduates\b|"
    r"\bgraduating in \d{4}\b"
    r")",
    re.IGNORECASE,
)

_REMOTE_WORDS = re.compile(r"\b(remote|work from home|anywhere|distributed)\b", re.IGNORECASE)

# Some boards say "remote" only to rule it out.
_NOT_REMOTE_WORDS = re.compile(r"\b(no[nt][- ]remote|not remote|on-?site only)\b", re.IGNORECASE)


class _TagStripper(HTMLParser):
    """Collect the text of an HTML document, keeping block-level line breaks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "li":
            self._parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        # Not `li`: the next item's start tag already breaks the line, and
        # emitting one here too would double-space every bullet list.
        if tag != "li" and tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def strip_html(raw: str | None) -> str:
    """Turn a description field into plain text.

    Greenhouse stores HTML that has itself been HTML-escaped, so the JSON
    contains ``&lt;p&gt;`` rather than ``<p>``. Unescaping first turns that
    back into real markup; boards that already send real markup are unaffected.
    """
    if not raw:
        return ""

    unescaped = html.unescape(raw)
    parser = _TagStripper()
    parser.feed(unescaped)
    parser.close()
    return normalize_text(parser.text())


def normalize_text(raw: str | None) -> str:
    """Tidy whitespace in text that is already plain.

    Lever and Ashby publish a ``descriptionPlain`` field, so their bodies never
    pass through :func:`strip_html` and used to keep whatever whitespace the
    company typed - mostly non-breaking spaces, which are invisible but are not
    blank. A line holding only one looks like a paragraph break to a reader and
    like content to code, which is exactly the kind of thing that quietly ruins
    chunk boundaries.
    """
    if not raw:
        return ""
    text = raw.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WHITESPACE_RUNS.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINE_RUNS.sub("\n\n", "\n".join(lines)).strip()


def infer_level(title: str, body: str) -> str:
    """Guess ``intern`` / ``newgrad`` from wording, defaulting to ``unknown``.

    The title decides whenever it can: it is short, deliberate, and a keyword
    there is almost always about the role itself. The body is a fallback and
    is held to a much higher bar — a phrase asserting that *this* posting is
    an internship, not any passing mention of the word.

    Anything unclear stays ``unknown`` rather than being guessed at. Level is
    the primary filter in the dashboard, and a wrong label there is worse than
    an honest absence: it buries real internships under senior roles.
    """
    if _INTERN_TITLE_WORDS.search(title):
        return "intern"
    if _NEWGRAD_TITLE_WORDS.search(title):
        return "newgrad"
    if _INTERN_BODY_PHRASES.search(body):
        return "intern"
    if _NEWGRAD_BODY_PHRASES.search(body):
        return "newgrad"
    return "unknown"


def infer_remote(location: str | None, explicit: bool | None = None) -> bool:
    """Decide whether a posting is remote.

    ``explicit`` is the board's own flag where it has one (Ashby's ``isRemote``,
    Lever's ``workplaceType``); it wins over reading the location string.
    """
    if explicit is not None:
        return explicit
    if not location:
        return False
    if _NOT_REMOTE_WORDS.search(location):
        return False
    return bool(_REMOTE_WORDS.search(location))


def iso_from_string(value: str | None) -> str | None:
    """Normalise a board's ISO-ish timestamp to UTC ISO-8601, or ``None``."""
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_from_epoch_ms(value: int | float | None) -> str | None:
    """Normalise Lever's milliseconds-since-1970 to UTC ISO-8601, or ``None``."""
    if value is None:
        return None
    try:
        seconds = float(value) / 1000.0
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def body_hash(body: str) -> str:
    """Fingerprint a posting body, so re-ingestion can tell edits from no-ops."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def make_id(source: str, external_id: str) -> str:
    """Build the primary key, ``"{source}:{external_id}"``."""
    return f"{source}:{external_id}"


def build_posting(
    *,
    source: str,
    external_id: str,
    company: str,
    title: str,
    location: str | None,
    url: str,
    body: str,
    posted_at: str | None,
    deadline: str | None = None,
    remote: bool | None = None,
) -> Posting | None:
    """Assemble a :class:`Posting`, or ``None`` if the record is unusable.

    A posting with no id, no title or no url cannot be stored or linked to, so
    it is dropped rather than written as a broken row.
    """
    external_id = (external_id or "").strip()
    title = (title or "").strip()
    url = (url or "").strip()
    if not external_id or not title or not url:
        return None

    body = body.strip()
    location = (location or "").strip() or None

    return Posting(
        id=make_id(source, external_id),
        source=source,
        company=company,
        title=title,
        location=location,
        remote=infer_remote(location, remote),
        url=url,
        body=body,
        body_hash=body_hash(body),
        posted_at=posted_at,
        deadline=deadline,
        level=infer_level(title, body),
    )
