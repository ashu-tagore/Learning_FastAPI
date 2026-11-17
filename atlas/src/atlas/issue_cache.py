"""Save fetched issues to disk so repeated runs don't call GitHub every time.

Unauthenticated clients get 60 GitHub requests per hour, and each request takes
a noticeable fraction of a second. A recent copy on disk is usually good enough.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from atlas.github import Issue


@dataclass(frozen=True)
class CachedIssues:
    repo: str
    labels: tuple[str, ...]
    fetched_at: datetime
    issues: list[Issue]

    def is_usable(
        self, repo: str, labels: tuple[str, ...], now: datetime, max_age: timedelta
    ) -> bool:
        """True if this copy answers the same question and is recent enough."""
        same_question = self.repo == repo and set(self.labels) == set(labels)
        recent_enough = now - self.fetched_at <= max_age
        return same_question and recent_enough


def default_cache_path(repo: str) -> Path:
    owner, name = repo.split("/")
    return Path.home() / ".cache" / "atlas" / f"issues-{owner}-{name}.json"


def save(path: Path, cached: CachedIssues) -> None:
    record = {
        "repo": cached.repo,
        "labels": list(cached.labels),
        "fetched_at": cached.fetched_at.isoformat(),
        "issues": [
            {
                "number": issue.number,
                "title": issue.title,
                "url": issue.url,
                "author": issue.author,
                "labels": list(issue.labels),
                "comments": issue.comments,
                "created_at": issue.created_at.isoformat(),
            }
            for issue in cached.issues
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")


def load(path: Path) -> CachedIssues | None:
    """The cached copy, or None if there isn't a readable one."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        # A missing or half-written cache file is not an error: we just fetch again.
        return None

    issues = [
        Issue(
            number=item["number"],
            title=item["title"],
            url=item["url"],
            author=item["author"],
            labels=tuple(item["labels"]),
            comments=item["comments"],
            created_at=datetime.fromisoformat(item["created_at"]),
        )
        for item in record["issues"]
    ]
    return CachedIssues(
        repo=record["repo"],
        labels=tuple(record["labels"]),
        fetched_at=datetime.fromisoformat(record["fetched_at"]),
        issues=issues,
    )
