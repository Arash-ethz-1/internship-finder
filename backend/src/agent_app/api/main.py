"""FastAPI application factory.

Routes hold no business logic: they resolve parameters, call into ``core/`` or
``db.py``, and serialise. ``create_app`` is a function rather than a
module-level ``FastAPI()`` so tests can build a fresh app against a throwaway
database.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import get_settings
from ..db import now_iso
from . import routes_chat, routes_inbox, routes_letters, routes_postings, routes_profile
from .schemas import Health

# When this process imported the app, so a stale server can be spotted
# without guessing from how long a request took.
STARTED_AT = now_iso()


def create_app() -> FastAPI:
    """Build the ASGI app."""
    settings = get_settings()

    app = FastAPI(
        title="Internship agent",
        version="0.1.0",
        summary="Local screener over internship postings.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", tags=["meta"], response_model=Health)
    def health() -> Health:
        """Liveness probe. Confirms the app boots and settings resolve."""
        settings = get_settings()
        return Health(
            status="ok",
            version=app.version,
            # Reported from the running process, not from the files on disk,
            # which is the entire point: those two disagree exactly when
            # something is wrong and nothing else says so.
            screens_results=settings.screen_results and bool(settings.anthropic_api_key),
            screen_model=settings.screen_model,
            started_at=STARTED_AT,
        )

    app.include_router(routes_postings.router)
    app.include_router(routes_letters.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_inbox.router)
    app.include_router(routes_profile.router)

    return app


app = create_app()
