"""Turning a subject line into ``rejection | interview | offer | other``.

One model call per candidate email, as PLAN.md specifies. The input is a
subject and Gmail's own snippet — never a body — which is around a hundred
tokens, so this is the cheapest step in the app despite being the only one
that needs judgement.

The classifier's output is a *suggestion*. Nothing here writes to
``applications``; see :mod:`agent_app.inbox.sync` for why that separation is
the whole point of the phase.

Two things the prompt works hard at:

* **``other`` is a first-class answer, not a failure.** Most mail from a
  company you applied to is not about your application. A classifier that
  feels obliged to pick one of the three interesting labels manufactures
  suggestions out of newsletters.
* **Confidence has to mean something.** An automated "we received your
  application" is not a rejection, and a model that reports 0.9 on everything
  makes the review queue's ordering useless.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..db import CLASSIFICATIONS, SUGGESTED_STATUS
from .gmail import EmailMessage

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You classify emails a job applicant received, using only \
the subject line and a short preview.

Answer with exactly one label:

- "rejection" — they are not moving forward with this application. Includes \
"we have decided to proceed with other candidates" and "the position has been \
filled".
- "interview" — they want to talk: an invitation to interview, a screening \
call, an online assessment, a request for availability, or a scheduling link.
- "offer" — they are offering the job, or sending an offer letter or contract.
- "other" — anything else at all.

"other" is the correct answer far more often than the other three, and \
choosing it is never a failure. In particular these are all "other":

- an automated "we received your application" acknowledgement
- a request to create an account, verify an email, or complete a profile
- a newsletter, marketing email, event invitation or job alert
- a recruiter cold-approaching about a different role the person did not apply for
- anything you cannot tell from the subject and preview alone

Confidence is your probability that the label is right, from 0.0 to 1.0. Use \
the full range. A subject that plainly says "Update on your application" \
without saying which way it went is a genuine 0.4, and reporting it as 0.9 \
makes the whole queue useless. Be decisive when the subject is decisive.

Respond with JSON only, no prose, in exactly this shape:
{"classification": "rejection", "confidence": 0.9, "reason": "six words"}"""

USER_PROMPT = """From: {sender_name} <{sender}>
Subject: {subject}

Preview: {snippet}"""

# Below this, the label is not worth acting on and the email is recorded as
# `other` instead. A suggestion nobody should trust is noise in the queue.
MIN_CONFIDENCE = 0.35


@dataclass(frozen=True)
class Classification:
    """What the model concluded about one email."""

    label: str
    confidence: float
    reason: str

    @property
    def suggested_status(self) -> str | None:
        """The status this label suggests, or ``None`` for ``other``."""
        return SUGGESTED_STATUS.get(self.label)


UNCLASSIFIED = Classification(label="other", confidence=0.0, reason="not classified")


def build_prompt(message: EmailMessage) -> str:
    """Assemble the user message for one email."""
    return USER_PROMPT.format(
        sender_name=message.sender_name or "(no name)",
        sender=message.sender or "(no address)",
        subject=message.subject or "(no subject)",
        snippet=message.snippet or "(no preview)",
    )


def call_model(settings: Settings, prompt: str) -> str:
    """Ask the model to classify one email. The seam the tests replace."""
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.require_anthropic_key())
    message = client.messages.create(
        model=settings.classifier_model,
        max_tokens=200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in message.content if block.type == "text").strip()


def classify(settings: Settings, message: EmailMessage) -> Classification:
    """Classify one email, degrading to ``other`` rather than raising.

    A model that returns something unparseable should cost one email its
    suggestion, not take down a sync of two hundred.
    """
    try:
        raw = call_model(settings, build_prompt(message))
    except Exception as exc:  # noqa: BLE001 - one bad email must not end the run
        log.warning("classifying %s failed: %s", message.message_id, exc)
        return Classification("other", 0.0, f"classification failed: {type(exc).__name__}")

    return parse_response(raw)


def parse_response(raw: str) -> Classification:
    """Read the model's JSON, falling back to ``other`` if it is unusable."""
    payload = _extract_json(raw)
    if payload is None:
        log.warning("classifier did not return JSON: %r", raw[:200])
        return Classification("other", 0.0, "unparseable response")

    label = str(payload.get("classification") or "").strip().lower()
    if label not in CLASSIFICATIONS:
        log.warning("classifier returned unknown label %r", label)
        return Classification("other", 0.0, f"unknown label {label!r}")

    try:
        confidence = float(payload.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))

    reason = str(payload.get("reason") or "").strip()[:200]

    # A label the model itself is unsure of is not worth showing as a
    # suggestion, so it is recorded as `other` with the confidence intact.
    if label != "other" and confidence < MIN_CONFIDENCE:
        return Classification("other", confidence, f"low confidence for {label}: {reason}")

    return Classification(label, confidence, reason)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull a JSON object out of a model response, fenced or not."""
    for attempt in (text, text[text.find("{") : text.rfind("}") + 1]):
        if not attempt:
            continue
        try:
            parsed = json.loads(attempt)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
