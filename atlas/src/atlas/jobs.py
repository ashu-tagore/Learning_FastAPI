"""Background jobs: creating them, running them once, and deciding what happens on failure.

The rules (lessons 5.1 to 5.3):
- A job is a row in Postgres; the queue only carries its id.
- The queue delivers at least once, so running a job must be safe to repeat: a job that
  already succeeded is skipped, and the sync handlers converge on the same end state.
- A failed attempt is retried after a random, growing delay; after MAX_ATTEMPTS it is dead.
- A GitHub rate limit is not the job's fault: it waits until the limit resets, and the
  attempt is not counted.
"""

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from atlas import github, gitrepo, storage
from atlas.config import Settings
from atlas.models import Job, Repo
from atlas.queue import JobQueue
from atlas.snapshots import SnapshotStore

logger = logging.getLogger("atlas.jobs")

MAX_ATTEMPTS = 5
# A running job whose worker vanished (killed, crashed, lost its network) is taken over
# once this lease ends. Longer than any real sync; shorter than a user's patience.
LEASE = timedelta(minutes=10)


@dataclass
class WorkerContext:
    """Everything a job needs from the outside world, so tests can swap any of it."""

    session_factory: sessionmaker[Session]
    queue: JobQueue
    settings: Settings
    snapshots: SnapshotStore
    github_transport: httpx.AsyncBaseTransport | None = None


def backoff_seconds(attempts: int, base: float = 30, cap: float = 3600) -> float:
    """Full jitter: a random delay up to an exponentially growing ceiling.

    Without the randomness, every job that failed in the same outage retries at the same
    moment and causes the next outage.
    """
    ceiling = min(cap, base * 2 ** (attempts - 1))
    return random.uniform(0, ceiling)


def create_job(session: Session, kind: str, repo_id: int) -> tuple[int, bool]:
    """The pending job of this kind for this repo: (id, True) if new, (id, False) if one existed.

    The partial unique index ix_jobs_one_pending makes "at most one pending job" a rule the
    database enforces, so two requests racing each other can't both create one.
    """
    statement = (
        insert(Job)
        .values(kind=kind, repo_id=repo_id)
        .on_conflict_do_nothing(
            index_elements=["kind", "repo_id"],
            index_where=Job.status.in_(["queued", "running"]),
        )
        .returning(Job.id)
    )
    new_id = session.scalar(statement)
    if new_id is not None:
        return new_id, True

    existing_id = session.scalar(
        select(Job.id).where(
            Job.kind == kind, Job.repo_id == repo_id, Job.status.in_(["queued", "running"])
        )
    )
    if existing_id is None:
        # The pending job finished between the two statements: try once more.
        return create_job(session, kind, repo_id)
    return existing_id, False


def run_job(ctx: WorkerContext, job_id: int) -> str:
    """Run one delivery of a job and say what happened.

    "skipped": already finished or being run by another worker; nothing to do.
    "succeeded": done.
    "retrying": failed; queued again after a backoff delay.
    "deferred": GitHub's rate limit is used up; queued again for when it resets.
    "dead": failed MAX_ATTEMPTS times; a person needs to look at last_error.
    """
    job = _claim(ctx, job_id)
    if job is None:
        return "skipped"

    try:
        with ctx.session_factory() as session, session.begin():
            _run_handler(ctx, session, job)
            # Same transaction as the handler's writes: either both happen or neither does.
            # If the worker dies before this commits, the next delivery redoes the work.
            session.execute(
                update(Job)
                .where(Job.id == job.id)
                .values(status="succeeded", finished_at=datetime.now(UTC), last_error=None)
            )
    except github.RateLimited as error:
        delay = (error.retry_at - datetime.now(UTC)).total_seconds() + 1
        _requeue(ctx, job, str(error), delay, undo_attempt=True)
        return "deferred"
    except Exception as error:
        logger.exception("job failed", extra={"job_id": job.id, "kind": job.kind})
        if job.attempts >= MAX_ATTEMPTS:
            _mark_dead(ctx, job, describe(error))
            return "dead"
        _requeue(ctx, job, describe(error), backoff_seconds(job.attempts))
        return "retrying"
    return "succeeded"


def describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def _claim(ctx: WorkerContext, job_id: int) -> Job | None:
    """Mark the job running and count the attempt, unless someone else has it.

    One UPDATE decides: a duplicate delivery of a job that is running (with a live lease)
    or already finished matches no row, so exactly one worker runs each attempt.
    """
    now = datetime.now(UTC)
    is_waiting = Job.status == "queued"
    is_abandoned = (Job.status == "running") & (Job.started_at < now - LEASE)
    with ctx.session_factory() as session, session.begin():
        job = session.scalar(
            update(Job)
            .where(Job.id == job_id, is_waiting | is_abandoned)
            .values(status="running", started_at=now, attempts=Job.attempts + 1)
            .returning(Job)
        )
        if job is not None:
            session.expunge(job)
        return job


def _requeue(
    ctx: WorkerContext, job: Job, error: str, delay: float, undo_attempt: bool = False
) -> None:
    attempts = job.attempts - 1 if undo_attempt else job.attempts
    with ctx.session_factory() as session, session.begin():
        session.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(status="queued", attempts=attempts, last_error=error)
        )
    # After the commit: a worker receiving this id must find the job queued again.
    ctx.queue.send(job.id, delay_seconds=max(delay, 0))


def _mark_dead(ctx: WorkerContext, job: Job, error: str) -> None:
    with ctx.session_factory() as session, session.begin():
        session.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(status="dead", last_error=error, finished_at=datetime.now(UTC))
        )


def _run_handler(ctx: WorkerContext, session: Session, job: Job) -> None:
    repo = session.get_one(Repo, job.repo_id)
    if job.kind == "sync_repo":
        sync_repo(session, repo, ctx.settings)
        return
    if job.kind == "snapshot_repo":
        snapshot_repo(repo, ctx.settings, ctx.snapshots)
        return
    sync_issues(session, repo, ctx.settings, ctx.github_transport)


def sync_repo(session: Session, repo: Repo, settings: Settings) -> storage.IndexSummary:
    """Bring the clone and the index up to the newest commit, re-indexing only what changed."""
    path = Path(settings.repos_dir) / repo.full_name
    url = f"{settings.git_base_url}/{repo.full_name}.git"
    head = gitrepo.clone_or_update(url, path)

    previous = repo.indexed_commit
    can_diff = previous is not None and gitrepo.has_commit(path, previous)
    if previous == head:
        summary = storage.IndexSummary(documents=0, chunks=0, skipped=0)
    elif can_diff:
        assert previous is not None  # can_diff implies it; this tells mypy
        changes = gitrepo.changes_between(path, previous, head)
        summary = storage.update_index(
            session, repo.full_name, path, changed=changes.changed, deleted=changes.deleted
        )
    else:
        # First sync, or history was rewritten upstream: nothing to diff against.
        summary = storage.index_folder(session, repo.full_name, path)
    repo.indexed_commit = head
    return summary


def snapshot_repo(repo: Repo, settings: Settings, snapshots: SnapshotStore) -> bool:
    """Store a snapshot of the indexed commit, unless it's stored already; prune old ones.

    A separate job from sync_repo on purpose: if object storage is down, indexing carries
    on, and only snapshots wait for their retries.
    """
    commit = repo.indexed_commit
    if commit is None:
        return False  # never indexed: nothing to snapshot yet
    path = Path(settings.repos_dir) / repo.full_name
    url = f"{settings.git_base_url}/{repo.full_name}.git"
    # This worker may not have the clone (another one did the sync); fetch brings the
    # indexed commit, unless upstream history was rewritten since.
    gitrepo.clone_or_update(url, path)
    uploaded = snapshots.upload(repo.full_name, commit, path)
    snapshots.prune(repo.full_name, keep=settings.snapshots_kept)
    return uploaded


def sync_issues(
    session: Session,
    repo: Repo,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> github.IssueChanges:
    """Fetch only the issues that changed since the last poll, and apply them."""
    token = settings.github_token.get_secret_value() if settings.github_token else None
    changes = asyncio.run(
        github.fetch_issue_changes(
            repo.full_name,
            since=repo.issues_synced_until,
            etag=repo.issues_etag,
            token=token,
            transport=transport,
        )
    )
    if changes.not_modified:
        return changes

    storage.apply_issue_changes(
        session, repo.full_name, changes.open_issues, changes.closed_numbers
    )
    repo.issues_etag = changes.etag
    repo.issues_synced_until = changes.newest_update
    return changes
