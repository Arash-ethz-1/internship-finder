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
URL_TEMPLATE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def build_url(token: str) -> str:
    """The endpoint for one company's board."""
    return URL_TEMPLATE.format(token=token)


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
