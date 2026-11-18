from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from atlas import github, storage
from atlas.models import Issue, User
from tests.conftest import sign_up


def make_issue(number: int, labels: tuple[str, ...] = ("good first issue",)) -> github.Issue:
    return github.Issue(
        number=number,
        title=f"Issue {number}",
        url=f"https://github.com/pytorch/ignite/issues/{number}",
        author="contributor",
        labels=labels,
        comments=0,
        created_at=datetime(2026, 11, 17, tzinfo=UTC) - timedelta(days=number),
    )


def store_issues(session: Session, numbers: list[int]) -> dict[int, int]:
    """Save issues the way `atlas issues --save` does; return {number: database id}."""
    storage.save_issues(session, "pytorch/ignite", [make_issue(number) for number in numbers])
    session.commit()
    rows = session.execute(select(Issue.number, Issue.id))
    return {number: issue_id for number, issue_id in rows}


def admin_headers(client: TestClient, session: Session) -> dict[str, str]:
    headers = sign_up(client, "admin@example.com")
    session.execute(update(User).where(User.email == "admin@example.com").values(role="admin"))
    session.commit()
    return headers


# ---------- Repositories ----------


def test_only_admins_may_add_a_repository(client: TestClient, session: Session) -> None:
    learner = sign_up(client, "ada@example.com")
    admin = admin_headers(client, session)

    forbidden = client.post("/repos", json={"full_name": "pytorch/ignite"}, headers=learner)
    created = client.post("/repos", json={"full_name": "pytorch/ignite"}, headers=admin)
    duplicate = client.post("/repos", json={"full_name": "pytorch/ignite"}, headers=admin)

    assert forbidden.status_code == 403
    assert created.status_code == 201 and created.json()["full_name"] == "pytorch/ignite"
    assert duplicate.status_code == 409
    assert [repo["full_name"] for repo in client.get("/repos").json()] == ["pytorch/ignite"]


def test_idempotency_key_replays_instead_of_repeating(client: TestClient, session: Session) -> None:
    admin = admin_headers(client, session)
    headers = {**admin, "Idempotency-Key": "add-ignite-1"}

    first = client.post("/repos", json={"full_name": "pytorch/ignite"}, headers=headers)
    retry = client.post("/repos", json={"full_name": "pytorch/ignite"}, headers=headers)

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()


def test_repository_names_are_validated(client: TestClient, session: Session) -> None:
    admin = admin_headers(client, session)

    response = client.post("/repos", json={"full_name": "not a repo"}, headers=admin)

    assert response.status_code == 422


# ---------- Issues ----------


def test_issues_are_paginated_with_a_cursor(client: TestClient, session: Session) -> None:
    store_issues(session, [1, 2, 3, 4, 5])

    first = client.get("/issues", params={"limit": 2}).json()
    second = client.get("/issues", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    third = client.get("/issues", params={"limit": 2, "cursor": second["next_cursor"]}).json()

    assert [issue["number"] for issue in first["items"]] == [5, 4]
    assert [issue["number"] for issue in second["items"]] == [3, 2]
    assert [issue["number"] for issue in third["items"]] == [1]
    assert third["next_cursor"] is None


def test_issues_can_be_filtered_by_label(client: TestClient, session: Session) -> None:
    storage.save_issues(
        session, "pytorch/ignite", [make_issue(1, ("docs",)), make_issue(2, ("help wanted",))]
    )
    session.commit()

    response = client.get("/issues", params={"label": "docs"})

    assert [issue["number"] for issue in response.json()["items"]] == [1]
    assert response.json()["items"][0]["labels"] == ["docs"]


def test_a_garbled_cursor_is_a_client_error(client: TestClient) -> None:
    assert client.get("/issues", params={"cursor": "not-a-cursor"}).status_code == 400


def test_missing_issue_is_404(client: TestClient) -> None:
    response = client.get("/issues/999999")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_saving_is_idempotent_and_private(client: TestClient, session: Session) -> None:
    ids = store_issues(session, [1, 2])
    ada = sign_up(client, "ada@example.com")
    grace = sign_up(client, "grace@example.com")

    assert client.put(f"/issues/{ids[2]}/saved", headers=ada).status_code == 204
    assert client.put(f"/issues/{ids[2]}/saved", headers=ada).status_code == 204

    ada_saved = client.get("/users/me/saved-issues", headers=ada).json()
    assert [issue["number"] for issue in ada_saved] == [2]
    assert client.get("/users/me/saved-issues", headers=grace).json() == []

    assert client.delete(f"/issues/{ids[2]}/saved", headers=ada).status_code == 204
    assert client.get("/users/me/saved-issues", headers=ada).json() == []


def test_saving_needs_a_login(client: TestClient, session: Session) -> None:
    ids = store_issues(session, [1])

    assert client.put(f"/issues/{ids[1]}/saved").status_code == 401


# ---------- Search ----------


def test_search_finds_and_pages(client: TestClient, session: Session, sample_repo: Path) -> None:
    storage.index_folder(session, "pytorch/ignite", sample_repo, chunk_lines=1)
    session.commit()

    first = client.get("/search", params={"q": "event", "limit": 1}).json()
    second = client.get("/search", params={"q": "event", "limit": 1, "offset": 1}).json()

    assert len(first["items"]) == 1 and first["next_offset"] == 1
    assert len(second["items"]) == 1 and second["next_offset"] is None
    assert first["items"][0]["path"] != second["items"][0]["path"]


def test_search_can_be_limited_to_documentation(
    client: TestClient, session: Session, sample_repo: Path
) -> None:
    storage.index_folder(session, "pytorch/ignite", sample_repo)
    session.commit()

    hits = client.get("/search", params={"q": "event", "kind": "doc"}).json()["items"]

    assert [hit["path"] for hit in hits] == ["README.md"]


def test_search_needs_a_query(client: TestClient) -> None:
    response = client.get("/search")

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["location"] == ["query", "q"]


# ---------- Notes (review lesson 4.16) ----------


def test_notes_are_created_listed_edited_and_deleted(client: TestClient, session: Session) -> None:
    ids = store_issues(session, [7])
    ada = sign_up(client, "ada@example.com")

    created = client.post(
        f"/issues/{ids[7]}/notes", json={"body": "Start in engine.py"}, headers=ada
    )
    note_id = created.json()["id"]
    edited = client.patch(f"/notes/{note_id}", json={"body": "Start in events.py"}, headers=ada)

    assert created.status_code == 201
    assert created.json()["issue_number"] == 7 and created.json()["repo"] == "pytorch/ignite"
    assert edited.json()["body"] == "Start in events.py"
    listed = client.get(f"/issues/{ids[7]}/notes", headers=ada).json()
    assert [note["body"] for note in listed] == ["Start in events.py"]

    assert client.delete(f"/notes/{note_id}", headers=ada).status_code == 204
    assert client.get(f"/issues/{ids[7]}/notes", headers=ada).json() == []


def test_other_peoples_notes_are_invisible(client: TestClient, session: Session) -> None:
    ids = store_issues(session, [7])
    ada = sign_up(client, "ada@example.com")
    grace = sign_up(client, "grace@example.com")
    note_id = client.post(f"/issues/{ids[7]}/notes", json={"body": "mine"}, headers=ada).json()[
        "id"
    ]

    assert client.get(f"/issues/{ids[7]}/notes", headers=grace).json() == []
    assert client.patch(f"/notes/{note_id}", json={"body": "x"}, headers=grace).status_code == 404
    assert client.delete(f"/notes/{note_id}", headers=grace).status_code == 404


def test_notes_survive_the_issue_closing(client: TestClient, session: Session) -> None:
    ids = store_issues(session, [7, 8])
    ada = sign_up(client, "ada@example.com")
    client.post(f"/issues/{ids[7]}/notes", json={"body": "keep me"}, headers=ada)

    # Issue 7 closes: the next sync removes it from the issues table...
    store_issues(session, [8])
    # ...and when it reopens it gets a new row, but the note is still attached by number.
    reopened = store_issues(session, [7, 8])

    notes = client.get(f"/issues/{reopened[7]}/notes", headers=ada).json()
    assert [note["body"] for note in notes] == ["keep me"]


def test_note_creation_honours_idempotency_keys(client: TestClient, session: Session) -> None:
    ids = store_issues(session, [7])
    ada = {**sign_up(client, "ada@example.com"), "Idempotency-Key": "note-1"}

    first = client.post(f"/issues/{ids[7]}/notes", json={"body": "once"}, headers=ada)
    retry = client.post(f"/issues/{ids[7]}/notes", json={"body": "once"}, headers=ada)
    other_path = client.post(f"/issues/{ids[7] + 1000}/notes", json={"body": "x"}, headers=ada)

    assert retry.json() == first.json()
    assert len(client.get(f"/issues/{ids[7]}/notes", headers=ada).json()) == 1
    assert other_path.status_code == 422
