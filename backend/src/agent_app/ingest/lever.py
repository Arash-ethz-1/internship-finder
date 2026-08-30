"""Lever postings API.

One public GET per company::

    https://api.lever.co/v0/postings/{company}?mode=json

The response is a bare JSON array. A posting's text is split across three
places — ``descriptionPlain``, a ``lists`` array of titled bullet sections, and
``additionalPlain`` — so the body is stitched back together here in the order
a reader would see it on the page.
"""

from __future__ import annotations

from typing import Any

from ..db import Posting
from .normalize import build_posting, iso_from_epoch_ms, normalize_text, strip_html

SOURCE = "lever"

# Lever runs two independent API hosts and a token exists on exactly one of
# them. `seb` and `mobileye` are 404 on api.lever.co and 200 on api.eu.lever.co,
# so checking only the US host silently loses every EU-hosted company.
HOSTS: tuple[str, ...] = ("api.lever.co", "api.eu.lever.co")
DEFAULT_HOST = HOSTS[0]

URL_TEMPLATE = "https://{host}/v0/postings/{token}?mode=json"


def build_url(token: str, host: str | None = None) -> str:
    """The endpoint for one company's postings on a given host."""
    return URL_TEMPLATE.format(host=host or DEFAULT_HOST, token=token)


def verify_url(token: str, host: str | None = None) -> str:
    """Cheapest URL that proves the board exists. Lever has no metadata
    endpoint, so this is the postings list itself."""
    return build_url(token, host)


def parse_verification(payload: Any) -> tuple[str | None, int | None]:
    """Read a display name and job count out of a verification response.

    Lever does not publish the company's display name anywhere, so the caller
    keeps whatever name it already had.
    """
    if isinstance(payload, list):
        return (None, len(payload))
    return (None, None)


def _body(job: dict[str, Any]) -> str:
    """Reassemble the description, the bullet sections and the closing note."""
    parts: list[str] = []

    intro = normalize_text(job.get("descriptionPlain")) or strip_html(job.get("description"))
    if intro:
        parts.append(intro.strip())

    for section in job.get("lists") or []:
        if not isinstance(section, dict):
            continue
        heading = (section.get("text") or "").strip()
        content = strip_html(section.get("content"))
        block = "\n".join(p for p in (heading, content) if p)
        if block:
            parts.append(block)

    closing = normalize_text(job.get("additionalPlain")) or strip_html(job.get("additional"))
    if closing:
        parts.append(closing.strip())

    return "\n\n".join(parts)


def _remote(job: dict[str, Any]) -> bool | None:
    """Lever's own workplace flag, where the board sets one."""
    workplace = (job.get("workplaceType") or "").strip().lower()
    if not workplace:
        return None
    return workplace == "remote"


def parse(payload: Any, company: str) -> list[Posting]:
    """Turn one postings response into postings, skipping records we cannot use."""
    if not isinstance(payload, list):
        return []

    postings: list[Posting] = []
    for job in payload:
        if not isinstance(job, dict):
            continue

        categories = job.get("categories")
        categories = categories if isinstance(categories, dict) else {}

        posting = build_posting(
            source=SOURCE,
            external_id=str(job.get("id") or ""),
            company=company,
            title=job.get("text") or "",
            location=categories.get("location"),
            url=job.get("hostedUrl") or job.get("applyUrl") or "",
            body=_body(job),
            posted_at=iso_from_epoch_ms(job.get("createdAt")),
            remote=_remote(job),
        )
        if posting is not None:
            postings.append(posting)

    return postings
