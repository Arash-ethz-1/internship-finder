"""Deciding which application an email is about.

Companies almost never quote a posting id in a rejection email, so this is
fuzzy by necessity. The rule that keeps it honest is PLAN.md's: **an unmatched
email is stored with ``posting_id NULL`` rather than guessed at.** A wrong
match is worse than no match, because a wrong match is what lets a
misclassified email close the wrong door.

Two signals, in the order the plan gives them:

1. **The sender's domain** against the company. ``no-reply@stripe.com`` is
   strong evidence. Applicant tracking systems relay a lot of this mail, so a
   domain belonging to Greenhouse or Lever is discarded rather than matched —
   it identifies the ATS, not the employer.
2. **The company name** against the subject and the sender's display name.
   Weaker, and only consulted when the domain says nothing.

The universe of candidates is deliberately small: only companies the user has
actually applied to. An email cannot be about an application that does not
exist, and narrowing to a handful of companies is what makes matching on a
subject line viable at all.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from ..db import TRACKED_STATUSES

# Mail from these domains was relayed by an applicant tracking system, so the
# domain tells us the vendor and nothing about the employer. Matching on it
# would map every Greenhouse rejection onto whichever company happened to be
# called "Greenhouse".
ATS_DOMAINS = frozenset(
    {
        "greenhouse.io",
        "greenhouse-mail.io",
        "us.greenhouse-mail.io",
        "myworkday.com",
        "myworkdayjobs.com",
        "lever.co",
        "hire.lever.co",
        "ashbyhq.com",
        "ashbyhq.io",
        "smartrecruiters.com",
        "workable.com",
        "workablemail.com",
        "icims.com",
        "successfactors.com",
        "taleo.net",
        "jobvite.com",
        "bamboohr.com",
        "recruitee.com",
        "teamtailor-mail.com",
        "personio.de",
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
    }
)

# Public suffixes common enough here to be worth stripping so that
# "stripe.co.uk" reduces to "stripe" rather than "co".
_MULTIPART_SUFFIXES = ("co.uk", "com.au", "co.jp", "co.nz", "com.br", "co.in")

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Match:
    """What the matcher concluded about one email.

    ``posting_id`` is ``None`` whenever the evidence names a company but not a
    single posting — several open applications at the same employer is a real
    and common case, and picking one of them would be a guess.
    """

    posting_id: str | None
    company_guess: str | None
    reason: str


@dataclass(frozen=True)
class OpenApplication:
    """One posting the user has an application row for."""

    posting_id: str
    company: str
    title: str


def open_applications(conn: sqlite3.Connection) -> list[OpenApplication]:
    """Every posting with an application row, newest change first.

    Closed applications are included on purpose: a rejection can arrive after
    you have already marked something rejected, and an offer can follow an
    interview. Filtering by status here would drop the emails that matter most.

    ``found`` is the one exception, and excluding it is the whole reason
    :data:`agent_app.db.TRACKED_STATUSES` exists. A search can surface hundreds
    of postings the person never applied to; matching an email against those
    would let any message from Stripe be read as a reply to a Stripe job that
    was only ever looked at, and produce a rejection suggestion for an
    application that was never sent.
    """
    marks = ",".join("?" * len(TRACKED_STATUSES))
    return [
        OpenApplication(posting_id=row["posting_id"], company=row["company"], title=row["title"])
        for row in conn.execute(
            "SELECT a.posting_id, p.company, p.title FROM applications a "
            "JOIN postings p ON p.id = a.posting_id "
            f"WHERE a.status IN ({marks}) ORDER BY a.updated_at DESC",
            TRACKED_STATUSES,
        )
    ]


def normalise(text: str) -> str:
    """Reduce a name to comparable letters and digits: ``"Match Group"`` -> ``matchgroup``."""
    return _NON_ALNUM.sub("", text.strip().lower())


def domain_root(domain: str) -> str:
    """The distinctive label of a domain: ``careers.stripe.co.uk`` -> ``stripe``.

    Subdomains are dropped from the left and a known public suffix from the
    right; what is left is the part that identifies the organisation.
    """
    domain = domain.strip().lower().rstrip(".")
    if not domain:
        return ""
    for suffix in _MULTIPART_SUFFIXES:
        if domain.endswith("." + suffix):
            domain = domain[: -(len(suffix) + 1)]
            break
    else:
        domain = domain.rpartition(".")[0] or domain
    return domain.rpartition(".")[2] or domain


def is_ats_domain(domain: str) -> bool:
    """True if this domain belongs to an ATS or a consumer mail provider."""
    domain = domain.strip().lower()
    if not domain:
        return True
    if domain in ATS_DOMAINS:
        return True
    # A subdomain of a relay is still a relay: mail.hire.lever.co, and so on.
    return any(domain.endswith("." + known) for known in ATS_DOMAINS)


def match_email(
    sender_domain: str,
    subject: str,
    sender_name: str,
    applications: list[OpenApplication],
) -> Match:
    """Resolve one email against the applications, or decline to.

    Returns a :class:`Match` in every case; ``posting_id`` and
    ``company_guess`` are both ``None`` when nothing was recognised.
    """
    if not applications:
        return Match(None, None, "no applications to match against")

    haystack = f"{subject} {sender_name}".lower()

    # Signal 1: the sender's own domain.
    if not is_ats_domain(sender_domain):
        root = domain_root(sender_domain)
        if root:
            hits = [a for a in applications if _company_matches_root(a.company, root)]
            if hits:
                return _narrow(hits, subject, f"sender domain {sender_domain}")

    # Signal 2: the company name in the subject or the display name.
    named = [a for a in applications if _company_in_text(a.company, haystack)]
    if named:
        return _narrow(named, subject, "company name in the subject")

    return Match(None, None, "no company recognised")


def _narrow(hits: list[OpenApplication], subject: str, reason: str) -> Match:
    """Pick a posting among one company's applications, or name the company only."""
    company = hits[0].company

    if len({h.company for h in hits}) > 1:
        # Two different employers matched the same evidence. That is ambiguity,
        # not a match.
        return Match(None, None, f"{reason} matched more than one company")

    if len(hits) == 1:
        return Match(hits[0].posting_id, company, reason)

    # One company, several applications. The subject sometimes names the role.
    lowered = subject.lower()
    titled = [h for h in hits if h.title and h.title.lower() in lowered]
    if len(titled) == 1:
        return Match(titled[0].posting_id, company, f"{reason}, role named in the subject")

    return Match(
        None,
        company,
        f"{reason}, but {len(hits)} applications at {company} and nothing to tell them apart",
    )


def _company_matches_root(company: str, root: str) -> bool:
    """True if a company name plausibly owns a domain with this root label."""
    normalised = normalise(company)
    root = normalise(root)
    if not normalised or not root:
        return False
    if normalised == root:
        return True
    # "Stripe Inc" owns stripe.com; "airbnb" owns airbnb.com. Require the
    # shorter side to be at least four characters so "ab" does not match
    # everything.
    shorter, longer = sorted((normalised, root), key=len)
    return len(shorter) >= 4 and shorter in longer


def _company_in_text(company: str, haystack: str) -> bool:
    """True if the company name appears in the subject or display name.

    Matched on word boundaries against the raw text rather than the normalised
    form, so "Ramp" does not match "rampant" and a two-letter company name
    cannot match at all.
    """
    name = company.strip()
    if len(name) < 3:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", haystack) is not None
