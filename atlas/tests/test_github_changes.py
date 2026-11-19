"""Incremental issue polling with `since` and conditional requests (lesson 5.3)."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from atlas.github import IssueChanges, RateLimited, fetch_issue_changes
from tests.fake_github import FakeIssueTracker


def poll(tracker: FakeIssueTracker, since: datetime | None, etag: str | None) -> IssueChanges:
    return asyncio.run(
        fetch_issue_changes("pytorch/ignite", since=since, etag=etag, transport=tracker.transport())
    )


def test_first_poll_returns_open_issues_and_where_to_continue() -> None:
    tracker = FakeIssueTracker()
    tracker.put(1, "Old and open", "2026-11-01T10:00:00Z")
    tracker.put(2, "Closed long ago", "2026-11-02T10:00:00Z", state="closed")
    tracker.put(3, "Newer and open", "2026-11-03T10:00:00Z")

    changes = poll(tracker, since=None, etag=None)

    assert [issue.number for issue in changes.open_issues] == [1, 3]
    assert changes.closed_numbers == []
    assert changes.newest_update == datetime(2026, 11, 3, 10, tzinfo=UTC)
    assert changes.etag is not None


def test_unchanged_repository_answers_304_and_changes_nothing() -> None:
    tracker = FakeIssueTracker()
    tracker.put(1, "Open", "2026-11-01T10:00:00Z")
    first = poll(tracker, since=None, etag=None)
    second = poll(tracker, since=first.newest_update, etag=None)

    third = poll(tracker, since=second.newest_update, etag=second.etag)

    assert third.not_modified
    assert third.newest_update == second.newest_update
    assert tracker.requests[-1].headers["If-None-Match"] == second.etag


def test_later_poll_sees_new_updated_and_closed_issues_only() -> None:
    tracker = FakeIssueTracker()
    tracker.put(1, "Will be closed", "2026-11-01T10:00:00Z")
    tracker.put(2, "Untouched", "2026-11-01T11:00:00Z")
    first = poll(tracker, since=None, etag=None)

    tracker.put(1, "Will be closed", "2026-11-05T10:00:00Z", state="closed")
    tracker.put(3, "Brand new", "2026-11-05T11:00:00Z")
    changes = poll(tracker, since=first.newest_update, etag=first.etag)

    assert changes.closed_numbers == [1]
    # Issue 2 was last updated exactly at `since`. GitHub's docs don't say whether such an
    # issue is included; this fake includes it, and storing it again is harmless.
    assert [issue.number for issue in changes.open_issues] == [2, 3]
    assert changes.newest_update == datetime(2026, 11, 5, 11, tzinfo=UTC)
    assert tracker.requests[-1].url.params["state"] == "all"


def test_rate_limit_says_when_to_try_again() -> None:
    tracker = FakeIssueTracker()
    tracker.rate_limited_until = 1795440000

    with pytest.raises(RateLimited) as caught:
        poll(tracker, since=None, etag=None)

    assert caught.value.retry_at == datetime.fromtimestamp(1795440000, tz=UTC)


def test_secondary_rate_limit_waits_as_long_as_retry_after_says() -> None:
    def too_fast(request: httpx.Request) -> httpx.Response:
        message = {"message": "You have exceeded a secondary rate limit."}
        return httpx.Response(403, json=message, headers={"retry-after": "120"})

    before = datetime.now(UTC)
    with pytest.raises(RateLimited) as caught:
        asyncio.run(
            fetch_issue_changes(
                "pytorch/ignite", since=None, etag=None, transport=httpx.MockTransport(too_fast)
            )
        )

    waited = caught.value.retry_at - before
    assert timedelta(seconds=119) < waited < timedelta(seconds=125)
