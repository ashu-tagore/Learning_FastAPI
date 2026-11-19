"""Background jobs as seen through the API: queue them, then watch them (lesson 5.2)."""

from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from atlas.api.deps import CurrentUser, SessionDep
from atlas.api.errors import ApiError
from atlas.api.schemas import JobOut
from atlas.jobs import create_job
from atlas.models import Job
from atlas.queue import JobQueue

router = APIRouter(prefix="/jobs", tags=["jobs"])

BOTH_SYNCS = ("sync_repo", "sync_issues")


async def queue_jobs(
    session: AsyncSession, queue: JobQueue, repo_id: int, kinds: tuple[str, ...]
) -> list[Job]:
    """Create (or reuse) a pending job of each kind, commit, then send the new ones.

    Commit first, send second: a worker that receives an id must find its job. If the
    process dies between the two, the job waits in the table until the scheduler re-sends
    it (atlas.worker.STUCK_AFTER), so it is late but never lost.
    """
    new_job_ids = []
    job_ids = []
    for kind in kinds:
        # create_job is written for the worker's synchronous sessions; run_sync lends it
        # this async session's connection instead of duplicating the logic.
        job_id, created = await session.run_sync(create_job, kind, repo_id)
        job_ids.append(job_id)
        if created:
            new_job_ids.append(job_id)
    await session.commit()

    for job_id in new_job_ids:
        # The Redis client is synchronous; a thread keeps it from blocking the event loop.
        await run_in_threadpool(queue.send, job_id)
    return list(await session.scalars(select(Job).where(Job.id.in_(job_ids)).order_by(Job.id)))


@router.get("/{job_id}", response_model=JobOut)
async def read_job(job_id: int, user: CurrentUser, session: SessionDep) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "job_not_found", "No job with that id")
    return job
