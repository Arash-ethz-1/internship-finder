"""Application configuration.

Reads ``.env`` once and exposes a frozen :class:`Settings` object via
:func:`get_settings`.

API keys are deliberately *not* validated when settings load. ``cli ingest``
and the postings API must run with no keys at all, so a missing key is only an
error at the point it is actually needed. Call :meth:`Settings.require_anthropic_key`
or :meth:`Settings.require_voyage_key` there; they raise :class:`ConfigError`
with a message naming the variable and the file it belongs in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# config.py -> agent_app -> src -> backend -> project root
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent

DEFAULT_AGENT_MODEL = "claude-opus-5"
# Discovery just lists company names, so it does not need the agent's model.
DEFAULT_DISCOVERY_MODEL = "claude-sonnet-5"
# Classifying a subject line into four buckets is the cheapest judgement call
# in the app, and it runs once per candidate email.
DEFAULT_CLASSIFIER_MODEL = "claude-sonnet-5"
DEFAULT_EMBEDDING_MODEL = "voyage-3.5"
DEFAULT_EMBEDDING_DIM = 1024


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or unusable."""


@dataclass(frozen=True)
class Settings:
    """Resolved paths, credentials and model choices for one process."""

    project_root: Path
    backend_dir: Path
    data_dir: Path
    profile_dir: Path
    db_path: Path
    vectors_path: Path
    vectors_meta_path: Path
    embed_cache_dir: Path
    letters_dir: Path
    eval_dir: Path
    companies_path: Path
    gmail_token_path: Path

    anthropic_api_key: str | None
    voyage_api_key: str | None
    google_client_id: str | None
    google_client_secret: str | None

    agent_model: str
    discovery_model: str
    classifier_model: str
    embedding_model: str
    embedding_dim: int

    api_host: str
    api_port: int
    cors_origins: tuple[str, ...]

    user_agent: str

    def require_anthropic_key(self) -> str:
        """Return the Anthropic key, or explain exactly what to do about it."""
        if not self.anthropic_api_key:
            raise ConfigError(
                "ANTHROPIC_API_KEY is not set. Add it to "
                f"{self.backend_dir / '.env'} (see .env.example). "
                "Get a key at https://console.anthropic.com/settings/keys"
            )
        return self.anthropic_api_key

    def require_voyage_key(self) -> str:
        """Return the Voyage key, or explain exactly what to do about it."""
        if not self.voyage_api_key:
            raise ConfigError(
                "VOYAGE_API_KEY is not set. Add it to "
                f"{self.backend_dir / '.env'} (see .env.example). "
                "Get a key at https://dashboard.voyageai.com/api-keys"
            )
        return self.voyage_api_key

    def require_google_client(self) -> tuple[str, str | None]:
        """Return the Google OAuth client id and secret, or explain what to do.

        The secret is optional: a Desktop client created after 2022 has one,
        but it is not a secret in any meaningful sense on a machine the user
        controls, and PKCE is what actually protects the exchange.
        """
        if not self.google_client_id:
            raise ConfigError(
                "GOOGLE_CLIENT_ID is not set. Add it to "
                f"{self.backend_dir / '.env'} (see .env.example). Create an OAuth "
                "client of type 'Desktop app' at "
                "https://console.cloud.google.com/apis/credentials after enabling "
                "the Gmail API for the project."
            )
        return (self.google_client_id, self.google_client_secret)

    def ensure_dirs(self) -> None:
        """Create the runtime directories that are gitignored and so absent on a clone."""
        for path in (
            self.data_dir,
            self.profile_dir,
            self.embed_cache_dir,
            self.letters_dir,
            self.eval_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    """Build a :class:`Settings` from ``backend/.env`` plus the process environment."""
    load_dotenv(BACKEND_DIR / ".env", override=False)

    data_dir = Path(os.getenv("DATA_DIR") or PROJECT_ROOT / "data").resolve()
    profile_dir = Path(os.getenv("PROFILE_DIR") or PROJECT_ROOT / "profile").resolve()

    raw_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    origins = tuple(o.strip() for o in raw_origins.split(",") if o.strip())

    return Settings(
        project_root=PROJECT_ROOT,
        backend_dir=BACKEND_DIR,
        data_dir=data_dir,
        profile_dir=profile_dir,
        db_path=data_dir / "postings.db",
        vectors_path=data_dir / "vectors.npy",
        # Records which model and dimension wrote vectors.npy, so switching
        # embedding providers fails loudly instead of silently mixing spaces.
        vectors_meta_path=data_dir / "vectors.meta.json",
        embed_cache_dir=data_dir / "embed_cache",
        letters_dir=data_dir / "letters",
        eval_dir=data_dir / "eval",
        companies_path=BACKEND_DIR / "companies.toml",
        # The Gmail refresh token. Lives in data/, which is gitignored whole.
        gmail_token_path=data_dir / "gmail_token.json",
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        voyage_api_key=os.getenv("VOYAGE_API_KEY") or None,
        google_client_id=os.getenv("GOOGLE_CLIENT_ID") or None,
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET") or None,
        agent_model=os.getenv("AGENT_MODEL", DEFAULT_AGENT_MODEL),
        discovery_model=os.getenv("DISCOVERY_MODEL", DEFAULT_DISCOVERY_MODEL),
        classifier_model=os.getenv("CLASSIFIER_MODEL", DEFAULT_CLASSIFIER_MODEL),
        embedding_model=os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        embedding_dim=int(os.getenv("EMBEDDING_DIM", str(DEFAULT_EMBEDDING_DIM))),
        api_host=os.getenv("API_HOST", "127.0.0.1"),
        api_port=int(os.getenv("API_PORT", "8000")),
        cors_origins=origins,
        user_agent=os.getenv(
            "USER_AGENT",
            "internship-agent/0.1 (personal job search; +https://github.com/Arash-ethz-1/internship-finder)",
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, loading them on first use."""
    return load_settings()


def reset_settings() -> None:
    """Drop the cached settings. Tests use this after changing the environment."""
    get_settings.cache_clear()
