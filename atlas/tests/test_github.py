import asyncio
from datetime import UTC, datetime

import pytest

from atlas.github import GitHubError, build_headers, fetch_issues, fetch_recent_commits
from tests.fake_github import TRANSPORT


def test_follows_pagination_and_skips_pull_requests() -> None:
    issues = asyncio.run(fetch_issues("pytorch/ignite", ["good first issue"], transport=TRANSPORT))

    assert [issue.number for issue in issues] == [12, 7]


def test_parses_issue_fields() -> None:
    issues = asyncio.run(fetch_issues("pytorch/ignite", ["good first issue"], transport=TRANSPORT))

    first = issues[0]
    assert first.title == "Fix docstring typo"
    assert first.url == "https://github.com/pytorch/ignite/issues/12"
    assert first.labels == ("good first issue",)
    assert first.created_at == datetime(2026, 11, 2, 9, 14, tzinfo=UTC)


def test_several_labels_are_merged_without_duplicates_newest_first() -> None:
    labels = ["good first issue", "help wanted"]
    issues = asyncio.run(fetch_issues("pytorch/ignite", labels, transport=TRANSPORT))

    assert [issue.number for issue in issues] == [20, 12, 7]


def test_missing_repository_is_a_clear_error() -> None:
    with pytest.raises(GitHubError, match="not found"):
        asyncio.run(fetch_issues("pytorch/nope", ["bug"], transport=TRANSPORT))


def test_rate_limit_explains_when_it_resets() -> None:
    with pytest.raises(GitHubError, match="rate limit reached; it resets at"):
        asyncio.run(fetch_issues("pytorch/limited", ["bug"], transport=TRANSPORT))


def test_token_is_sent_only_when_given() -> None:
    assert "Authorization" not in build_headers(None)
    assert build_headers("abc")["Authorization"] == "Bearer abc"


def test_recent_commits_use_short_sha_and_first_message_line() -> None:
    commits = asyncio.run(fetch_recent_commits("pytorch/ignite", 1, transport=TRANSPORT))

    assert commits[0].sha == "0abbcab"
    assert commits[0].summary == "Pin pytest to 9.1.1 (#3932)"
    assert commits[0].author == "Maintainer"
