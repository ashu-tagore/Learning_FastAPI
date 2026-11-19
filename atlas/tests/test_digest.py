"""The daily digest (lesson 5.7's unguided project; these are its acceptance tests)."""

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from atlas import github, storage
from atlas.digest import build_digests
from atlas.models import Digest, Notification, User
from tests.conftest import sign_up

DAY = date(2026, 11, 19)  # the digest of 19 November covers issues opened on 18 November


def opened(number: int, when: datetime, labels: tuple[str, ...]) -> github.Issue:
    return github.Issue(
        number=number,
        title=f"Issue {number}",
        url=f"https://github.com/pytorch/ignite/issues/{number}",
        author="contributor",
        labels=labels,
        comments=0,
        created_at=when,
    )


def add_user(session: Session, email: str) -> User:
    user = User(email=email, password_hash="not-a-real-hash")
    session.add(user)
    session.flush()
    return user


def test_digest_lists_beginner_issues_opened_the_day_before(session: Session) -> None:
    yesterday_noon = datetime(2026, 11, 18, 12, tzinfo=UTC)
    issues = [
        opened(1, yesterday_noon, ("good first issue",)),
        opened(2, yesterday_noon, ("help wanted", "module: metrics")),
        opened(3, yesterday_noon, ("bug",)),  # not for beginners
        opened(4, yesterday_noon - timedelta(days=1), ("good first issue",)),  # too old
        opened(5, datetime(2026, 11, 19, 0, 30, tzinfo=UTC), ("good first issue",)),  # today
    ]
    storage.save_issues(session, "pytorch/ignite", issues)
    reader = add_user(session, "reader@example.com")

    created = build_digests(session, DAY)

    digest = session.scalars(select(Digest).where(Digest.user_id == reader.id)).one()
    assert created == 1
    assert [item["number"] for item in digest.issues] == [1, 2]
    assert digest.issues[0]["repo"] == "pytorch/ignite"


def test_building_the_same_day_twice_changes_nothing(session: Session) -> None:
    add_user(session, "reader@example.com")

    first = build_digests(session, DAY)
    second = build_digests(session, DAY)

    assert (first, second) == (1, 0)
    assert len(session.scalars(select(Digest)).all()) == 1


def test_digest_counts_the_days_notifications(session: Session) -> None:
    reader = add_user(session, "reader@example.com")
    for number, when in [
        (1, datetime(2026, 11, 18, 9, tzinfo=UTC)),
        (2, datetime(2026, 11, 17, 9, tzinfo=UTC)),
    ]:
        session.add(
            Notification(
                user_id=reader.id,
                repo_full_name="pytorch/ignite",
                issue_number=number,
                title=f"Issue {number}",
                change="closed",
                labels=[],
                event_key=f"k{number}",
                created_at=when,
            )
        )
    session.flush()

    build_digests(session, DAY)

    digest = session.scalars(select(Digest)).one()
    assert digest.notification_count == 1


@pytest.mark.anyio
async def test_latest_digest_is_the_users_own(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    reader_headers = await sign_up(client, "reader@example.com")
    before = await client.get("/digests/latest", headers=reader_headers)
    await async_session.run_sync(lambda sync: build_digests(sync, DAY - timedelta(days=1)))
    await async_session.run_sync(lambda sync: build_digests(sync, DAY))
    await async_session.commit()
    other_headers = await sign_up(client, "other@example.com")

    mine = await client.get("/digests/latest", headers=reader_headers)
    theirs = await client.get("/digests/latest", headers=other_headers)

    assert before.json()["error"]["code"] == "digest_not_found"
    assert mine.json()["day"] == "2026-11-19"
    assert theirs.json()["error"]["code"] == "digest_not_found"  # signed up after both
