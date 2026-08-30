"""Greenhouse job board API.

One public GET per company::

    https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

``content=true`` includes the description, which arrives as HTML that has been
HTML-escaped, so ``&lt;p&gt;`` rather than ``<p>``. :func:`normalize.strip_html`
handles that.
"""

from __future__ import annotations

from typing import Any

from ..db import Posting
from .normalize import build_posting, iso_from_string, strip_html

SOURCE = "greenhouse"

HOSTS: tuple[str, ...] = ("boards-api.greenhouse.io",)
DEFAULT_HOST = HOSTS[0]

URL_TEMPLATE = "https://{host}/v1/boards/{token}/jobs?content=true"
BOARD_URL_TEMPLATE = "https://{host}/v1/boards/{token}"


def build_url(token: str, host: str | None = None) -> str:
    """The endpoint for one company's board."""
    return URL_TEMPLATE.format(host=host or DEFAULT_HOST, token=token)


def verify_url(token: str, host: str | None = None) -> str:
    """Cheapest URL that proves the board exists.

    The board metadata endpoint returns a few bytes and the company's real
    display name, where fetching every job with its description would return
    megabytes. Discovery checks thousands of tokens, so this matters.
    """
    return BOARD_URL_TEMPLATE.format(host=host or DEFAULT_HOST, token=token)


def parse_verification(payload: Any) -> tuple[str | None, int | None]:
    """Read the authoritative display name out of the board metadata."""
    if isinstance(payload, dict):
        name = payload.get("name")
        return (name.strip() if isinstance(name, str) and name.strip() else None, None)
    return (None, None)


def parse(payload: Any, company: str) -> list[Posting]:
    """Turn one board response into postings, skipping records we cannot use."""
    if not isinstance(payload, dict):
        return []

    postings: list[Posting] = []
    for job in payload.get("jobs") or []:
        if not isinstance(job, dict):
            continue

        location = job.get("location")
        location_name = location.get("name") if isinstance(location, dict) else None

        posting = build_posting(
            source=SOURCE,
            external_id=str(job.get("id") or ""),
            company=company,
            title=job.get("title") or "",
            location=location_name,
            url=job.get("absolute_url") or "",
            body=strip_html(job.get("content")),
            # first_published is when it went up; updated_at is the fallback.
            posted_at=iso_from_string(job.get("first_published") or job.get("updated_at")),
        )
        if posting is not None:
            postings.append(posting)

    return postings
