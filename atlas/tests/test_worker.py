"""The worker loop and the scheduler (lessons 5.1 and 5.2)."""

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update

from atlas.jobs import WorkerContext
from atlas.models import Document, Job, Repo
from atlas.worker import schedule_once, work


def add_repo(worker: WorkerContext) -> int:
    with worker.session_factory() as session, session.begin():
        repo = Repo(full_name="pytorch/ignite")
        session.add(repo)
        session.flush()
        return repo.id


def run_until_idle(worker: WorkerContext) -> int:
    return work(worker, threading.Event(), wait_seconds=0, stop_when_idle=True)


def test_scheduler_queues_both_syncs_and_worker_runs_them(
    worker: WorkerContext, upstream: Path
) -> None:
    add_repo(worker)

    assert schedule_once(worker) == (2, 0)
    assert run_until_idle(worker) == 2

    with worker.session_factory() as session:
        statuses = sorted(session.scalars(select(Job.status)))
        assert statuses == ["succeeded", "succeeded"]
        assert session.scalar(select(Document.id).limit(1)) is not None


def test_scheduler_does_not_pile_up_jobs_nobody_has_run(worker: WorkerContext) -> None:
    add_repo(worker)

    first = schedule_once(worker)
    second = schedule_once(worker)

    assert (first, second) == ((2, 0), (0, 0))
    with worker.session_factory() as session:
        assert len(session.scalars(select(Job.id)).all()) == 2


def test_scheduler_resends_a_job_whose_message_was_lost(worker: WorkerContext) -> None:
    add_repo(worker)
    schedule_once(worker)
    # Lose both messages, as if Redis had restarted without saving them.
    while worker.queue.receive(wait_seconds=0) is not None:
        pass
    an_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    with worker.session_factory() as session, session.begin():
        session.execute(update(Job).values(created_at=an_hour_ago))

    assert schedule_once(worker) == (0, 2)


def test_stop_event_ends_the_loop_before_the_next_job(worker: WorkerContext) -> None:
    stop = threading.Event()
    stop.set()
    worker.queue.send(12345)

    assert work(worker, stop, wait_seconds=0) == 0
