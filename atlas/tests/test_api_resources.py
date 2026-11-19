from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from atlas import github, storage
from atlas.models import Issue, User
from tests.conftest import sign_up

pytestmark = pytest.mark.anyio


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


async def store_issues(session: AsyncSession, issues: list[github.Issue]) -> dict[int, int]:
    """Save issues the way `atlas issues --save` does; return {number: database id}.

    storage.save_issues is synchronous code (the CLI uses it). run_sync runs it on the
    async session's connection, inside the same rolled-back transaction.
    """
    await session.run_sync(lambda sync: storage.save_issues(sync, "pytorch/ignite", issues))
    await session.commit()
    rows = await session.execute(select(Issue.number, Issue.id))
    return {number: issue_id for number, issue_id in rows}


async def store_numbers(session: AsyncSession, numbers: list[int]) -> dict[int, int]:
    return await store_issues(session, [make_issue(number) for number in numbers])


async def index_sample(session: AsyncSession, root: Path, chunk_lines: int = 40) -> None:
    await session.run_sync(
        lambda sync: storage.index_folder(sync, "pytorch/ignite", root, chunk_lines=chunk_lines)
    )
    await session.commit()


async def admin_headers(client: httpx.AsyncClient, session: AsyncSession) -> dict[str, str]:
    headers = await sign_up(client, "admin@example.com")
    await session.execute(
        update(User).where(User.email == "admin@example.com").values(role="admin")
    )
    await session.commit()
    return headers


# ---------- Repositories ----------


async def test_only_admins_may_add_a_repository(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    learner = await sign_up(client, "ada@example.com")
    admin = await admin_headers(client, async_session)
    body = {"full_name": "pytorch/ignite"}

    forbidden = await client.post("/repos", json=body, headers=learner)
    created = await client.post("/repos", json=body, headers=admin)
    duplicate = await client.post("/repos", json=body, headers=admin)

    assert forbidden.status_code == 403
    assert created.status_code == 201 and created.json()["full_name"] == "pytorch/ignite"
    assert duplicate.status_code == 409
    listed = await client.get("/repos")
    assert [repo["full_name"] for repo in listed.json()] == ["pytorch/ignite"]


async def test_idempotency_key_replays_instead_of_repeating(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    admin = await admin_headers(client, async_session)
    headers = {**admin, "Idempotency-Key": "add-ignite-1"}

    first = await client.post("/repos", json={"full_name": "pytorch/ignite"}, headers=headers)
    retry = await client.post("/repos", json={"full_name": "pytorch/ignite"}, headers=headers)

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()


async def test_repository_names_are_validated(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    admin = await admin_headers(client, async_session)

    response = await client.post("/repos", json={"full_name": "not a repo"}, headers=admin)

    assert response.status_code == 422


# ---------- Issues ----------


async def test_issues_are_paginated_with_a_cursor(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    await store_numbers(async_session, [1, 2, 3, 4, 5])

    first = (await client.get("/issues", params={"limit": 2})).json()
    second = (
        await client.get("/issues", params={"limit": 2, "cursor": first["next_cursor"]})
    ).json()
    third = (
        await client.get("/issues", params={"limit": 2, "cursor": second["next_cursor"]})
    ).json()

    assert [issue["number"] for issue in first["items"]] == [5, 4]
    assert [issue["number"] for issue in second["items"]] == [3, 2]
    assert [issue["number"] for issue in third["items"]] == [1]
    assert third["next_cursor"] is None


async def test_issues_can_be_filtered_by_label(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    await store_issues(async_session, [make_issue(1, ("docs",)), make_issue(2, ("help wanted",))])

    response = await client.get("/issues", params={"label": "docs"})

    assert [issue["number"] for issue in response.json()["items"]] == [1]
    assert response.json()["items"][0]["labels"] == ["docs"]


@pytest.mark.parametrize("cursor", ["not-a-cursor", "e30=", "eyJiZWZvcmUiOiAieCJ9", "é"])
async def test_a_garbled_cursor_is_a_client_error(client: httpx.AsyncClient, cursor: str) -> None:
    # Garbage, {} with no "before", and {"before": "x"}, and text that isn't ASCII.
    response = await client.get("/issues", params={"cursor": cursor})

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["location"] == ["query", "cursor"]


async def test_missing_issue_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/issues/999999")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "issue_not_found"


async def test_saving_is_idempotent_and_private(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    ids = await store_numbers(async_session, [1, 2])
    ada = await sign_up(client, "ada@example.com")
    grace = await sign_up(client, "grace@example.com")

    assert (await client.put(f"/issues/{ids[2]}/saved", headers=ada)).status_code == 204
    assert (await client.put(f"/issues/{ids[2]}/saved", headers=ada)).status_code == 204

    ada_saved = (await client.get("/users/me/saved-issues", headers=ada)).json()
    assert [issue["number"] for issue in ada_saved] == [2]
    assert (await client.get("/users/me/saved-issues", headers=grace)).json() == []

    assert (await client.delete(f"/issues/{ids[2]}/saved", headers=ada)).status_code == 204
    assert (await client.get("/users/me/saved-issues", headers=ada)).json() == []


async def test_saving_needs_a_login(client: httpx.AsyncClient, async_session: AsyncSession) -> None:
    ids = await store_numbers(async_session, [1])

    assert (await client.put(f"/issues/{ids[1]}/saved")).status_code == 401


# ---------- Search ----------


async def test_search_finds_and_pages(
    client: httpx.AsyncClient, async_session: AsyncSession, sample_repo: Path
) -> None:
    await index_sample(async_session, sample_repo, chunk_lines=1)

    first = (await client.get("/search", params={"q": "event", "limit": 1})).json()
    second = (await client.get("/search", params={"q": "event", "limit": 1, "offset": 1})).json()

    assert len(first["items"]) == 1 and first["next_offset"] == 1
    assert len(second["items"]) == 1 and second["next_offset"] is None
    assert first["items"][0]["path"] != second["items"][0]["path"]


async def test_search_can_be_limited_to_documentation(
    client: httpx.AsyncClient, async_session: AsyncSession, sample_repo: Path
) -> None:
    await index_sample(async_session, sample_repo)

    hits = (await client.get("/search", params={"q": "event", "kind": "doc"})).json()["items"]

    assert [hit["path"] for hit in hits] == ["README.md"]


async def test_search_needs_a_query(client: httpx.AsyncClient) -> None:
    response = await client.get("/search")

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["location"] == ["query", "q"]


# ---------- Notes (review lesson 4.20) ----------


async def test_notes_are_created_listed_edited_and_deleted(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    ids = await store_numbers(async_session, [7])
    ada = await sign_up(client, "ada@example.com")

    created = await client.post(
        f"/issues/{ids[7]}/notes", json={"body": "Start in engine.py"}, headers=ada
    )
    note_id = created.json()["id"]
    edited = await client.patch(
        f"/notes/{note_id}", json={"body": "Start in events.py"}, headers=ada
    )

    assert created.status_code == 201
    assert created.json()["issue_number"] == 7 and created.json()["repo"] == "pytorch/ignite"
    assert edited.json()["body"] == "Start in events.py"
    listed = (await client.get(f"/issues/{ids[7]}/notes", headers=ada)).json()
    assert [note["body"] for note in listed] == ["Start in events.py"]

    assert (await client.delete(f"/notes/{note_id}", headers=ada)).status_code == 204
    assert (await client.get(f"/issues/{ids[7]}/notes", headers=ada)).json() == []


async def test_other_peoples_notes_are_invisible(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    ids = await store_numbers(async_session, [7])
    ada = await sign_up(client, "ada@example.com")
    grace = await sign_up(client, "grace@example.com")
    created = await client.post(f"/issues/{ids[7]}/notes", json={"body": "mine"}, headers=ada)
    note_id = created.json()["id"]

    assert (await client.get(f"/issues/{ids[7]}/notes", headers=grace)).json() == []
    edit = await client.patch(f"/notes/{note_id}", json={"body": "x"}, headers=grace)
    assert edit.status_code == 404
    assert (await client.delete(f"/notes/{note_id}", headers=grace)).status_code == 404


async def test_notes_survive_the_issue_closing(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    ids = await store_numbers(async_session, [7, 8])
    ada = await sign_up(client, "ada@example.com")
    await client.post(f"/issues/{ids[7]}/notes", json={"body": "keep me"}, headers=ada)

    # Issue 7 closes: the next sync removes it from the issues table...
    await store_numbers(async_session, [8])
    # ...and when it reopens it gets a new row, but the note is still attached by number.
    reopened = await store_numbers(async_session, [7, 8])

    notes = (await client.get(f"/issues/{reopened[7]}/notes", headers=ada)).json()
    assert [note["body"] for note in notes] == ["keep me"]


async def test_note_creation_honours_idempotency_keys(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    ids = await store_numbers(async_session, [7])
    ada = {**await sign_up(client, "ada@example.com"), "Idempotency-Key": "note-1"}
    path = f"/issues/{ids[7]}/notes"

    first = await client.post(path, json={"body": "once"}, headers=ada)
    retry = await client.post(path, json={"body": "once"}, headers=ada)
    other_path = await client.post(
        f"/issues/{ids[7] + 1000}/notes", json={"body": "x"}, headers=ada
    )

    assert retry.json() == first.json()
    assert len((await client.get(path, headers=ada)).json()) == 1
    assert other_path.status_code == 422


async def test_repository_query_parameters_are_parsed_at_the_edge(
    client: httpx.AsyncClient,
) -> None:
    issues = await client.get("/issues", params={"repo": "not a repo"})
    search = await client.get("/search", params={"q": "event", "repo": "ignite"})

    assert issues.status_code == search.status_code == 422
    assert issues.json()["error"]["details"][0]["location"] == ["query", "repo"]
