"""Where candidate companies come from.

Three sources, one shape out. None of them is trusted: a candidate is a guess
until :mod:`agent_app.ingest.discovery` gets a 200 from the board.

* **crawl** — the Common Crawl URL index. Greenhouse and Lever and Ashby all
  publish per-company endpoints and no directory, so there is nothing to
  enumerate against officially. Common Crawl has already walked the web and
  exposes a documented index API, which makes it a de-facto directory: one
  query for ``boards.greenhouse.io/*`` returns thousands of real board URLs.
  Free, no key, and it knows the exact token.
* **llm** — one Claude call for company *names* matching a description. The
  only source that can target a niche ("robotics companies in Zurich"). Ask
  for names, never tokens: a model knows which companies exist but its
  knowledge of which ATS they use goes stale as companies migrate.
* **file** — one name per line. Keeps the pipeline usable with no API key.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings

log = logging.getLogger(__name__)

COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"

# Where each board's tokens appear in a URL path.
CRAWL_PATTERNS: dict[str, tuple[str, ...]] = {
    "greenhouse": ("boards.greenhouse.io/*", "job-boards.greenhouse.io/*"),
    # jobs.lever.co is disallowed in robots.txt so Common Crawl never stored
    # it; the EU host is indexed and is exactly the half we were missing.
    "lever": ("jobs.eu.lever.co/*", "jobs.lever.co/*"),
    "ashby": ("jobs.ashbyhq.com/*",),
}

# Path segments that are plainly not companies.
NOT_A_TOKEN = frozenset(
    {
        "robots.txt",
        "favicon.ico",
        "embed",
        "api",
        "static",
        "assets",
        "blog",
        "privacy",
        "terms",
        "search",
        "jobs",
        "index.html",
        "sitemap.xml",
        "providers",
        "customers",
        "pricing",
        "about",
        "login",
        "signup",
    }
)

_TOKEN_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")
_SLUG_STRIP = re.compile(r"[^a-z0-9\s-]")
# Removed rather than replaced with a space, so "Jerry's" stays one word.
_APOSTROPHES = re.compile(r"['’`]")
_LEGAL_SUFFIXES = {
    "inc",
    "inc.",
    "llc",
    "ltd",
    "ltd.",
    "limited",
    "gmbh",
    "ag",
    "sa",
    "sas",
    "bv",
    "nv",
    "plc",
    "corp",
    "corporation",
    "co",
    "company",
    "holdings",
    "group",
}


@dataclass(frozen=True)
class Candidate:
    """Something that might be a company board.

    ``token`` is set when the source knows it exactly (crawl). ``name`` is set
    when the source knows the company but not the token (llm, file). ``source``
    is set when the board is known; ``None`` means try all three.
    """

    origin: str  # crawl | llm | file
    token: str | None = None
    name: str | None = None
    source: str | None = None


def slug_candidates(name: str, limit: int = 5) -> list[str]:
    """Derive plausible board tokens from a company name.

    Tokens are almost always a slug of the name, so this turns a hallucination
    problem into a small search with a cheap oracle: ``"Match Group"`` gives
    ``matchgroup``, ``match``, ``match-group``, one of which is real.

    Both the full name and a suffix-stripped version are kept. Dropping a
    trailing "Group" or "GmbH" often helps ("Acme GmbH" -> ``acme``) but not
    always: Match Group's real Lever token is ``matchgroup``, so stripping the
    suffix and stopping there would lose the company entirely. An extra
    candidate costs one request; a missed company costs the company.
    """
    cleaned = _APOSTROPHES.sub("", name.strip().lower())
    cleaned = _SLUG_STRIP.sub(" ", cleaned)
    words = [w for w in cleaned.split() if w]
    if not words:
        return []

    stripped = list(words)
    while len(stripped) > 1 and stripped[-1] in _LEGAL_SUFFIXES:
        stripped.pop()

    ordered = (
        "".join(words),
        "".join(stripped),
        "-".join(words),
        "-".join(stripped),
        words[0],
    )

    out: list[str] = []
    for slug in ordered:
        if slug and slug not in out and _TOKEN_OK.match(slug):
            out.append(slug)
    return out[:limit]


def token_from_url(url: str) -> str | None:
    """Pull the board token out of a crawled URL, or ``None`` if it is not one."""
    path = url.split("://", 1)[-1]
    path = path.split("/", 1)[1] if "/" in path else ""
    path = path.split("?", 1)[0].split("#", 1)[0].strip("/")
    if not path:
        return None
    first = path.split("/")[0]
    if first.lower() in NOT_A_TOKEN or not _TOKEN_OK.match(first):
        return None
    return first


def from_crawl(
    client: Any,
    sources: tuple[str, ...],
    limit_per_pattern: int = 20000,
) -> list[Candidate]:
    """Enumerate board tokens from the Common Crawl URL index.

    ``client`` is a :class:`~agent_app.ingest.runner.PoliteClient`. The index
    is a free public service and drops connections under load, so it gets the
    same retry treatment as the job boards rather than a bare request.
    """
    collections = client.get_json(COLLINFO_URL)
    if not collections:
        raise RuntimeError("Common Crawl returned no collections")
    cdx_api = collections[0]["cdx-api"]
    log.info("using Common Crawl collection %s", collections[0].get("id"))

    seen: set[tuple[str, str]] = set()
    out: list[Candidate] = []

    for source in sources:
        for pattern in CRAWL_PATTERNS.get(source, ()):
            try:
                body = client.get_text(
                    cdx_api,
                    params={"url": pattern, "output": "json", "limit": str(limit_per_pattern)},
                )
            except Exception as exc:  # noqa: BLE001 - one dead pattern is not fatal
                log.warning("crawl index failed for %s: %s", pattern, exc)
                continue

            found = 0
            for line in body.strip().splitlines():
                try:
                    url = json.loads(line).get("url", "")
                except ValueError:
                    continue
                token = token_from_url(url)
                if token is None or (source, token) in seen:
                    continue
                seen.add((source, token))
                out.append(Candidate(origin="crawl", token=token, source=source))
                found += 1
            log.info("%s: %d tokens from %s", source, found, pattern)

    return out


PROMPT = """List {limit} real companies matching this description:

{query}

Rules:
- Real companies that exist today and publish a public careers page.
- Prefer companies likely to hire interns or new graduates.
- Return the company's common trading name, not its legal name.
- Do not guess which applicant tracking system they use unless you are sure.

Respond with JSON only, no prose, in exactly this shape:
{{"companies": [{{"name": "Example Corp", "likely_source": "greenhouse"}}]}}

"likely_source" must be one of "greenhouse", "lever", "ashby", or "unknown".
Use "unknown" freely - a wrong guess costs nothing because every candidate is
verified against the real API anyway."""


def from_llm(settings: Settings, query: str, limit: int = 50) -> list[Candidate]:
    """Ask Claude for company names matching a description.

    Names only. The model is a candidate generator, not a source of truth, and
    everything it returns is verified over HTTP before it is believed.
    """
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.require_anthropic_key())
    message = client.messages.create(
        model=settings.discovery_model,
        max_tokens=4096,
        messages=[{"role": "user", "content": PROMPT.format(limit=limit, query=query)}],
    )

    text = "".join(block.text for block in message.content if block.type == "text").strip()
    payload = _extract_json(text)
    if payload is None:
        raise ValueError(f"Model did not return usable JSON. First 200 chars: {text[:200]!r}")

    out: list[Candidate] = []
    for raw in payload.get("companies") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        source = str(raw.get("likely_source") or "").strip().lower()
        out.append(
            Candidate(
                origin="llm",
                name=name,
                source=source if source in CRAWL_PATTERNS else None,
            )
        )
    return out


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


def from_file(path: Path) -> list[Candidate]:
    """Read one company name per line. Blank lines and ``#`` comments skipped."""
    if not path.exists():
        raise FileNotFoundError(f"No candidate file at {path}")
    out: list[Candidate] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        out.append(Candidate(origin="file", name=name))
    return out
