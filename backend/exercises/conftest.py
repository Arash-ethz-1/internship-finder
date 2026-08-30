"""Fixtures for the exercise suite.

Separate from ``tests/conftest.py`` because these live outside the main suite,
so pytest does not share the one in ``tests/``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_app import runtime
from agent_app.config import Settings, get_settings, reset_settings


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PROFILE_DIR", str(tmp_path / "profile"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    reset_settings()
    runtime.reset()
    yield
    runtime.reset()
    reset_settings()


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def conn() -> sqlite3.Connection:
    return runtime.get_db()
