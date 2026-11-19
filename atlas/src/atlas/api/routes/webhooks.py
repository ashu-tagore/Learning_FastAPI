"""GitHub tells Atlas when a tracked repository changes (lesson 5.2).

Anyone on the internet can POST to this URL, so nothing in a request is trusted until its
signature checks out: GitHub signs each body with a secret shared only with Atlas.
"""

import hashlib
import hmac
import json

from fastapi import APIRouter, Header, Request, status
from sqlalchemy import select

from atlas.api.deps import QueueDep, SessionDep, SettingsDep
from atlas.api.errors import ApiError
from atlas.api.routes.jobs import queue_jobs
from atlas.api.schemas import JobOut, QueuedJobs
from atlas.models import Repo

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

JOB_FOR_EVENT = {"push": "sync_repo", "issues": "sync_issues"}


def signature_is_valid(secret: str, body: bytes, signature: str | None) -> bool:
    if signature is None:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # compare_digest takes the same time however many characters match, so an attacker
    # can't find the right signature one character at a time by timing the answers.
    return hmac.compare_digest(expected, signature)


@router.post("/github", status_code=status.HTTP_202_ACCEPTED, response_model=QueuedJobs)
async def github_webhook(
    request: Request,
    session: SessionDep,
    queue: QueueDep,
    settings: SettingsDep,
    x_github_event: str = Header(),
    x_hub_signature_256: str | None = Header(default=None),
) -> QueuedJobs:
    secret = settings.github_webhook_secret
    if secret is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND, "webhooks_disabled", "Webhooks are not configured"
        )

    # The signature covers the exact bytes GitHub sent, so check them before parsing.
    body = await request.body()
    if not signature_is_valid(secret.get_secret_value(), body, x_hub_signature_256):
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED, "bad_signature", "The webhook signature is not valid"
        )

    kind = JOB_FOR_EVENT.get(x_github_event)
    if kind is None:
        # ping (sent when the webhook is created) and events Atlas doesn't use.
        return QueuedJobs(jobs=[])

    full_name = json.loads(body).get("repository", {}).get("full_name")
    repo_id = await session.scalar(select(Repo.id).where(Repo.full_name == full_name))
    if repo_id is None:
        return QueuedJobs(jobs=[])

    jobs = await queue_jobs(session, queue, repo_id, (kind,))
    return QueuedJobs(jobs=[JobOut.model_validate(job) for job in jobs])
