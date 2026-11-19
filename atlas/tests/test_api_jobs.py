"""Queuing background work through the API, and GitHub webhooks (lesson 5.2)."""

import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.models import Job
from tests.conftest import RecordingQueue, sign_up
from tests.test_api_resources import admin_headers

pytestmark = pytest.mark.anyio

SECRET = b"It's a Secret to Everybody"


def signed(event: str, payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    """A webhook request body and headers, signed the way GitHub signs them."""
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    headers = {
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": signature,
        "Content-Type": "application/json",
    }
    return body, headers


async def add_ignite(client: httpx.AsyncClient, session: AsyncSession) -> int:
    admin = await admin_headers(client, session)
    response = await client.post("/repos", json={"full_name": "pytorch/ignite"}, headers=admin)
    repo_id: int = response.json()["id"]
    return repo_id


async def test_adding_a_repository_queues_its_first_sync(
    client: httpx.AsyncClient, async_session: AsyncSession, sent_jobs: RecordingQueue
) -> None:
    await add_ignite(client, async_session)

    assert len(sent_jobs.sent) == 2


async def test_sync_now_reuses_pending_jobs_and_sends_nothing_new(
    client: httpx.AsyncClient, async_session: AsyncSession, sent_jobs: RecordingQueue
) -> None:
    repo_id = await add_ignite(client, async_session)
    admin = await admin_headers(client, async_session)

    response = await client.post(f"/repos/{repo_id}/sync", headers=admin)

    assert response.status_code == 202
    jobs = response.json()["jobs"]
    assert [job["kind"] for job in jobs] == ["sync_repo", "sync_issues"]
    assert [job["id"] for job in jobs] == sent_jobs.sent  # the same two, not two more
    assert len(sent_jobs.sent) == 2


async def test_anyone_signed_in_can_watch_a_job(
    client: httpx.AsyncClient, async_session: AsyncSession, sent_jobs: RecordingQueue
) -> None:
    await add_ignite(client, async_session)
    learner = await sign_up(client, "learner@example.com")

    response = await client.get(f"/jobs/{sent_jobs.sent[0]}", headers=learner)
    missing = await client.get("/jobs/999999", headers=learner)

    assert response.json()["status"] == "queued"
    assert missing.json()["error"]["code"] == "job_not_found"


async def test_only_admins_may_trigger_a_sync(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    repo_id = await add_ignite(client, async_session)
    learner = await sign_up(client, "learner@example.com")

    response = await client.post(f"/repos/{repo_id}/sync", headers=learner)

    assert response.status_code == 403


async def test_signature_from_githubs_documentation_is_accepted(
    client: httpx.AsyncClient,
) -> None:
    # GitHub's worked example: this secret and body must give exactly this signature.
    headers = {
        "X-GitHub-Event": "ping",
        "X-Hub-Signature-256": (
            "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
        ),
    }

    response = await client.post("/webhooks/github", content=b"Hello, World!", headers=headers)

    assert response.status_code == 202
    assert response.json() == {"jobs": []}


async def test_push_to_a_tracked_repository_queues_a_code_sync(
    client: httpx.AsyncClient, async_session: AsyncSession, sent_jobs: RecordingQueue
) -> None:
    await add_ignite(client, async_session)
    sent_jobs.sent.clear()
    # Pretend the first sync ran, so there is no pending job to reuse.
    await async_session.execute(update(Job).values(status="succeeded"))
    await async_session.commit()

    payload = {"ref": "refs/heads/master", "repository": {"full_name": "pytorch/ignite"}}
    body, headers = signed("push", payload)
    response = await client.post("/webhooks/github", content=body, headers=headers)

    assert [job["kind"] for job in response.json()["jobs"]] == ["sync_repo"]
    assert len(sent_jobs.sent) == 1


@pytest.mark.parametrize(
    "signature",
    [
        None,
        "sha256=" + "0" * 64,
        # Signed with a different secret: what an attacker who guessed the URL can make.
        "sha256=" + hmac.new(b"guess", b"{}", hashlib.sha256).hexdigest(),
    ],
)
async def test_forged_webhooks_are_rejected(
    client: httpx.AsyncClient, sent_jobs: RecordingQueue, signature: str | None
) -> None:
    headers = {"X-GitHub-Event": "push"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature

    response = await client.post("/webhooks/github", content=b"{}", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "bad_signature"
    assert sent_jobs.sent == []


async def test_untracked_repository_is_ignored(
    client: httpx.AsyncClient, sent_jobs: RecordingQueue
) -> None:
    payload = {"repository": {"full_name": "someone/else"}}

    body, headers = signed("push", payload)
    response = await client.post("/webhooks/github", content=body, headers=headers)

    assert response.json() == {"jobs": []}
    assert sent_jobs.sent == []
