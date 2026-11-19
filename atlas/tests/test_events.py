"""Issue events on a Redis Stream, and the notifier that consumes them (lesson 5.6)."""

import threading
import time
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from atlas import github, storage
from atlas.events import EventStream, IssueEvent
from atlas.jobs import WorkerContext, run_job
from atlas.models import Issue, Notification, User, saved_issues
from atlas.notifier import notify, run_notifier
from tests.conftest import sign_up
from tests.fake_github import FakeIssueTracker
from tests.test_jobs import add_repo, queue_job


def fetched(number: int, labels: tuple[str, ...] = ("good first issue",)) -> github.Issue:
    return github.Issue(
        number=number,
        title=f"Issue {number}",
        url=f"https://github.com/pytorch/ignite/issues/{number}",
        author="contributor",
        labels=labels,
        comments=0,
        created_at=datetime(2026, 11, 1, tzinfo=UTC),
    )


def save_issue(session: Session, email: str, number: int) -> int:
    user = User(email=email, password_hash="not-a-real-hash")
    session.add(user)
    session.flush()
    issue_id = session.scalar(select(Issue.id).where(Issue.number == number))
    session.execute(insert(saved_issues).values(user_id=user.id, issue_id=issue_id))
    return user.id


def event(
    key: str = "pytorch/ignite#1:closed:first", saved_by: list[int] | None = None
) -> IssueEvent:
    return IssueEvent(
        repo="pytorch/ignite",
        number=1,
        title="Issue 1",
        change="closed",
        labels=["good first issue"],
        saved_by=saved_by or [],
        key=key,
    )


# ---------- What the producer reports ----------


def test_changes_are_reported_with_who_had_saved_the_issue(session: Session) -> None:
    storage.apply_issue_changes(session, "pytorch/ignite", [fetched(1), fetched(2)], [], poll="a")
    reader = save_issue(session, "reader@example.com", 1)

    events = storage.apply_issue_changes(
        session,
        "pytorch/ignite",
        [fetched(2, labels=("good first issue", "help wanted")), fetched(3)],
        [1],
        poll="b",
    )

    summary = [(e.number, e.change, e.saved_by, e.key) for e in events]
    assert summary == [
        # Closed, and deleted with its saved_issues rows; the event still knows who saved it.
        (1, "closed", [reader], "pytorch/ignite#1:closed:b"),
        (2, "relabelled", [], "pytorch/ignite#2:relabelled:b"),
        (3, "opened", [], "pytorch/ignite#3:opened:b"),
    ]


# ---------- The stream ----------


@pytest.fixture
def stream(worker: WorkerContext) -> EventStream:
    return worker.events


def test_a_group_reads_each_event_once_and_acknowledges_it(stream: EventStream) -> None:
    stream.ensure_group("g")
    stream.publish([event("k1"), event("k2")])

    first = stream.read("g", "worker-1", block_ms=0)
    stream.ack("g", first[0][0])
    stream.ack("g", first[1][0])
    again = stream.read("g", "worker-2", block_ms=0, claim_after_ms=0)

    assert [e.key for _, e in first] == ["k1", "k2"]
    assert again == []


def test_every_group_gets_every_event(stream: EventStream) -> None:
    stream.ensure_group("notifications")
    stream.ensure_group("digest")
    stream.publish([event("k1")])

    for_notifications = stream.read("notifications", "a", block_ms=0)
    for_digest = stream.read("digest", "b", block_ms=0)

    assert [e.key for _, e in for_notifications] == [e.key for _, e in for_digest] == ["k1"]


def test_an_event_its_consumer_never_acknowledged_is_claimed_by_another(
    stream: EventStream,
) -> None:
    stream.ensure_group("g")
    stream.publish([event("k1")])
    taken = stream.read("g", "crashed-worker", block_ms=0)

    still_fresh = stream.read("g", "worker-2", block_ms=0, claim_after_ms=60_000)
    time.sleep(0.1)  # Redis measures idle time on its own clock
    claimed = stream.read("g", "worker-2", block_ms=0, claim_after_ms=50)

    assert [e.key for _, e in taken] == ["k1"]
    assert still_fresh == []
    assert [e.key for _, e in claimed] == ["k1"]


# ---------- The notifier ----------


def test_a_duplicate_event_creates_no_second_notification(session: Session) -> None:
    storage.apply_issue_changes(session, "pytorch/ignite", [fetched(1)], [])
    reader = save_issue(session, "reader@example.com", 1)

    first = notify(session, event(saved_by=[reader]))
    second = notify(session, event(saved_by=[reader]))

    assert (first, second) == (1, 0)


def test_closing_a_saved_issue_notifies_the_reader(
    worker: WorkerContext, issue_tracker: FakeIssueTracker
) -> None:
    issue_tracker.put(1, "Fix docstring typo", "2026-11-01T10:00:00Z")
    repo_id = add_repo(worker)
    run_job(worker, queue_job(worker, "sync_issues", repo_id))
    with worker.session_factory() as session, session.begin():
        reader = save_issue(session, "reader@example.com", 1)

    issue_tracker.put(1, "Fix docstring typo", "2026-11-03T10:00:00Z", state="closed")
    run_job(worker, queue_job(worker, "sync_issues", repo_id))
    handled = run_notifier(
        worker.session_factory, worker.events, threading.Event(), "test", stop_when_idle=True
    )

    with worker.session_factory() as session:
        notifications = session.scalars(select(Notification)).all()
    # Two syncs: the first issue's "opened" and its "closed"; only the second concerns anyone.
    assert handled == 2
    assert [(n.user_id, n.issue_number, n.change) for n in notifications] == [(reader, 1, "closed")]


# ---------- The API ----------


@pytest.mark.anyio
async def test_users_see_only_their_own_notifications(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    await async_session.run_sync(
        lambda sync: storage.apply_issue_changes(sync, "pytorch/ignite", [fetched(1)], [])
    )
    reader_headers = await sign_up(client, "reader@example.com")
    await sign_up(client, "other@example.com")
    reader_id = await async_session.scalar(
        select(User.id).where(User.email == "reader@example.com")
    )
    assert reader_id is not None
    await async_session.run_sync(lambda sync: notify(sync, event(saved_by=[reader_id])))
    await async_session.commit()
    other_headers = await sign_up(client, "other@example.com")

    mine = await client.get("/notifications", headers=reader_headers)
    theirs = await client.get("/notifications", headers=other_headers)

    assert [(n["issue_number"], n["change"]) for n in mine.json()] == [(1, "closed")]
    assert theirs.json() == []
