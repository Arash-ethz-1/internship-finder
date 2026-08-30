"""Phase 7: the API surface, against a throwaway database."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from agent_app.api.main import create_app
from agent_app.core import tools


@pytest.fixture
def client(conn: sqlite3.Connection) -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def seed(conn: sqlite3.Connection, n: int = 3) -> None:
    rows = [
        (
            "greenhouse:1",
            "greenhouse",
            "Acme",
            "Software Engineering Intern",
            "Zurich",
            0,
            "intern",
            "2026-06-01T00:00:00Z",
        ),
        (
            "lever:2",
            "lever",
            "Beta Robotics",
            "Senior Engineer",
            "Remote - Europe",
            1,
            "unknown",
            "2026-05-01T00:00:00Z",
        ),
        (
            "ashby:3",
            "ashby",
            "Acme",
            "New Grad Engineer",
            "Berlin",
            0,
            "newgrad",
            "2026-07-01T00:00:00Z",
        ),
    ][:n]
    for pid, source, company, title, location, remote, level, posted in rows:
        conn.execute(
            "INSERT INTO postings (id, source, company, title, location, remote, url, body,"
            " body_hash, posted_at, level, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?, 'https://example.com', 'the body', 'h', ?, ?,"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (pid, source, company, title, location, remote, posted, level),
        )
    conn.commit()


# --- health and docs -------------------------------------------------------


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_documents_every_route(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for expected in (
        "/api/postings",
        "/api/postings/{posting_id}",
        "/api/applications/{posting_id}",
        "/api/letters/{posting_id}",
        "/api/stats",
        "/api/chat",
    ):
        assert expected in paths, expected


# --- postings --------------------------------------------------------------


def test_list_postings(client: TestClient, conn: sqlite3.Connection) -> None:
    seed(conn)
    body = client.get("/api/postings").json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    # Newest first by default.
    assert body["items"][0]["id"] == "ashby:3"
    # A posting with no application row is untriaged, not null.
    assert body["items"][0]["status"] == "untriaged"
    # The grid never needs the body.
    assert "body" not in body["items"][0]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("level=intern", ["greenhouse:1"]),
        ("company=Acme", ["ashby:3", "greenhouse:1"]),
        ("remote=true", ["lever:2"]),
        ("source=ashby", ["ashby:3"]),
        ("location=Zurich", ["greenhouse:1"]),
        # "Software Engineering Intern" matches too, newest first.
        ("q=engineer", ["ashby:3", "greenhouse:1", "lever:2"]),
        ("posted_after=2026-06-15T00:00:00Z", ["ashby:3"]),
    ],
)
def test_posting_filters(
    client: TestClient, conn: sqlite3.Connection, query: str, expected: list[str]
) -> None:
    seed(conn)
    items = client.get(f"/api/postings?{query}").json()["items"]
    assert [i["id"] for i in items] == expected


def test_postings_pagination_reports_the_full_total(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    seed(conn)
    body = client.get("/api/postings?limit=1&offset=1").json()
    assert body["total"] == 3  # the count before the page, not after
    assert len(body["items"]) == 1
    assert body["offset"] == 1


def test_postings_sorting(client: TestClient, conn: sqlite3.Connection) -> None:
    seed(conn)
    items = client.get("/api/postings?sort=company&descending=false").json()["items"]
    assert [i["company"] for i in items] == ["Acme", "Acme", "Beta Robotics"]


def test_untriaged_filter(client: TestClient, conn: sqlite3.Connection) -> None:
    seed(conn)
    tools.update_status("greenhouse:1", "applied")

    untriaged = client.get("/api/postings?status=untriaged").json()
    assert {i["id"] for i in untriaged["items"]} == {"lever:2", "ashby:3"}

    applied = client.get("/api/postings?status=applied").json()
    assert [i["id"] for i in applied["items"]] == ["greenhouse:1"]


def test_unknown_status_filter_is_rejected(client: TestClient, conn: sqlite3.Connection) -> None:
    assert client.get("/api/postings?status=pending").status_code == 422


def test_posting_detail(client: TestClient, conn: sqlite3.Connection) -> None:
    seed(conn)
    tools.update_status("greenhouse:1", "interviewing", "call friday")

    body = client.get("/api/postings/greenhouse:1").json()
    assert body["id"] == "greenhouse:1"
    assert body["body"] == "the body"
    assert body["status"] == "interviewing"
    assert body["note"] == "call friday"
    assert len(body["history"]) == 1


def test_posting_detail_404(client: TestClient, conn: sqlite3.Connection) -> None:
    assert client.get("/api/postings/greenhouse:nope").status_code == 404


# --- applications ----------------------------------------------------------


def test_patch_application(client: TestClient, conn: sqlite3.Connection) -> None:
    seed(conn)
    response = client.patch(
        "/api/applications/greenhouse:1", json={"status": "applied", "note": "sent"}
    )
    assert response.status_code == 200
    assert response.json()["from_status"] is None
    assert response.json()["status"] == "applied"

    # And it persists.
    assert client.get("/api/postings/greenhouse:1").json()["status"] == "applied"


def test_patch_application_rejects_an_invented_status(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    seed(conn)
    response = client.patch("/api/applications/greenhouse:1", json={"status": "pending"})
    assert response.status_code == 422


def test_patch_application_404(client: TestClient, conn: sqlite3.Connection) -> None:
    response = client.patch("/api/applications/nope:1", json={"status": "applied"})
    assert response.status_code == 404


# --- stats and filters -----------------------------------------------------


def test_stats(client: TestClient, conn: sqlite3.Connection) -> None:
    seed(conn)
    tools.update_status("greenhouse:1", "applied")

    body = client.get("/api/stats").json()
    assert body["total"] == 3
    assert body["by_status"] == {"untriaged": 2, "applied": 1}
    assert body["by_source"] == {"greenhouse": 1, "lever": 1, "ashby": 1}
    assert body["by_level"]["intern"] == 1
    assert {c["company"] for c in body["by_company"]} == {"Acme", "Beta Robotics"}
    assert [d["date"] for d in body["recent"]] == [
        "2026-05-01",
        "2026-06-01",
        "2026-07-01",
    ]


def test_filters_lists_companies_present(client: TestClient, conn: sqlite3.Connection) -> None:
    seed(conn)
    body = client.get("/api/filters").json()
    assert body["companies"] == ["Acme", "Beta Robotics"]
    assert "untriaged" in body["statuses"]


# --- the unimplemented halves ----------------------------------------------


def test_chat_returns_a_clean_501_naming_the_function(client: TestClient) -> None:
    response = client.post("/api/chat", json={"message": "find me internships"})
    assert response.status_code == 501
    assert "run_agent" in response.json()["detail"]


def test_letters_returns_a_clean_501_naming_the_function(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    seed(conn)
    response = client.post("/api/letters/greenhouse:1")
    assert response.status_code == 501
    assert "retrieval.search" in response.json()["detail"]


def test_the_rest_of_the_api_still_works_while_those_are_unimplemented(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    # The whole point of importing an unimplemented function rather than
    # faking it: everything else stays up.
    seed(conn)
    assert client.get("/api/postings").status_code == 200
    assert client.get("/api/stats").status_code == 200
    assert client.get("/api/health").status_code == 200
