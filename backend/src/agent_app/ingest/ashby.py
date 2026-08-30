"""Ashby job board API.

One public GET per company::

    https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true

Ashby is the friendliest of the three: it hands over ``descriptionPlain``
already stripped, and an explicit ``isRemote`` flag. Unlisted jobs appear in
the response with ``isListed: false`` and are skipped.
"""

from __future__ import annotations

from typing import Any

from ..db import Posting
from .normalize import build_posting, iso_from_string, strip_html

SOURCE = "ashby"

HOSTS: tuple[str, ...] = ("api.ashbyhq.com",)
DEFAULT_HOST = HOSTS[0]

URL_TEMPLATE = "https://{host}/posting-api/job-board/{token}?includeCompensation=true"


def build_url(token: str, host: str | None = None) -> str:
    """The endpoint for one company's board."""
    return URL_TEMPLATE.format(host=host or DEFAULT_HOST, token=token)


def verify_url(token: str, host: str | None = None) -> str:
    """Cheapest URL that proves the board exists. Ashby has no metadata
    endpoint, so this is the board itself, without compensation data."""
    host = host or DEFAULT_HOST
    return f"https://{host}/posting-api/job-board/{token}"


def parse_verification(payload: Any) -> tuple[str | None, int | None]:
    """Count the listed jobs. Ashby does not publish a company display name."""
    if isinstance(payload, dict):
        jobs = payload.get("jobs")
        if isinstance(jobs, list):
            return (
                None,
                sum(1 for j in jobs if isinstance(j, dict) and j.get("isListed") is not False),
            )
    return (None, None)


def parse(payload: Any, company: str) -> list[Posting]:
    """Turn one board response into postings, skipping records we cannot use."""
    if not isinstance(payload, dict):
        return []

    postings: list[Posting] = []
    for job in payload.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        if job.get("isListed") is False:
            continue

        body = job.get("descriptionPlain") or strip_html(job.get("descriptionHtml"))
        is_remote = job.get("isRemote")

        posting = build_posting(
            source=SOURCE,
            external_id=str(job.get("id") or ""),
            company=company,
            title=job.get("title") or "",
            location=job.get("location"),
            url=job.get("jobUrl") or job.get("applyUrl") or "",
            body=body,
            posted_at=iso_from_string(job.get("publishedAt") or job.get("updatedAt")),
            remote=bool(is_remote) if isinstance(is_remote, bool) else None,
        )
        if posting is not None:
            postings.append(posting)

    return postings
