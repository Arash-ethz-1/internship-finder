"""FastAPI application factory.

Phase 1 wires the app, CORS and a health check only. The routers in
``routes_postings``, ``routes_chat`` and ``routes_letters`` are registered in
Phase 7, once there is something behind them.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import get_settings


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

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        """Liveness probe. Confirms the app boots and settings resolve."""
        return {"status": "ok", "version": app.version}

    return app


app = create_app()
