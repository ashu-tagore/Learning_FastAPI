"""Read-only access to the GitHub REST API: open issues and recent commits.

Atlas never writes to GitHub, so this module only ever sends GET requests.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"


class GitHubError(Exception):
    """A problem talking to GitHub, explained in words a user can act on."""


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    url: str
    author: str
    labels: tuple[str, ...]
    comments: int
    created_at: datetime


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    committed_at: datetime
    summary: str  # first line of the commit message


def build_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests without a User-Agent.
        "User-Agent": "atlas-course",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def check_response(response: httpx.Response) -> None:
    """Raise GitHubError with a clear message unless the response succeeded."""
    if response.is_success:
        return

    out_of_requests = response.headers.get("x-ratelimit-remaining") == "0"
    if out_of_requests:
        reset_epoch = int(response.headers.get("x-ratelimit-reset", "0"))
        reset_at = datetime.fromtimestamp(reset_epoch, tz=UTC).strftime("%H:%M UTC")
        raise GitHubError(f"GitHub rate limit reached; it resets at {reset_at}")

    if response.status_code == 404:
        raise GitHubError(f"not found on GitHub: {response.request.url.path}")

    raise GitHubError(f"GitHub answered {response.status_code}: {response.text[:200]}")


def parse_issue(item: dict[str, Any]) -> Issue:
    return Issue(
        number=item["number"],
        title=item["title"],
        url=item["html_url"],
        author=item["user"]["login"],
        labels=tuple(label["name"] for label in item["labels"]),
        comments=item["comments"],
        created_at=datetime.fromisoformat(item["created_at"]),
    )


async def fetch_label(
    client: httpx.AsyncClient, repo: str, label: str, max_pages: int
) -> list[Issue]:
    """All open issues in repo carrying one label, following pagination."""
    url = f"/repos/{repo}/issues"
    params: dict[str, str | int] | None = {"state": "open", "labels": label, "per_page": 100}
    issues: list[Issue] = []

    for _ in range(max_pages):
        response = await client.get(url, params=params)
        check_response(response)

        for item in response.json():
            # The issues endpoint also returns pull requests; they carry this key.
            is_pull_request = "pull_request" in item
            if is_pull_request:
                continue
            issues.append(parse_issue(item))

        next_page = response.links.get("next")
        if next_page is None:
            break
        # The "next" link already contains every query parameter.
        url = next_page["url"]
        params = None

    return issues


async def fetch_issues(
    repo: str,
    labels: list[str],
    *,
    token: str | None = None,
    max_pages: int = 10,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[Issue]:
    """Open issues having any of the labels, newest first.

    GitHub's `labels=a,b` filter means "has a AND b", so each label is fetched
    separately. The requests are independent, which is why they run concurrently.
    `transport` lets tests replace the network with a fake GitHub.
    """
    async with httpx.AsyncClient(
        base_url=GITHUB_API, headers=build_headers(token), timeout=10.0, transport=transport
    ) as client:
        results_per_label = await asyncio.gather(
            *(fetch_label(client, repo, label, max_pages) for label in labels)
        )

    # An issue with two requested labels comes back twice; keep one copy.
    unique: dict[int, Issue] = {}
    for issues in results_per_label:
        for issue in issues:
            unique[issue.number] = issue
    return sorted(unique.values(), key=lambda issue: issue.number, reverse=True)


async def fetch_recent_commits(
    repo: str,
    limit: int,
    *,
    token: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[Commit]:
    """The most recent commits on the default branch, newest first (at most 100)."""
    async with httpx.AsyncClient(
        base_url=GITHUB_API, headers=build_headers(token), timeout=10.0, transport=transport
    ) as client:
        response = await client.get(f"/repos/{repo}/commits", params={"per_page": limit})
        check_response(response)

    commits = []
    for item in response.json():
        details = item["commit"]
        commits.append(
            Commit(
                sha=item["sha"][:7],
                author=details["author"]["name"],
                committed_at=datetime.fromisoformat(details["committer"]["date"]),
                summary=details["message"].splitlines()[0],
            )
        )
    return commits
