"""Reading application replies out of Gmail, read-only, and suggesting statuses.

Phase 10. A sibling of :mod:`agent_app.ingest` rather than part of it: both
pull from an external source, but this one has its own OAuth, its own vendor,
and a rule the job boards do not have — **it only ever suggests.** Nothing in
here writes an application status except :func:`accept_suggestion`, which runs
when a person clicks accept.
"""

# NB: the `classify` *function* is deliberately not re-exported here. Binding
# it as a package attribute would shadow the `classify` submodule, so
# `from agent_app.inbox import classify` would hand you a function where a
# module was meant. Import it from `.classify` if you need it directly.
from .classify import Classification, parse_response
from .gmail import (
    SCOPE,
    EmailMessage,
    GmailClient,
    GmailError,
    NotAuthorised,
    Token,
    authorize,
    build_client,
    load_token,
    save_token,
)
from .match import Match, OpenApplication, match_email, open_applications
from .sync import (
    InboxError,
    SyncReport,
    accept_suggestion,
    dismiss_suggestion,
    get_suggestion,
    list_suggestions,
    pending_count,
    sync_email,
)

__all__ = [
    "SCOPE",
    "Classification",
    "EmailMessage",
    "GmailClient",
    "GmailError",
    "InboxError",
    "Match",
    "NotAuthorised",
    "OpenApplication",
    "SyncReport",
    "Token",
    "accept_suggestion",
    "authorize",
    "build_client",
    "dismiss_suggestion",
    "get_suggestion",
    "list_suggestions",
    "load_token",
    "match_email",
    "open_applications",
    "parse_response",
    "pending_count",
    "save_token",
    "sync_email",
]
