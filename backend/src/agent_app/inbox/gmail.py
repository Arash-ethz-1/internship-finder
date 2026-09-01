"""Read-only Gmail access: OAuth, token storage, and the two REST calls we need.

No Google client libraries. The Gmail REST API is two GETs and the OAuth
exchange is one POST, so ``httpx`` (already a dependency) plus the standard
library does the whole job, and the code that holds the user's mailbox
credentials stays small enough to read in one sitting.

**Scope is ``gmail.readonly`` and nothing else.** This module has no code path
that sends, deletes, labels or modifies anything, and it never asks for a
scope that would let it.

**Why not the device flow PLAN.md names.** Google's OAuth 2.0 flow for
limited-input devices is restricted to a fixed scope list — ``openid``,
``email``, ``profile``, two Drive scopes and two YouTube ones. ``gmail.readonly``
is not on it, so a device-flow implementation would fail at the authorisation
request. The flow used instead is the one Google documents for desktop apps: a
loopback redirect to ``127.0.0.1`` on an ephemeral port, with PKCE. It is
strictly better here anyway — the browser is on the same machine — and it
keeps every other property the plan asked for: read-only scope, a refresh
token stored in ``data/`` and gitignored, and nothing ever sent.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parseaddr, parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"

# Refresh a little early rather than racing the expiry on a slow request.
EXPIRY_SKEW_SECONDS = 120
DEFAULT_TIMEOUT = 30.0
# Gmail caps `maxResults` at 500; we ask for far fewer and page if needed.
PAGE_SIZE = 100


class GmailError(RuntimeError):
    """Gmail could not be reached, or refused."""


class NotAuthorised(GmailError):
    """There is no usable token. The user needs to run ``cli sync-email --login``."""


@dataclass(frozen=True)
class EmailMessage:
    """One message, reduced to the four things this app is allowed to look at.

    No body. PLAN.md is explicit that there is no reason to pull an entire
    inbox into a local database, and a subject plus Gmail's own snippet is
    enough for both matching and classification.
    """

    message_id: str
    sender: str  # the address, e.g. "no-reply@stripe.com"
    sender_name: str  # the display name, e.g. "Stripe Recruiting"
    subject: str
    snippet: str
    received_at: str | None  # UTC ISO-8601

    @property
    def domain(self) -> str:
        """The sender's domain, lowercased. Empty if the address is unparseable."""
        _, _, domain = self.sender.partition("@")
        return domain.strip().lower()


# --- token storage ---------------------------------------------------------


@dataclass
class Token:
    """What we keep between runs.

    The refresh token is the durable half and the only thing that must
    survive; the access token is cached purely to avoid a refresh round-trip
    on every command.
    """

    refresh_token: str
    access_token: str | None = None
    expires_at: float = 0.0
    scope: str = SCOPE

    def fresh(self) -> bool:
        """True if the cached access token is still good for a moment longer."""
        return bool(self.access_token) and time.time() < self.expires_at - EXPIRY_SKEW_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
        }


def load_token(path: Path) -> Token | None:
    """Read the stored token, or ``None`` if there is not one yet."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GmailError(
            f"{path} is not readable JSON: {exc}. Delete it and log in again."
        ) from exc
    refresh = raw.get("refresh_token")
    if not refresh:
        return None
    return Token(
        refresh_token=str(refresh),
        access_token=raw.get("access_token"),
        expires_at=float(raw.get("expires_at") or 0.0),
        scope=str(raw.get("scope") or SCOPE),
    )


def save_token(path: Path, token: Token) -> None:
    """Write the token, readable only by this user where the OS supports it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token.to_dict(), indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows does not implement this
        pass


# --- the authorisation flow ------------------------------------------------


class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches the one redirect Google sends back, then stops."""

    query: dict[str, list[str]] = {}

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        parsed = urllib.parse.urlparse(self.path)
        type(self).query = urllib.parse.parse_qs(parsed.query)
        body = (
            b"<!doctype html><meta charset=utf-8>"
            b"<title>Authorised</title>"
            b"<body style='font:14px system-ui;padding:3rem'>"
            b"<p>Gmail access granted, read-only.</p>"
            b"<p>You can close this tab and go back to the terminal.</p>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        """Silence the default stderr access log."""


def _pkce_pair() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return (verifier, challenge)


def authorize(
    client_id: str,
    client_secret: str | None,
    *,
    open_browser: bool = True,
    timeout: float = 300.0,
    transport: httpx.BaseTransport | None = None,
) -> Token:
    """Run the loopback OAuth flow and return a token carrying a refresh token.

    Blocks until the user finishes in their browser, or ``timeout`` passes.
    """
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)

    # Port 0 asks the OS for a free one, which is what Google's loopback
    # guidance recommends: no fixed port to collide with, and the redirect URI
    # is registered as "http://127.0.0.1" with any port allowed.
    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    server.timeout = timeout
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    _CallbackHandler.query = {}

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        # Without these two Google returns a refresh token only on the very
        # first consent, and a re-login after deleting the token file would
        # silently produce a credential that dies in an hour.
        "access_type": "offline",
        "prompt": "consent",
    }
    url = f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"

    print("Opening your browser to authorise read-only Gmail access.")
    print("If it does not open, paste this into a browser:\n")
    print(url + "\n")
    if open_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    try:
        server.handle_request()
    finally:
        server.server_close()

    query = _CallbackHandler.query
    if not query:
        raise GmailError(f"No response from Google within {timeout:.0f}s. Nothing was authorised.")
    if "error" in query:
        raise GmailError(f"Google refused the request: {query['error'][0]}")
    if query.get("state", [""])[0] != state:
        raise GmailError("The redirect carried the wrong state parameter; refusing to continue.")
    code = query.get("code", [""])[0]
    if not code:
        raise GmailError("The redirect carried no authorisation code.")

    payload = {
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    data = _post_token(payload, transport=transport)
    refresh = data.get("refresh_token")
    if not refresh:
        raise GmailError(
            "Google did not return a refresh token. Revoke this app's access at "
            "https://myaccount.google.com/permissions and log in again."
        )
    return Token(
        refresh_token=str(refresh),
        access_token=data.get("access_token"),
        expires_at=time.time() + float(data.get("expires_in") or 0),
        scope=str(data.get("scope") or SCOPE),
    )


def _post_token(
    payload: dict[str, str], *, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    """POST to Google's token endpoint and return the decoded response."""
    with httpx.Client(timeout=DEFAULT_TIMEOUT, transport=transport) as client:
        try:
            response = client.post(TOKEN_ENDPOINT, data=payload)
        except httpx.RequestError as exc:
            raise GmailError(f"Could not reach Google's token endpoint: {exc}") from exc

    if response.is_error:
        detail = response.text[:300]
        raise GmailError(f"Token exchange failed (HTTP {response.status_code}): {detail}")
    try:
        data = response.json()
    except ValueError as exc:
        raise GmailError(f"Google's token endpoint returned non-JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise GmailError("Google's token endpoint returned an unexpected shape")
    return data


# --- the API client --------------------------------------------------------


@dataclass
class GmailClient:
    """Read-only Gmail, with the access token refreshed on demand.

    ``transport`` exists so the tests can serve every response from an
    ``httpx.MockTransport`` and never touch the network.
    """

    client_id: str
    client_secret: str | None
    token: Token
    token_path: Path | None = None
    timeout: float = DEFAULT_TIMEOUT
    transport: httpx.BaseTransport | None = None

    def access_token(self) -> str:
        """Return a valid access token, refreshing it if the cached one is stale."""
        if self.token.fresh():
            return str(self.token.access_token)

        payload = {
            "client_id": self.client_id,
            "refresh_token": self.token.refresh_token,
            "grant_type": "refresh_token",
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret

        data = _post_token(payload, transport=self.transport)
        access = data.get("access_token")
        if not access:
            raise NotAuthorised(
                "Google would not refresh the access token. The stored credential has "
                "probably been revoked. Run: cli sync-email --login"
            )
        self.token.access_token = str(access)
        self.token.expires_at = time.time() + float(data.get("expires_in") or 0)
        # A rotated refresh token must be kept or the next run has to log in again.
        if data.get("refresh_token"):
            self.token.refresh_token = str(data["refresh_token"])
        if self.token_path is not None:
            save_token(self.token_path, self.token)
        return self.token.access_token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.access_token()}"}
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            try:
                response = client.get(f"{API_ROOT}{path}", params=params, headers=headers)
            except httpx.RequestError as exc:
                raise GmailError(f"Could not reach Gmail: {exc}") from exc

        if response.status_code in (401, 403):
            raise NotAuthorised(
                f"Gmail refused the request (HTTP {response.status_code}). The token may "
                "have been revoked, or the Gmail API is not enabled for this Google "
                "Cloud project. Run: cli sync-email --login"
            )
        if response.is_error:
            raise GmailError(f"Gmail returned HTTP {response.status_code}: {response.text[:300]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise GmailError(f"Gmail returned non-JSON: {exc}") from exc
        return data if isinstance(data, dict) else {}

    def search(self, query: str, *, limit: int = 200) -> list[str]:
        """Return message ids matching a Gmail search query, newest first."""
        ids: list[str] = []
        page_token: str | None = None

        while len(ids) < limit:
            params: dict[str, Any] = {
                "q": query,
                "maxResults": min(PAGE_SIZE, limit - len(ids)),
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._get("/messages", params)

            for message in payload.get("messages") or []:
                if isinstance(message, dict) and message.get("id"):
                    ids.append(str(message["id"]))

            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        return ids[:limit]

    def metadata(self, message_id: str) -> EmailMessage:
        """Fetch one message's headers and snippet. Never its body.

        ``format=metadata`` with an explicit header list is what keeps this
        honest: Gmail does not send the body, so there is no body to
        accidentally store.
        """
        payload = self._get(
            f"/messages/{message_id}",
            {
                "format": "metadata",
                "metadataHeaders": ["Subject", "From", "Date"],
            },
        )
        return parse_message(payload)


def parse_message(payload: dict[str, Any]) -> EmailMessage:
    """Turn one Gmail message resource into an :class:`EmailMessage`."""
    headers = {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in (payload.get("payload") or {}).get("headers") or []
        if isinstance(h, dict)
    }
    sender_name, sender = parseaddr(headers.get("from", ""))

    return EmailMessage(
        message_id=str(payload.get("id") or ""),
        sender=sender.strip().lower(),
        sender_name=sender_name.strip(),
        subject=headers.get("subject", "").strip(),
        snippet=str(payload.get("snippet") or "").strip(),
        received_at=_received_at(payload, headers.get("date")),
    )


def _received_at(payload: dict[str, Any], date_header: str | None) -> str | None:
    """Prefer Gmail's own receive timestamp; fall back to the Date header.

    ``internalDate`` is milliseconds since the epoch and is when Gmail actually
    received the message, which is more trustworthy than a Date header written
    by whatever sent it.
    """
    internal = payload.get("internalDate")
    if internal:
        try:
            moment = datetime.fromtimestamp(int(internal) / 1000, tz=UTC)
        except (TypeError, ValueError, OSError):
            pass
        else:
            return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    if date_header:
        try:
            parsed = parsedate_to_datetime(date_header)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    return None


def build_client(settings: Any, *, transport: httpx.BaseTransport | None = None) -> GmailClient:
    """Build a client from settings and the stored token.

    Raises :class:`NotAuthorised` if there is no token yet, which is the signal
    for the CLI to tell the user to log in rather than a failure to report.
    """
    client_id, client_secret = settings.require_google_client()
    token = load_token(settings.gmail_token_path)
    if token is None:
        raise NotAuthorised(
            f"No Gmail token at {settings.gmail_token_path}. Run: cli sync-email --login"
        )
    return GmailClient(
        client_id=client_id,
        client_secret=client_secret,
        token=token,
        token_path=settings.gmail_token_path,
        transport=transport,
    )
