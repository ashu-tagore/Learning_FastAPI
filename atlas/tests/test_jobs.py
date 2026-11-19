"""Running jobs: deduplication, retries, dead jobs and duplicate deliveries (lessons 5.1, 5.2)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update

from atlas import jobs
from atlas.jobs import WorkerContext, backoff_seconds, create_job, run_job
from atlas.models import Document, Issue, Job, Repo
from tests.conftest import FakeClock, git_commit
from tests.fake_github import FakeIssueTracker


def add_repo(worker: WorkerContext, full_name: str = "pytorch/ignite") -> int:
    with worker.session_factory() as session, session.begin():
        repo = Repo(full_name=full_name)
        session.add(repo)
        session.flush()
        return repo.id


def queue_job(worker: WorkerContext, kind: str, repo_id: int) -> int:
    with worker.session_factory() as session, session.begin():
        job_id, _ = create_job(session, kind, repo_id)
    worker.queue.send(job_id)
    return job_id


def load_job(worker: WorkerContext, job_id: int) -> Job:
    with worker.session_factory() as session:
        return session.get_one(Job, job_id)


def indexed_paths(worker: WorkerContext) -> list[str]:
    with worker.session_factory() as session:
        return sorted(session.scalars(select(Document.path)))


def test_second_job_for_same_work_reuses_the_pending_one(worker: WorkerContext) -> None:
    repo_id = add_repo(worker)
    with worker.session_factory() as session, session.begin():
        first = create_job(session, "sync_repo", repo_id)
        second = create_job(session, "sync_repo", repo_id)
        other_kind = create_job(session, "sync_issues", repo_id)

    assert first == (first[0], True)
    assert second == (first[0], False)
    assert other_kind[1] is True


def test_finished_job_does_not_block_a_new_one(worker: WorkerContext, upstream: Path) -> None:
    repo_id = add_repo(worker)
    first = queue_job(worker, "sync_repo", repo_id)
    run_job(worker, first)

    with worker.session_factory() as session, session.begin():
        second, created = create_job(session, "sync_repo", repo_id)

    assert created
    assert second != first


def test_sync_repo_clones_and_indexes(worker: WorkerContext, upstream: Path) -> None:
    job_id = queue_job(worker, "sync_repo", add_repo(worker))

    assert run_job(worker, job_id) == "succeeded"

    job = load_job(worker, job_id)
    assert (job.status, job.attempts) == ("succeeded", 1)
    assert indexed_paths(worker) == ["README.md", "engine.py"]


def test_second_sync_indexes_only_what_changed(worker: WorkerContext, upstream: Path) -> None:
    repo_id = add_repo(worker)
    run_job(worker, queue_job(worker, "sync_repo", repo_id))
    (upstream / "engine.py").write_text("class Engine:\n    def run(self):\n        return 1\n")
    (upstream / "README.md").unlink()
    (upstream / "metrics.py").write_text("def accuracy():\n    return 1.0\n")
    head = git_commit(upstream, "second commit")

    with worker.session_factory() as session:
        untouched_before = session.scalars(select(Document.id)).all()
    run_job(worker, queue_job(worker, "sync_repo", repo_id))

    assert indexed_paths(worker) == ["engine.py", "metrics.py"]
    with worker.session_factory() as session:
        assert session.get_one(Repo, repo_id).indexed_commit == head
        # engine.py changed, so its row was replaced: no id survives from the first index.
        assert set(session.scalars(select(Document.id))).isdisjoint(untouched_before)


def test_duplicate_delivery_of_a_finished_job_is_skipped(
    worker: WorkerContext, upstream: Path
) -> None:
    job_id = queue_job(worker, "sync_repo", add_repo(worker))
    run_job(worker, job_id)

    assert run_job(worker, job_id) == "skipped"
    assert load_job(worker, job_id).attempts == 1


def test_failed_job_is_retried_with_backoff_then_dead(
    worker: WorkerContext, clock: FakeClock
) -> None:
    # No upstream fixture: the clone fails every time.
    job_id = queue_job(worker, "sync_repo", add_repo(worker))
    assert worker.queue.receive(wait_seconds=0) == job_id

    outcomes = []
    for _ in range(jobs.MAX_ATTEMPTS):
        outcomes.append(run_job(worker, job_id))
        clock.advance(4000)  # past the longest backoff

    assert outcomes == ["retrying"] * (jobs.MAX_ATTEMPTS - 1) + ["dead"]
    job = load_job(worker, job_id)
    assert job.status == "dead"
    assert job.last_error is not None and "CalledProcessError" in job.last_error


def test_a_retry_is_sent_back_to_the_queue_with_a_delay(
    worker: WorkerContext, clock: FakeClock
) -> None:
    job_id = queue_job(worker, "sync_repo", add_repo(worker))
    worker.queue.receive(wait_seconds=0)

    run_job(worker, job_id)
    worker.queue.ack(job_id)  # what the worker loop does after every run

    assert worker.queue.receive(wait_seconds=0) is None  # not straight away
    clock.advance(31)  # the first backoff is at most 30 seconds
    assert worker.queue.receive(wait_seconds=0) == job_id


def test_backoff_grows_and_is_capped() -> None:
    ceilings = [max(backoff_seconds(attempt) for _ in range(500)) for attempt in (1, 2, 3, 12)]

    assert ceilings[0] <= 30 < ceilings[1] <= 60 < ceilings[2] <= 120
    assert 3000 < ceilings[3] <= 3600


def test_sync_issues_stores_open_issues_and_watermark(
    worker: WorkerContext, issue_tracker: FakeIssueTracker
) -> None:
    issue_tracker.put(1, "Open", "2026-11-01T10:00:00Z")
    issue_tracker.put(2, "Also open", "2026-11-02T10:00:00Z")
    repo_id = add_repo(worker)

    assert run_job(worker, queue_job(worker, "sync_issues", repo_id)) == "succeeded"

    with worker.session_factory() as session:
        assert sorted(session.scalars(select(Issue.number))) == [1, 2]
        repo = session.get_one(Repo, repo_id)
        assert repo.issues_synced_until == datetime(2026, 11, 2, 10, tzinfo=UTC)
        assert repo.issues_etag is not None


def test_sync_issues_removes_closed_issues(
    worker: WorkerContext, issue_tracker: FakeIssueTracker
) -> None:
    issue_tracker.put(1, "Open", "2026-11-01T10:00:00Z")
    repo_id = add_repo(worker)
    run_job(worker, queue_job(worker, "sync_issues", repo_id))

    issue_tracker.put(1, "Open", "2026-11-03T10:00:00Z", state="closed")
    run_job(worker, queue_job(worker, "sync_issues", repo_id))

    with worker.session_factory() as session:
        assert list(session.scalars(select(Issue.number))) == []


def test_rate_limited_job_waits_for_the_reset_without_using_an_attempt(
    worker: WorkerContext, issue_tracker: FakeIssueTracker, clock: FakeClock
) -> None:
    reset_in = 600
    issue_tracker.rate_limited_until = int(datetime.now(UTC).timestamp()) + reset_in
    job_id = queue_job(worker, "sync_issues", add_repo(worker))
    worker.queue.receive(wait_seconds=0)

    assert run_job(worker, job_id) == "deferred"
    worker.queue.ack(job_id)

    job = load_job(worker, job_id)
    assert (job.status, job.attempts) == ("queued", 0)
    clock.advance(reset_in - 10)
    assert worker.queue.receive(wait_seconds=0) is None
    clock.advance(20)
    assert worker.queue.receive(wait_seconds=0) == job_id


@pytest.mark.parametrize(("started_minutes_ago", "outcome"), [(1, "skipped"), (30, "succeeded")])
def test_running_job_is_taken_over_only_after_its_lease_ends(
    worker: WorkerContext, upstream: Path, started_minutes_ago: int, outcome: str
) -> None:
    job_id = queue_job(worker, "sync_repo", add_repo(worker))
    started = datetime.now(UTC) - timedelta(minutes=started_minutes_ago)
    with worker.session_factory() as session, session.begin():
        # As if another worker claimed it and then died.
        session.execute(
            update(Job).where(Job.id == job_id).values(status="running", started_at=started)
        )

    assert run_job(worker, job_id) == outcome
