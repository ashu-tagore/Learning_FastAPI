from datetime import UTC, datetime, timedelta
from pathlib import Path

from atlas import issue_cache
from atlas.github import Issue
from atlas.issue_cache import CachedIssues

FETCHED_AT = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
ISSUE = Issue(
    number=12,
    title="Fix docstring typo",
    url="https://github.com/pytorch/ignite/issues/12",
    author="contributor",
    labels=("good first issue",),
    comments=2,
    created_at=datetime(2026, 11, 2, 9, 14, tzinfo=UTC),
)


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "issues.json"
    original = CachedIssues("pytorch/ignite", ("good first issue",), FETCHED_AT, [ISSUE])

    issue_cache.save(path, original)

    assert issue_cache.load(path) == original


def test_missing_or_corrupt_cache_loads_as_none(tmp_path: Path) -> None:
    assert issue_cache.load(tmp_path / "missing.json") is None

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{ not json", encoding="utf-8")
    assert issue_cache.load(corrupt) is None


def test_usable_only_for_same_question_and_recent_enough() -> None:
    cached = CachedIssues("pytorch/ignite", ("good first issue",), FETCHED_AT, [ISSUE])
    labels = ("good first issue",)
    one_hour = timedelta(hours=1)

    assert cached.is_usable("pytorch/ignite", labels, FETCHED_AT + timedelta(minutes=59), one_hour)
    assert not cached.is_usable("pytorch/ignite", labels, FETCHED_AT + timedelta(hours=2), one_hour)
    assert not cached.is_usable("other/repo", labels, FETCHED_AT, one_hour)
    assert not cached.is_usable("pytorch/ignite", ("help wanted",), FETCHED_AT, one_hour)
