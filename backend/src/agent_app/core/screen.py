"""Reading the result list back before anyone is shown it.

Retrieval ranks by similarity, and similarity is not relevance. "Quantitative
Research Intern" at a trading desk sits high in a search for "ML research
internship" because it genuinely is quantitative research written in the same
vocabulary -- it is simply a different job. No amount of tuning ``rrf_k`` fixes
that, because nothing in a score knows what kind of work the person wants.

So one model call reads the candidate list against the request and says which
rows are a different kind of job. One call per search, on the cheap model,
against titles and a short excerpt.

Three rules the prompt works hard at, each protecting against a way this goes
wrong:

* **Keeping is the default.** A screen that prunes aggressively is worse than
  no screen at all: a missed posting is invisible, so its cost never shows up
  anywhere the person can see it. Dropping needs confidence; ambiguity does
  not.
* **It judges subject matter, nothing else.** Level, location and company are
  filters, and the retrieval layer already applied them. A screen that also
  second-guesses those is two systems disagreeing about the same question.
* **A reason that names the job.** "Not relevant" explains nothing. "A trading
  desk's quant research, not ML" is the sentence that lets the person see the
  screen was right -- or catch it being wrong.

Nothing here is permanent. A dropped posting is never recorded as ``found``,
so it stays undecided and comes back in the next search; see
:func:`agent_app.core.tools.find_postings`. This module removes rows from one
list, not from the corpus.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from ..config import Settings, get_settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are screening job postings a search returned, against \
what the person actually asked for.

Search ranks by similarity, and similar wording is not the same job. Your one \
job is to spot rows that are a different *kind of work* from the request, \
however well they match on words.

Keep is the default, and by a wide margin. Drop a posting only when you are \
confident it is a different kind of job. Two things follow from that:

- A vague or generic title is not a reason to drop. "Research Intern" with \
nothing else to go on is a keep.
- Being a weaker match is not a reason to drop. You are not reordering the \
list, and the bottom of a good list is still a good list.

Some of the request has already been applied as a database filter before \
the search ran, and the message below says which. Never re-decide those: a \
posting reached you because it passed them, and second-guessing a filter \
just makes two systems disagree about the same question. Everything the \
filters did not cover is yours to judge -- if the person asked for an \
internship and no level filter was applied, a role wanting eight years of \
experience is a different job and you should drop it.

How strict to be follows from how specific the request is, and this is the \
judgement that matters most. A request naming a kind of work -- "machine \
learning research internship" -- lets you drop a role doing different work. \
A request naming a whole field -- "AI internships in Europe", "robotics" -- \
is asking to see the field, and almost everything in it belongs. Drop from a \
broad request only what is plainly outside the field altogether.

Clear reasons to drop, when the evidence supports them:

- The role is in a different field that shares vocabulary with the request. A \
quantitative trading or quant finance role is not a machine learning research \
role, though both are "quantitative research". Lab automation is not robotics \
research. Data analytics is not data science research.
- The role is a different function at a relevant company: recruiting, sales, \
marketing, IT support or finance at an AI lab is not an AI role.
- The posting is not really a job: a talent pool, a general application, an \
event or a newsletter signup.

For each row you drop, give a reason of at most eight words that names what \
the job actually is. The person reads these to check your work.

An empty drop list is a good answer and the most common one.

Respond with JSON only, no prose, in exactly this shape:
{"drop": [{"n": 4, "why": "quant trading desk, not ML research"}]}"""

USER_PROMPT = """The person asked for: {request}

Already applied as filters, so do not re-decide these: {applied}

Candidates:

{candidates}"""

# What to say when the search ran with no constraints at all. Naming the empty
# case beats an empty line, which reads as a truncated prompt.
NO_FILTERS = "nothing - every constraint in the request is yours to judge"

# How many rows to put in front of the model. Beyond this the prompt gets
# expensive without getting better, and a list this long was already a sign
# the search was too broad.
MAX_CANDIDATES = 60

# Excerpts are the matching chunk, not a job summary, so a long one is mostly
# boilerplate. This is enough to tell a trading desk from a research group.
EXCERPT_CHARS = 180

# The reply is one JSON row per *dropped* posting, so its length scales with
# how much the screen throws away -- and the worst case, everything dropped, is
# the case a fixed ceiling silently breaks. A flat 1000 was enough while
# testing at `limit=10` and truncated at the default `limit=30`, where the pool
# is 60: the answer came back cut mid-string, failed to parse, and the whole
# screen fell back to the unscreened list. Nothing looked wrong from outside.
#
# 45 tokens a row covers a pretty-printed entry with an eight-word reason and
# leaves room for the fences the model likes to add. Unused output tokens cost
# nothing, so this is generous on purpose.
TOKENS_PER_ROW = 45
TOKENS_OVERHEAD = 300


def token_budget(count: int) -> int:
    """Enough output tokens for a verdict that drops every candidate."""
    return TOKENS_PER_ROW * count + TOKENS_OVERHEAD


@dataclass(frozen=True)
class Screening:
    """What the screen concluded about one candidate list.

    ``ran`` is the honest bit. ``dropped == {}`` means two very different
    things -- the model read the list and kept all of it, or the screen never
    happened because there is no API key -- and a caller that reports "screened
    60, showed 60" when nothing was screened is lying in the trace.
    """

    dropped: dict[int, str]
    ran: bool


NOT_RUN = Screening(dropped={}, ran=False)


def render_candidates(candidates: list[dict[str, Any]]) -> str:
    """One numbered line per candidate: what it is, and what matched.

    Numbers rather than posting ids. An id is a dozen opaque tokens the model
    has to copy back exactly, and a single wrong character silently drops the
    wrong row; an index is one token and an out-of-range answer is detectable.
    """
    lines = []
    for position, candidate in enumerate(candidates, start=1):
        company = candidate.get("company") or "(unknown company)"
        title = candidate.get("title") or "(untitled)"
        line = f"{position}. {company} - {title}"
        excerpt = (candidate.get("excerpt") or "").strip().replace("\n", " ")
        if excerpt:
            line += f"\n   {excerpt[:EXCERPT_CHARS]}"
        lines.append(line)
    return "\n".join(lines)


def build_prompt(request: str, candidates: list[dict[str, Any]], applied: str = "") -> str:
    """Assemble the user message for one candidate list."""
    return USER_PROMPT.format(
        request=request.strip() or "(no request given)",
        applied=applied.strip() or NO_FILTERS,
        candidates=render_candidates(candidates),
    )


def call_model(settings: Settings, prompt: str, count: int) -> str:
    """Ask the model to screen one list. The seam the tests replace."""
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.require_anthropic_key())
    message = client.messages.create(
        model=settings.screen_model,
        max_tokens=token_budget(count),
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    if message.stop_reason == "max_tokens":
        # Worth its own line. Truncated JSON reaches `parse_response` as
        # unparseable prose, which reports "the model did not answer in JSON"
        # -- true, and completely the wrong thing to go and investigate.
        log.warning(
            "screen hit its %d token ceiling on %d candidates and was cut off",
            token_budget(count),
            count,
        )
    return "".join(block.text for block in message.content if block.type == "text").strip()


def screen(request: str, candidates: list[dict[str, Any]], applied: str = "") -> Screening:
    """Judge a candidate list, degrading to keeping everything rather than raising.

    A search that returns a slightly noisy list is useful. A search that raises
    because the screening model timed out is not, so every failure here ends as
    :data:`NOT_RUN` and the caller shows the unscreened list.

    ``applied`` names the constraints the database already enforced. Without it
    the prompt has to either forbid judging seniority and place -- and then a
    search that passed no level filter shows senior roles for an internship
    request -- or allow it, and then the screen re-decides questions SQL
    already answered. Saying which is which costs one line and removes both.
    """
    settings = get_settings()
    if not settings.screen_results or not candidates:
        return NOT_RUN
    if not settings.anthropic_api_key:
        # Not a warning. No key is the normal state under test and for anyone
        # using retrieval without the agent, and the caller degrades cleanly.
        log.debug("screening skipped: no ANTHROPIC_API_KEY")
        return NOT_RUN

    trimmed = candidates[:MAX_CANDIDATES]
    try:
        raw = call_model(settings, build_prompt(request, trimmed, applied), len(trimmed))
    except Exception as exc:  # noqa: BLE001 - a failed screen must not fail the search
        log.warning("screening failed, showing the unscreened list: %s", exc)
        return NOT_RUN

    return parse_response(raw, len(trimmed))


def parse_response(raw: str, count: int) -> Screening:
    """Read the model's JSON into index -> reason, ignoring anything unusable.

    Every way this can be malformed resolves towards keeping a posting, which
    is the direction a mistake should fall in.
    """
    payload = _extract_json(raw)
    if payload is None:
        log.warning("screen did not return JSON: %r", raw[:200])
        return NOT_RUN

    rows = payload.get("drop")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        log.warning("screen returned a non-list drop: %r", rows)
        return NOT_RUN

    dropped: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            number = int(row.get("n"))
        except (TypeError, ValueError):
            continue
        # The model numbers from 1, and an index outside the list it was shown
        # is a hallucinated row rather than a reason to drop a real one.
        index = number - 1
        if not 0 <= index < count:
            log.warning("screen dropped row %s, which it was not shown", number)
            continue
        dropped[index] = str(row.get("why") or "").strip()[:80] or "off-target"

    return Screening(dropped=dropped, ran=True)


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
