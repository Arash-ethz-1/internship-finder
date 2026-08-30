"""Shared helpers for the exercise suite."""

from __future__ import annotations

from agent_app.db import Posting


def make_posting(body: str, **over: object) -> Posting:
    """A Posting with a given body, for chunking exercises."""
    fields: dict[str, object] = {
        "id": "greenhouse:1",
        "source": "greenhouse",
        "company": "Acme Robotics",
        "title": "Software Engineering Intern",
        "location": "Zurich",
        "remote": False,
        "url": "https://example.com/1",
        "body": body,
        "body_hash": "h",
        "level": "intern",
    }
    fields.update(over)
    return Posting(**fields)  # type: ignore[arg-type]
