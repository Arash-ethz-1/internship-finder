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

# A letter is the one thing here a person actually sends, under their own
# name, to someone deciding whether to interview them. It gets the strongest
# model of the four by default, and it is deliberately its own setting: it used
# to share `agent_model` with the chat loop, so turning the loop down to a
# cheap model while iterating silently turned the letters down too.
DEFAULT_LETTER_MODEL = "claude-sonnet-5"
# The chat loop. Cheap on purpose: it runs many turns per question, and its
# job is choosing tools and summarising results rather than writing prose
# anybody will read.
DEFAULT_AGENT_MODEL = "claude-haiku-4-5"
# Discovery just lists company names, so it does not need the agent's model.
DEFAULT_DISCOVERY_MODEL = "claude-haiku-4-5"
# Classifying a subject line into four buckets is the cheapest judgement call
# in the app, and it runs once per candidate email.
DEFAULT_CLASSIFIER_MODEL = "claude-haiku-4-5"

# Reads a search result list back and drops rows that are a different kind of
# job. One call per search on top of the agent's own, so it is the cheap model
# and stays the cheap model.
DEFAULT_SCREEN_MODEL = "claude-haiku-4-5"
# Which embedding backend to build. "local" runs a model on this machine and
# needs no key at all, which is why it is the default: a fresh clone can embed
# the whole corpus without signing up for anything.
DEFAULT_EMBEDDING_PROVIDER = "local"

# A provider is useless without a model and a dimension, and the right pair
# differs per provider. Choosing a provider therefore chooses both, unless
# EMBEDDING_MODEL / EMBEDDING_DIM say otherwise.
PROVIDER_DEFAULTS: dict[str, tuple[str, int]] = {
    "local": ("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", 384),
    "voyage": ("voyage-3.5", 1024),
}


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
    bm25_index_path: Path
    letters_dir: Path
    eval_dir: Path
    companies_path: Path
    gmail_token_path: Path

    anthropic_api_key: str | None
    voyage_api_key: str | None
    google_client_id: str | None
    google_client_secret: str | None

    letter_model: str
    agent_model: str
    discovery_model: str
    classifier_model: str
    screen_model: str
    screen_results: bool
    embedding_provider: str
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


def _flag(raw: str | None, *, default: bool) -> bool:
    """Read a boolean environment variable, tolerating how people write them."""
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def load_settings() -> Settings:
    """Build a :class:`Settings` from ``backend/.env`` plus the process environment."""
    load_dotenv(BACKEND_DIR / ".env", override=False)

    data_dir = Path(os.getenv("DATA_DIR") or PROJECT_ROOT / "data").resolve()
    profile_dir = Path(os.getenv("PROFILE_DIR") or PROJECT_ROOT / "profile").resolve()

    raw_origins = os.getenv("CORS_ORIGINS") or "http://localhost:5173,http://127.0.0.1:5173"
    origins = tuple(o.strip() for o in raw_origins.split(",") if o.strip())

    provider = (os.getenv("EMBEDDING_PROVIDER") or DEFAULT_EMBEDDING_PROVIDER).strip().lower()
    if provider not in PROVIDER_DEFAULTS:
        raise ConfigError(
            f"EMBEDDING_PROVIDER={provider!r} is not one of "
            f"{', '.join(sorted(PROVIDER_DEFAULTS))}. Fix it in {BACKEND_DIR / '.env'}."
        )
    default_model, default_dim = PROVIDER_DEFAULTS[provider]

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
        # The inverted index BM25 scores against. Derived from the chunks
        # table and rebuilt when it no longer matches, so it is cache, not data.
        bm25_index_path=data_dir / "bm25.npz",
        letters_dir=data_dir / "letters",
        eval_dir=data_dir / "eval",
        companies_path=BACKEND_DIR / "companies.toml",
        # The Gmail refresh token. Lives in data/, which is gitignored whole.
        gmail_token_path=data_dir / "gmail_token.json",
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        voyage_api_key=os.getenv("VOYAGE_API_KEY") or None,
        google_client_id=os.getenv("GOOGLE_CLIENT_ID") or None,
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET") or None,
        letter_model=os.getenv("LETTER_MODEL") or DEFAULT_LETTER_MODEL,
        agent_model=os.getenv("AGENT_MODEL") or DEFAULT_AGENT_MODEL,
        discovery_model=os.getenv("DISCOVERY_MODEL") or DEFAULT_DISCOVERY_MODEL,
        classifier_model=os.getenv("CLASSIFIER_MODEL") or DEFAULT_CLASSIFIER_MODEL,
        screen_model=os.getenv("SCREEN_MODEL") or DEFAULT_SCREEN_MODEL,
        # On by default: a screen nobody switched on is a screen that never
        # runs. `SCREEN_RESULTS=0` turns it off for a run where the extra call
        # per search is not wanted, such as timing retrieval on its own.
        screen_results=_flag(os.getenv("SCREEN_RESULTS"), default=True),
        embedding_provider=provider,
        embedding_model=os.getenv("EMBEDDING_MODEL") or default_model,
        embedding_dim=int(os.getenv("EMBEDDING_DIM") or default_dim),
        api_host=os.getenv("API_HOST") or "127.0.0.1",
        api_port=int(os.getenv("API_PORT") or 8010),
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
