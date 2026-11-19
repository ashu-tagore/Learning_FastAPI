"""Repositories Atlas knows about. Anyone may list them; only administrators may add one."""

from typing import Annotated

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from atlas.api import idempotency
from atlas.api.deps import AdminUser, CurrentUser, QueueDep, SessionDep, SnapshotsDep
from atlas.api.errors import ApiError
from atlas.api.routes.jobs import BOTH_SYNCS, queue_jobs
from atlas.api.schemas import JobOut, QueuedJobs, RepoCreate, RepoOut, SnapshotOut
from atlas.models import Repo

router = APIRouter(prefix="/repos", tags=["repos"])


@router.get("", response_model=list[RepoOut])
async def list_repos(session: SessionDep) -> list[Repo]:
    return list(await session.scalars(select(Repo).order_by(Repo.full_name)))


async def get_repo_or_404(session: SessionDep, repo_id: int) -> Repo:
    repo = await session.get(Repo, repo_id)
    if repo is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "repo_not_found", "No repository with that id")
    return repo


@router.get("/{repo_id}", response_model=RepoOut)
async def read_repo(repo_id: int, session: SessionDep) -> Repo:
    return await get_repo_or_404(session, repo_id)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RepoOut)
async def add_repo(
    body: RepoCreate,
    admin: AdminUser,
    session: SessionDep,
    queue: QueueDep,
    request: Request,
    idempotency_key: Annotated[str | None, Header(max_length=100)] = None,
) -> JSONResponse:
    previous = await idempotency.replay(session, admin, idempotency_key, request.url.path)
    if previous is not None:
        return previous

    exists = (
        await session.scalar(select(Repo.id).where(Repo.full_name == body.full_name)) is not None
    )
    if exists:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "repo_already_tracked",
            f"{body.full_name} is already tracked",
        )

    repo = Repo(full_name=body.full_name)
    session.add(repo)
    await session.flush()
    payload = RepoOut.model_validate(repo).model_dump(mode="json")
    idempotency.remember(
        session, admin, idempotency_key, request.url.path, status.HTTP_201_CREATED, payload
    )
    await session.commit()
    # The first sync clones and indexes the code and fetches its issues, in the background.
    await queue_jobs(session, queue, repo.id, BOTH_SYNCS)
    return JSONResponse(payload, status_code=status.HTTP_201_CREATED)


@router.post("/{repo_id}/sync", status_code=status.HTTP_202_ACCEPTED, response_model=QueuedJobs)
async def sync_repo(
    repo_id: int, admin: AdminUser, session: SessionDep, queue: QueueDep
) -> QueuedJobs:
    """Queue a sync now. 202 Accepted: the work happens later; poll GET /jobs/{id}."""
    await get_repo_or_404(session, repo_id)
    jobs = await queue_jobs(session, queue, repo_id, BOTH_SYNCS)
    return QueuedJobs(jobs=[JobOut.model_validate(job) for job in jobs])


@router.get("/{repo_id}/snapshot", response_model=SnapshotOut)
async def read_snapshot(
    repo_id: int, user: CurrentUser, session: SessionDep, snapshots: SnapshotsDep
) -> SnapshotOut:
    """A short-lived link to the repository's files at the indexed commit.

    The client downloads from object storage directly: megabytes never pass through the
    API, and the link's signature, not Atlas, decides who may read the object.
    """
    repo = await get_repo_or_404(session, repo_id)
    commit = repo.indexed_commit
    not_found = ApiError(
        status.HTTP_404_NOT_FOUND, "snapshot_not_found", "No snapshot of this repository yet"
    )
    if commit is None:
        raise not_found
    # boto3 is synchronous and this asks the storage server: keep it off the event loop.
    stored = await run_in_threadpool(snapshots.exists, repo.full_name, commit)
    if not stored:
        raise not_found
    url, expires_at = snapshots.download_url(repo.full_name, commit)
    return SnapshotOut(commit=commit, url=url, expires_at=expires_at)
