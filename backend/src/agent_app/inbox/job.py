"""Running a mailbox sync from the dashboard without blocking it.

`PROGRESS.md` recorded a decision on 2026-09-01 that there would be no route
which syncs mail: "fetching mail is slow, network-bound and holds credentials;
it belongs to `cli sync-email`, not to a dashboard button that makes a page
load wait on Google." That reasoning was right about the mechanism and wrong
about the conclusion. What must not happen is a *request* waiting on Gmail. A
background job with a status endpoint has none of that problem, and the OAuth
loopback flow is a better fit for a browser than for a terminal.

So the rule is kept exactly where it mattered: nothing here holds a request
open, and nothing here changes an `applications` row. A sync still only ever
produces suggestions for the review queue.

One job at a time, in this process. That is not a limitation to work around --
two concurrent syncs would fetch the same messages, and the second would
discover that the first had already recorded them.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from ..db import now_iso
from .sync import InboxError, SyncReport, sync_email

log = logging.getLogger(__name__)


@dataclass
class JobState:
    """What the dashboard needs to render a sync that is happening elsewhere."""

    status: str = "idle"  # idle | running | done | error
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    report: dict[str, Any] | None = None

    @property
    def running(self) -> bool:
        return self.status == "running"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "report": self.report,
        }


def _report_dict(report: SyncReport) -> dict[str, Any]:
    return {
        "since": report.since,
        "found": report.found,
        "already_seen": report.already_seen,
        "fetched": report.fetched,
        "matched": report.matched,
        "unmatched": report.unmatched,
        "suggestions": report.suggestions,
        "by_label": dict(report.by_label),
        "skipped_reason": report.skipped_reason,
    }


class SyncJob:
    """A single background mailbox sync, and its last result.

    The lock guards the transition into `running` rather than the whole run, so
    asking for the status never waits behind a sync that is talking to Gmail.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state = JobState()

    @property
    def state(self) -> JobState:
        return self._state

    def start(self, *, include_sent: bool = False, limit: int | None = None) -> JobState:
        """Begin a sync, or raise if one is already going.

        The caller gets the state back immediately; the work happens on the
        thread. `include_sent` lifts the `-from:me` exclusion, which exists so
        your own application to a company is never read as that company's
        answer to it -- it is for testing with one mailbox, and the default
        stays off.
        """
        with self._lock:
            if self._state.running:
                raise InboxError("A mailbox sync is already running")
            self._state = JobState(status="running", started_at=now_iso())

        thread = threading.Thread(
            target=self._run,
            kwargs={"include_sent": include_sent, "limit": limit},
            name="mailbox-sync",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        return self._state

    def _run(self, *, include_sent: bool, limit: int | None) -> None:
        """The thread body. Every failure becomes state, never a traceback.

        A sync reaches Gmail, an OAuth endpoint and a model, so there are many
        ways for it to fail and none of them should take the API down or leave
        the job stuck reading `running` forever.
        """
        try:
            # A fresh connection: this is not the thread that serves requests,
            # and `runtime.get_db` hands out one per thread for that reason.
            from .. import runtime
            from ..config import get_settings
            from .gmail import build_client

            settings = get_settings()
            settings.ensure_dirs()
            client = build_client(settings)

            report = sync_email(
                runtime.get_db(),
                settings,
                client,
                include_sent=include_sent,
                **({"limit": limit} if limit is not None else {}),
            )
        except InboxError as exc:
            self._finish(status="error", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - a stuck job is worse
            log.exception("mailbox sync failed")
            self._finish(status="error", error=f"{type(exc).__name__}: {exc}")
        else:
            self._finish(status="done", report=_report_dict(report))
        finally:
            from .. import runtime

            runtime.close_db()

    def _finish(
        self,
        *,
        status: str,
        error: str | None = None,
        report: dict[str, Any] | None = None,
    ) -> None:
        self._state = JobState(
            status=status,
            started_at=self._state.started_at,
            finished_at=now_iso(),
            error=error,
            report=report,
        )


# One job per process, which is what "one sync at a time" means here.
JOB = SyncJob()
