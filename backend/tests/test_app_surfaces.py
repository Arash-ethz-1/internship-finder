"""The surfaces that moved out of the CLI and into the app.

Profile editing, letter revision and the mailbox sync job. What they have in
common is a rule that has to survive the move: editing the profile must not let
the chunks go stale, revising a letter must not let it invent, and syncing mail
must still never change an application by itself.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_app.api.main import create_app
from agent_app.inbox.job import SyncJob
from agent_app.inbox.sync import InboxError


@pytest.fixture
def client(conn: sqlite3.Connection) -> TestClient:  # noqa: ARG001 - shares the temp db
    return TestClient(create_app())


class TestProfileEditing:
    """The corpus every letter is grounded in.

    The failure this exists to prevent: editing `profile/` in a text editor,
    forgetting `cli ingest-profile`, and having every subsequent letter
    grounded in text that no longer exists -- with nothing anywhere saying so.
    """

    def test_saving_rewrites_the_file_and_rechunks_in_one_step(
        self, client: TestClient, profile_dir: Path, conn: sqlite3.Connection
    ) -> None:
        (profile_dir / "robots.md").write_text("# Robots\n\nOld text.", encoding="utf-8")

        response = client.put(
            "/api/profile/robots",
            json={"text": "# Robots\n\nI built a perception stack in PyTorch.\n"},
        )
        assert response.status_code == 200

        # The file on disk and the chunks in the database agree, because one
        # request did both.
        assert "perception stack" in (profile_dir / "robots.md").read_text(encoding="utf-8")
        texts = [
            row[0] for row in conn.execute("SELECT text FROM chunks WHERE profile_doc = 'robots'")
        ]
        assert texts and any("perception stack" in t for t in texts)
        assert not any("Old text" in t for t in texts)

    def test_a_saved_document_reports_nothing_embedded_yet(
        self, client: TestClient, profile_dir: Path
    ) -> None:
        """Chunking is immediate and embedding is not, and the response says so
        rather than implying the write-up is fully searchable."""
        (profile_dir / "robots.md").write_text("# Robots\n\nText.", encoding="utf-8")
        body = client.put("/api/profile/robots", json={"text": "# Robots\n\nMore."}).json()
        assert body["chunks"] >= 1
        assert body["embedded"] == 0

    def test_the_readme_and_example_are_listed_but_never_chunked(
        self, client: TestClient, profile_dir: Path
    ) -> None:
        """Grounding a letter in placeholder text is how a letter starts lying."""
        (profile_dir / "example-project.md").write_text("# Example\n\nLorem.", encoding="utf-8")
        body = client.put(
            "/api/profile/example-project", json={"text": "# Example\n\nLorem ipsum."}
        ).json()
        assert body["ingested"] is False
        assert body["chunks"] == 0

    @pytest.mark.parametrize("slug", ["../secrets", "..%2Fsecrets", "a/b", ".env", "A_B"])
    def test_a_slug_cannot_escape_the_profile_folder(self, client: TestClient, slug: str) -> None:
        """A path parameter that reaches the filesystem is exactly where
        directory traversal lives."""
        assert client.get(f"/api/profile/{slug}").status_code in (404, 422)

    def test_deleting_removes_the_file_and_its_chunks(
        self, client: TestClient, profile_dir: Path, conn: sqlite3.Connection
    ) -> None:
        (profile_dir / "robots.md").write_text("# Robots\n\nText.", encoding="utf-8")
        client.put("/api/profile/robots", json={"text": "# Robots\n\nText."})

        assert client.delete("/api/profile/robots").status_code == 204
        assert not (profile_dir / "robots.md").exists()
        assert (
            conn.execute("SELECT count(*) FROM chunks WHERE profile_doc = 'robots'").fetchone()[0]
            == 0
        )


class TestLetterRevision:
    def test_an_empty_instruction_is_refused(self) -> None:
        """ "Change it" with nothing after it is not a revision."""
        from agent_app.core.letters import LetterError, revise_letter

        with pytest.raises(LetterError, match="instruction"):
            revise_letter("greenhouse:1", "   ")

    def test_the_prompt_carries_the_grounding_and_the_current_text(self) -> None:
        """A revision that could not see the extracts would be free to shorten
        by inventing a crisper fact."""
        from agent_app.core.letters import build_revision_prompt
        from agent_app.core.retrieval import SearchHit
        from agent_app.db import Posting

        posting = Posting(
            id="greenhouse:1",
            source="greenhouse",
            company="Acme",
            title="Intern",
            location="Zurich",
            remote=False,
            url="https://example.com",
            body="Work on robots.",
            body_hash="h",
        )
        hit = SearchHit(
            chunk_id=1,
            posting_id=None,
            profile_doc="robots",
            ordinal=0,
            text="I built a perception stack in PyTorch.",
            score=1.0,
            rank=1,
            component_scores={"dense": 0.6, "bm25": 0.4},
        )

        prompt = build_revision_prompt(posting, [hit], "The current letter.", "Make it shorter.")
        assert "perception stack" in prompt
        assert "The current letter." in prompt
        assert "Make it shorter." in prompt


class TestSyncJob:
    """The job behind the dashboard's `check mail` button.

    PROGRESS.md ruled out a sync *route* because a request must not wait on
    Gmail. These pin that the job form keeps that property and the one that
    matters more: a sync only ever suggests.
    """

    def test_two_syncs_cannot_run_at_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The second would fetch the same messages and find the first had
        already recorded them."""
        job = SyncJob()
        job._state.status = "running"  # noqa: SLF001 - simulating a run in flight
        with pytest.raises(InboxError, match="already running"):
            job.start()

    def test_a_failure_becomes_state_rather_than_a_traceback(self) -> None:
        """A sync reaches Gmail, an OAuth endpoint and a model. None of those
        failing should take the API down or leave the job stuck on `running`.
        """
        job = SyncJob()
        job._run(include_sent=False, limit=None)  # noqa: SLF001 - no credentials in tests

        assert job.state.status == "error"
        assert job.state.error
        assert job.state.finished_at is not None

    def test_the_status_route_says_when_gmail_is_not_connected(self, client: TestClient) -> None:
        """ "Nothing happened" then has a setup answer rather than being a
        failure the person has to go and diagnose."""
        body = client.get("/api/inbox/sync").json()
        assert body["status"] == "idle"
        assert "authorised" in body

    def test_starting_without_a_token_is_refused_immediately(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_app.api import routes_inbox

        monkeypatch.setattr(routes_inbox, "_is_authorised", lambda: False)
        response = client.post("/api/inbox/sync", json={})
        assert response.status_code == 409
        assert "login" in response.json()["detail"]
