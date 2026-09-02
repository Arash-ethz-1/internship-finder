"""Fetch, match, classify — and then stop.

The three steps are separate because each fails differently: the network can
be down, a company can be unrecognisable, and a subject line can be genuinely
ambiguous. Running them as one function would mean a matching failure looked
like a fetch failure.

**The rule this module exists to enforce: nothing is applied automatically.**
:func:`sync_email` never writes to ``applications`` or ``status_history``. It
only ever inserts into ``email_matches``, where each row is a suggestion
waiting for a person. :func:`accept_suggestion` is the only function here that
changes an application, and it runs when the user clicks accept.

PLAN.md gives the reason, and it is worth restating where the code is: a
wrongly auto-applied ``rejected`` is worse than no automation at all, because
you stop checking a company that actually wanted to interview you. Every
accepted suggestion writes a ``status_history`` note naming the email it came
from, so an automated mistake is always traceable to its source.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..config import Settings
from ..db import STATUSES, TRACKED_STATUSES, now_iso
from .classify import UNCLASSIFIED, Classification, classify
from .gmail import EmailMessage, GmailClient
from .match import Match, match_email, open_applications

log = logging.getLogger(__name__)

# Gmail search terms shared by every sync. Chats are not email, and promotions
# and social are where newsletters live — a rejection never lands there, and
# excluding them keeps the classifier's bill down.
QUERY_EXCLUSIONS = "-in:chats -category:promotions -category:social -from:me"

# Look back a little further than the earliest application, because a reply
# can arrive before the status is recorded by hand.
LOOKBACK_DAYS = 3

DEFAULT_LIMIT = 200


class InboxError(RuntimeError):
    """A suggestion could not be applied."""


@dataclass
class SyncReport:
    """What one sync run did. Deliberately counts what it did *not* do."""

    since: str | None = None
    found: int = 0  # message ids Gmail returned
    already_seen: int = 0  # skipped: this message was synced before
    fetched: int = 0  # metadata actually pulled
    matched: int = 0  # resolved to a posting
    unmatched: int = 0  # stored with posting_id NULL
    suggestions: int = 0  # rows with an actionable suggested_status
    by_label: dict[str, int] = field(default_factory=dict)
    skipped_reason: str | None = None

    def format(self) -> str:
        if self.skipped_reason:
            return self.skipped_reason
        lines = [
            f"searched since     : {self.since}",
            f"messages found     : {self.found}",
            f"already synced     : {self.already_seen}",
            f"newly examined     : {self.fetched}",
            f"matched to posting : {self.matched}",
            f"unmatched          : {self.unmatched}",
        ]
        if self.by_label:
            lines.append("")
            lines.append("classified as:")
            for label, count in sorted(self.by_label.items(), key=lambda kv: -kv[1]):
                lines.append(f"  {label:12} {count}")
        lines.append("")
        lines.append(f"{self.suggestions} suggestion(s) waiting for review.")
        lines.append("No application was changed. Accept them in the dashboard, /inbox.")
        return "\n".join(lines)


def earliest_application(conn: sqlite3.Connection) -> str | None:
    """The oldest ``applications.updated_at``, or ``None`` if there are none.

    This is the horizon PLAN.md specifies: there is no point reading mail from
    before the first application was recorded.
    """
    marks = ",".join("?" * len(TRACKED_STATUSES))
    row = conn.execute(
        f"SELECT min(updated_at) AS earliest FROM applications WHERE status IN ({marks})",
        TRACKED_STATUSES,
    ).fetchone()
    return row["earliest"] if row and row["earliest"] else None


def build_query(since: str, *, lookback_days: int = LOOKBACK_DAYS) -> str:
    """Build the Gmail search query for everything since a UTC ISO-8601 stamp."""
    try:
        moment = datetime.strptime(since[:10], "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        moment = datetime.now(UTC) - timedelta(days=30)
    moment -= timedelta(days=lookback_days)
    return f"after:{moment.strftime('%Y/%m/%d')} {QUERY_EXCLUSIONS}"


def known_message_ids(conn: sqlite3.Connection) -> set[str]:
    """Every Gmail id already recorded. This is what makes re-runs idempotent."""
    return {row["message_id"] for row in conn.execute("SELECT message_id FROM email_matches")}


def sync_email(
    conn: sqlite3.Connection,
    settings: Settings,
    client: GmailClient,
    *,
    limit: int = DEFAULT_LIMIT,
    classify_fn: object = None,
) -> SyncReport:
    """Fetch candidate emails, match and classify them, and store suggestions.

    Writes only to ``email_matches``. ``applications`` and ``status_history``
    are not touched — verified by a test, because it is the one property of
    this phase that matters.
    """
    report = SyncReport()

    since = earliest_application(conn)
    if since is None:
        report.skipped_reason = (
            "No applications yet, so there is nothing for an email to be about.\n"
            "Mark a posting as applied first, in the dashboard or with the agent."
        )
        return report

    report.since = since
    applications = open_applications(conn)
    seen = known_message_ids(conn)
    classifier = classify_fn if classify_fn is not None else classify

    message_ids = client.search(build_query(since), limit=limit)
    report.found = len(message_ids)

    for message_id in message_ids:
        if message_id in seen:
            report.already_seen += 1
            continue

        message = client.metadata(message_id)
        report.fetched += 1

        match = match_email(message.domain, message.subject, message.sender_name, applications)
        # Classifying mail from a company that was never applied to is money
        # spent to learn nothing, so an unrecognised sender skips the model.
        if match.company_guess is None:
            result = UNCLASSIFIED
        else:
            result = classifier(settings, message)

        record_match(conn, message, match, result)

        report.by_label[result.label] = report.by_label.get(result.label, 0) + 1
        if match.posting_id:
            report.matched += 1
        else:
            report.unmatched += 1
        if result.suggested_status and match.posting_id:
            report.suggestions += 1

        log.info(
            "%s: %s (%.2f) -> %s [%s]",
            message_id,
            result.label,
            result.confidence,
            match.posting_id or "unmatched",
            match.reason,
        )

    return report


def record_match(
    conn: sqlite3.Connection,
    message: EmailMessage,
    match: Match,
    result: Classification,
) -> None:
    """Insert one suggestion. Ignores a message already recorded.

    ``ON CONFLICT DO NOTHING`` rather than an upsert: re-syncing must never
    overwrite a row the user has already accepted or dismissed.
    """
    with conn:
        conn.execute(
            """
            INSERT INTO email_matches (
                message_id, posting_id, company_guess, sender, received_at,
                subject, snippet, classification, confidence, suggested_status,
                applied, dismissed, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
            ON CONFLICT(message_id) DO NOTHING
            """,
            (
                message.message_id,
                match.posting_id,
                match.company_guess,
                message.sender,
                message.received_at,
                message.subject,
                message.snippet,
                result.label,
                result.confidence,
                result.suggested_status,
                now_iso(),
            ),
        )


# --- reading suggestions back out ------------------------------------------


def list_suggestions(
    conn: sqlite3.Connection,
    *,
    pending_only: bool = True,
    actionable_only: bool = False,
    classification: str | None = None,
    min_confidence: float = 0.0,
) -> list[sqlite3.Row]:
    """The review queue, most confident first.

    ``actionable_only`` drops the ``other`` classifications, which carry no
    suggested status. ``classification`` narrows to one kind of message, and
    ``min_confidence`` drops the ones the model was unsure about.

    All three default to off. A queue that silently hides what the classifier
    read is a queue you cannot learn to trust, so the narrowing is a choice the
    caller makes and the dashboard shows how many rows it is holding back.
    """
    where = []
    params: list[Any] = []
    if pending_only:
        where.append("e.applied = 0 AND e.dismissed = 0")
    if actionable_only:
        where.append("e.suggested_status IS NOT NULL")
    if classification:
        where.append("e.classification = ?")
        params.append(classification)
    if min_confidence > 0:
        # Confidence is nullable; an unclassified row has nothing to compare.
        where.append("coalesce(e.confidence, 0) >= ?")
        params.append(min_confidence)
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    return conn.execute(
        "SELECT e.*, p.company, p.title, p.url, a.status AS current_status "
        "FROM email_matches e "
        "LEFT JOIN postings p ON p.id = e.posting_id "
        "LEFT JOIN applications a ON a.posting_id = e.posting_id"
        f"{clause} "
        "ORDER BY e.suggested_status IS NULL, e.confidence DESC, e.received_at DESC",
        params,
    ).fetchall()


def get_suggestion(conn: sqlite3.Connection, match_id: int) -> sqlite3.Row:
    """One suggestion by id, or ``KeyError``."""
    row = conn.execute("SELECT * FROM email_matches WHERE id = ?", (match_id,)).fetchone()
    if row is None:
        raise KeyError(f"No email suggestion with id {match_id}")
    return row


# --- the one function here that changes an application ---------------------


def accept_suggestion(
    conn: sqlite3.Connection,
    match_id: int,
    *,
    posting_id: str | None = None,
    status: str | None = None,
) -> dict[str, object]:
    """Apply one suggestion: set the status and record where it came from.

    ``posting_id`` attaches an unmatched suggestion to a posting the user
    picked — the matcher declines to guess, so this is how an unmatched email
    becomes actionable. ``status`` overrides the suggested one, because the
    person reading the email is a better classifier than the model.

    Everything happens in one transaction: the application, the history row
    naming the email, and the flag that stops this suggestion being offered
    again.
    """
    row = get_suggestion(conn, match_id)

    if row["applied"]:
        raise InboxError(f"Suggestion {match_id} has already been accepted")

    target = posting_id or row["posting_id"]
    if not target:
        raise InboxError(
            f"Suggestion {match_id} is not matched to a posting"
            + (f" (it looks like {row['company_guess']})" if row["company_guess"] else "")
            + ". Choose the posting it belongs to before accepting it."
        )

    new_status = status or row["suggested_status"]
    if not new_status:
        raise InboxError(
            f"Suggestion {match_id} was classified as {row['classification']!r}, which "
            "suggests no status change. Choose a status explicitly to apply one anyway."
        )
    if new_status not in STATUSES:
        raise ValueError(f"Unknown status {new_status!r}. Allowed: {', '.join(STATUSES)}")

    if conn.execute("SELECT 1 FROM postings WHERE id = ?", (target,)).fetchone() is None:
        raise KeyError(f"No posting with id {target!r}")

    previous = conn.execute(
        "SELECT status FROM applications WHERE posting_id = ?", (target,)
    ).fetchone()
    from_status = previous["status"] if previous else None
    changed_at = now_iso()
    note = history_note(row)

    with conn:
        conn.execute(
            "INSERT INTO applications (posting_id, status, note, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(posting_id) DO UPDATE SET "
            "status = excluded.status, note = excluded.note, updated_at = excluded.updated_at",
            (target, new_status, note, changed_at),
        )
        conn.execute(
            "INSERT INTO status_history (posting_id, from_status, to_status, note, changed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (target, from_status, new_status, note, changed_at),
        )
        conn.execute(
            "UPDATE email_matches SET applied = 1, dismissed = 0, posting_id = ? WHERE id = ?",
            (target, match_id),
        )

    return {
        "id": match_id,
        "posting_id": target,
        "from_status": from_status,
        "status": new_status,
        "note": note,
        "updated_at": changed_at,
    }


def dismiss_suggestion(conn: sqlite3.Connection, match_id: int) -> dict[str, object]:
    """Reject one suggestion. Writes nothing but the flag.

    A dismissed suggestion stays in the table rather than being deleted, so a
    re-sync does not offer the same email again and the record of what the
    classifier proposed survives.
    """
    row = get_suggestion(conn, match_id)
    if row["applied"]:
        raise InboxError(f"Suggestion {match_id} was already accepted and cannot be dismissed")

    with conn:
        conn.execute("UPDATE email_matches SET dismissed = 1 WHERE id = ?", (match_id,))
    return {"id": match_id, "dismissed": True}


def history_note(row: sqlite3.Row) -> str:
    """The ``status_history`` note for an accepted suggestion.

    It names the email, so a wrong automated suggestion is always traceable to
    the message that caused it. That traceability is the thing that makes
    accepting a model's judgement safe.
    """
    subject = (row["subject"] or "(no subject)").strip()
    if len(subject) > 90:
        subject = subject[:87] + "..."
    sender = row["sender"] or "unknown sender"
    return f'from email: "{subject}" — {sender} (gmail:{row["message_id"]})'


def pending_count(conn: sqlite3.Connection) -> int:
    """How many suggestions are waiting. Used by the CLI's ``status`` output."""
    return conn.execute(
        "SELECT count(*) FROM email_matches WHERE applied = 0 AND dismissed = 0 "
        "AND suggested_status IS NOT NULL"
    ).fetchone()[0]
