"""Personio job board feed.

Added 2026-09-02 to reach the European market. Greenhouse, Lever and Ashby are
overwhelmingly US startups, and the postings this search is actually for --
``Praktikum``, ``Werkstudent``, ``Abschlussarbeit`` -- sit on Personio, which
is the default ATS for German, Austrian and Swiss employers.

Two deviations from the other three modules, both forced by the vendor:

**It is XML, not JSON.** PLAN.md's Phase 2 says "public JSON endpoints", and
Personio's JSON endpoint (``/search.json``) returns every field *except* the
description -- verified empty across every job on every board tried. The
documented ``/xml`` feed is the only one that carries the text, and a posting
with no body cannot be chunked, embedded or searched, which is the entire
point. Parsed with stdlib :mod:`xml.etree.ElementTree`, so it adds no
dependency, and it is still a documented public feed rather than scraping.

**An unknown board answers 429, not 404.** Personio serves a generic HTML
error page with status 429 for a token that does not exist. Left alone, the
polite client would treat that as rate limiting, retry twice, and finally
report a transient failure -- so discovery would record `unresolved` for a
company that is simply not on Personio, and check it again forever. Hence
:data:`NOT_FOUND_STATUSES`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from ..db import Posting
from .normalize import build_posting, iso_from_string, strip_html

SOURCE = "personio"

# The token is a subdomain here rather than a path segment. Both TLDs serve the
# same boards; ``.de`` is the older one and answers for nearly everything.
HOSTS: tuple[str, ...] = ("jobs.personio.de", "jobs.personio.com")
DEFAULT_HOST = HOSTS[0]

URL_TEMPLATE = "https://{token}.{host}/xml"
VERIFY_TEMPLATE = "https://{token}.{host}/search.json"
JOB_URL_TEMPLATE = "https://{token}.{host}/job/{job_id}"

# Statuses that mean "no such board" rather than "try again later". See the
# module docstring: Personio answers 429 with an HTML page for a bad token.
NOT_FOUND_STATUSES: tuple[int, ...] = (404, 410, 429)

# The feed is XML, so the runner hands `parse` raw bytes rather than decoded
# JSON. Every other source leaves this False.
FEED_IS_XML = True

# The section Personio appends to every description holding the apply link
# rather than prose. Kept out of the body so it is not embedded as content.
_URL_SECTION = "url"


def build_url(token: str, host: str | None = None) -> str:
    """The XML feed for one company's board."""
    return URL_TEMPLATE.format(token=token, host=host or DEFAULT_HOST)


def verify_url(token: str, host: str | None = None) -> str:
    """Cheapest URL that proves the board exists.

    ``search.json`` is a few kilobytes where the XML feed is hundreds, and
    discovery checks thousands of tokens.
    """
    return VERIFY_TEMPLATE.format(token=token, host=host or DEFAULT_HOST)


def parse_verification(payload: Any) -> tuple[str | None, int | None]:
    """Read what the board metadata can tell us.

    Personio publishes no company display name anywhere in either feed, so the
    name stays whatever the caller already had -- unlike Greenhouse, where the
    board is authoritative. What it does give is a job count, which is the
    other half of what discovery records.
    """
    if isinstance(payload, list):
        return (None, len(payload))
    return (None, None)


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _describe(position: ET.Element) -> str:
    """Flatten the description sections into one plain-text body.

    Personio splits a description into named sections ("DEIN TEAM", "DEIN
    PROFIL"), each holding HTML. Keeping the headings matters: they are exactly
    the structure `chunk_posting` splits on, so a chunk ends at a section
    boundary rather than mid-requirement.
    """
    container = position.find("jobDescriptions")
    if container is None:
        return ""

    parts: list[str] = []
    for section in container.findall("jobDescription"):
        name = _text(section.find("name"))
        if name.strip().lower() == _URL_SECTION:
            continue
        value = strip_html(_text(section.find("value")))
        if not value:
            continue
        parts.append(f"{name}\n\n{value}" if name else value)

    return "\n\n".join(parts)


def _offices(position: ET.Element) -> str | None:
    """Every office this position is offered in, as one string.

    ``core.locations`` splits on the semicolon, so a two-office posting stays
    two places instead of collapsing to the first one.
    """
    names = [_text(position.find("office"))]
    additional = position.find("additionalOffices")
    if additional is not None:
        names.extend(_text(office) for office in additional.findall("office"))
        # Some boards write the extra offices as one comma-separated string.
        if not len(additional) and _text(additional):
            names.extend(part.strip() for part in _text(additional).split(","))

    unique = list(dict.fromkeys(name for name in names if name))
    return "; ".join(unique) or None


def parse(payload: Any, company: str, token: str = "", host: str | None = None) -> list[Posting]:
    """Turn one board's XML feed into postings, skipping records we cannot use.

    ``payload`` is the raw response bytes or text, not decoded JSON -- this is
    the one source whose feed is XML, and :func:`agent_app.ingest.runner`
    hands it through untouched.
    """
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if not isinstance(payload, bytes | bytearray):
        return []

    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []

    postings: list[Posting] = []
    for position in root.findall("position"):
        job_id = _text(position.find("id"))
        if not job_id:
            continue

        posting = build_posting(
            source=SOURCE,
            external_id=job_id,
            company=company,
            title=_text(position.find("name")),
            location=_offices(position),
            url=JOB_URL_TEMPLATE.format(
                token=token or company, host=host or DEFAULT_HOST, job_id=job_id
            ),
            body=_describe(position),
            posted_at=iso_from_string(_text(position.find("createdAt"))),
        )
        if posting is not None:
            postings.append(posting)

    return postings
