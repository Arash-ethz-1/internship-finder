"""Shared fixtures.

Every test runs against a throwaway data directory. Nothing here touches the
real ``data/`` folder, and no test is allowed to reach the network.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_app import runtime
from agent_app.config import Settings, get_settings, reset_settings


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point settings at ``tmp_path`` and clear cached state around each test."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PROFILE_DIR", str(tmp_path / "profile"))
    # Emptied, not deleted. ``load_settings`` calls ``load_dotenv`` on every
    # call, so a deleted variable is put straight back from the developer's own
    # backend/.env; an empty one is left alone, and config reads "" as unset.
    # Without this a machine that has real keys, or a different provider, runs
    # a different test suite from CI.
    for name in (
        "ANTHROPIC_API_KEY",
        "VOYAGE_API_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "EMBEDDING_PROVIDER",
        "EMBEDDING_MODEL",
        "EMBEDDING_DIM",
    ):
        monkeypatch.setenv(name, "")
    reset_settings()
    runtime.reset()
    yield
    runtime.reset()
    reset_settings()


@pytest.fixture
def settings() -> Settings:
    """The settings for the current isolated test."""
    return get_settings()


@pytest.fixture
def conn() -> sqlite3.Connection:
    """An initialised connection to the throwaway database."""
    return runtime.get_db()
