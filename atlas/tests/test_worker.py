"""The worker loop and the scheduler (lessons 5.1 and 5.2)."""

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update

from atlas.jobs import WorkerContext
from atlas.models import Document, Job, Repo
from atlas.worker import schedule_once, work
from tests.conftest import FakeClock


def add_repo(worker: WorkerContext) -> int:
    with worker.session_factory() as session, session.begin():
        repo = Repo(full_name="pytorch/ignite")
        session.add(repo)
        session.flush()
        return repo.id


def run_until_idle(worker: WorkerContext) -> int:
    return work(worker, threading.Event(), wait_seconds=0, stop_when_idle=True)


def test_scheduler_queues_every_job_kind_and_worker_runs_them(
    worker: WorkerContext, upstream: Path
) -> None:
    add_repo(worker)

    assert schedule_once(worker) == (4, 0)
    # The snapshot and embed jobs may run before the code sync and find nothing to do
    # yet; the next scheduler run catches up. Each job converges on its own.
    assert run_until_idle(worker) == 4

    with worker.session_factory() as session:
        statuses = sorted(session.scalars(select(Job.status)))
        assert statuses == ["succeeded"] * 4
        assert session.scalar(select(Document.id).limit(1)) is not None


def test_scheduler_does_not_pile_up_jobs_nobody_has_run(worker: WorkerContext) -> None:
    add_repo(worker)

    first = schedule_once(worker)
    second = schedule_once(worker)

    assert (first, second) == ((4, 0), (0, 0))
    with worker.session_factory() as session:
        assert len(session.scalars(select(Job.id)).all()) == 4


def test_scheduler_resends_a_job_whose_message_was_lost(worker: WorkerContext) -> None:
    add_repo(worker)
    schedule_once(worker)
    # Lose all four messages, as if Redis had restarted without saving them.
    while worker.queue.receive(wait_seconds=0) is not None:
        pass
    an_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    with worker.session_factory() as session, session.begin():
        session.execute(update(Job).values(created_at=an_hour_ago))

    assert schedule_once(worker) == (0, 4)


def test_stop_event_ends_the_loop_before_the_next_job(worker: WorkerContext) -> None:
    stop = threading.Event()
    stop.set()
    worker.queue.send(12345)

    assert work(worker, stop, wait_seconds=0) == 0


class WorkerDied(BaseException):
    """Stands in for the process being killed mid-job. A BaseException, like SystemExit,
    so `except Exception` in run_job doesn't treat it as an ordinary job failure."""


def test_a_job_whose_worker_dies_is_delivered_again(
    worker: WorkerContext, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker.queue.send(42)

    def die(ctx: WorkerContext, job_id: int) -> str:
        raise WorkerDied

    monkeypatch.setattr("atlas.worker.run_job", die)
    with pytest.raises(WorkerDied):
        work(worker, threading.Event(), wait_seconds=0)
    clock.advance(61)  # past the test queue's 60-second visibility timeout

    assert worker.queue.receive(wait_seconds=0) == 42
