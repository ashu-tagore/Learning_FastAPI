"""The worker process and the scheduler (lessons 5.1 and 5.2).

    atlas worker      receive job ids from the queue and run them, one at a time
    atlas scheduler   every few minutes, queue a sync of every repository

Run as many workers as you like: the queue hands each message to one of them, and
jobs.run_job makes a duplicate delivery harmless. Run one scheduler; two would only
create jobs that the one-pending-job rule then merges.
"""

import logging
import signal
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from atlas.jobs import WorkerContext, create_job, run_job
from atlas.models import Job, Repo
from atlas.queue import WAIT_SECONDS

logger = logging.getLogger("atlas.worker")

# A queued job this old without being run has probably lost its queue message (the API
# committed the job, then crashed or lost Redis before sending). The scheduler re-sends it.
STUCK_AFTER = timedelta(minutes=5)


def work(
    ctx: WorkerContext,
    stop: threading.Event,
    wait_seconds: int = WAIT_SECONDS,
    stop_when_idle: bool = False,
) -> int:
    """Run jobs until `stop` is set; return how many deliveries were handled.

    stop_when_idle ends the loop at the first empty receive, for tests and one-off runs.
    """
    handled = 0
    while not stop.is_set():
        job_id = ctx.queue.receive(wait_seconds=wait_seconds)
        if job_id is None:
            if stop_when_idle:
                break
            continue

        outcome = run_job(ctx, job_id)
        # Acknowledge after running, never before: if this process dies mid-job, the
        # message reappears after the visibility timeout and another worker finishes it.
        ctx.queue.ack(job_id)
        handled += 1
        logger.info("job handled", extra={"job_id": job_id, "outcome": outcome})
    return handled


def schedule_once(ctx: WorkerContext) -> tuple[int, int]:
    """Queue a sync of every repository, and re-send stuck jobs; return (queued, resent)."""
    queued = 0
    with ctx.session_factory() as session, session.begin():
        repo_ids = list(session.scalars(select(Repo.id)))
        new_job_ids = []
        for repo_id in repo_ids:
            for kind in ("sync_repo", "sync_issues", "snapshot_repo"):
                job_id, created = create_job(session, kind, repo_id)
                if created:
                    new_job_ids.append(job_id)

        # Never started: a retry waiting out its backoff has started_at set and must not
        # be sent early.
        is_stuck = (
            (Job.status == "queued")
            & Job.started_at.is_(None)
            & (Job.created_at < datetime.now(UTC) - STUCK_AFTER)
        )
        stuck_job_ids = list(session.scalars(select(Job.id).where(is_stuck)))

    # Sent only after the commit, so a worker never receives the id of an uncommitted job.
    for job_id in new_job_ids:
        ctx.queue.send(job_id)
        queued += 1
    # Re-sending may duplicate a message that was only slow, not lost. That is safe:
    # at-least-once delivery already allows duplicates.
    for job_id in stuck_job_ids:
        ctx.queue.send(job_id)
    return queued, len(stuck_job_ids)


def run_scheduler(ctx: WorkerContext, stop: threading.Event) -> None:
    interval = ctx.settings.sync_interval_minutes * 60
    while not stop.is_set():
        queued, resent = schedule_once(ctx)
        logger.info("scheduled syncs", extra={"queued": queued, "resent": resent})
        # Event.wait instead of time.sleep: a stop signal ends the wait immediately.
        stop.wait(interval)


def stop_on_signals() -> threading.Event:
    """An event set by Ctrl+C or SIGTERM (what Docker and ECS send before killing a process).

    The loops check it between jobs, so a job that has started is allowed to finish.
    """
    stop = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        logger.info("stopping after the current job", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    return stop
