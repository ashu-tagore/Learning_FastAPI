"""A fake GitHub API for tests: real httpx requests, answered in memory, no network."""

import httpx


def issue_json(number: int, title: str, labels: list[str]) -> dict[str, object]:
    """One issue in the shape the GitHub REST API returns (only the fields Atlas reads)."""
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/pytorch/ignite/issues/{number}",
        "user": {"login": "contributor"},
        "labels": [{"name": label} for label in labels],
        "comments": 2,
        "created_at": "2026-11-02T09:14:00Z",
    }


def pull_request_json(number: int) -> dict[str, object]:
    pull = issue_json(number, "A pull request, not an issue", ["good first issue"])
    pull["pull_request"] = {"url": f"https://api.github.com/repos/pytorch/ignite/pulls/{number}"}
    return pull


def fake_github(request: httpx.Request) -> httpx.Response:
    """Answers the requests Atlas makes, the way GitHub would."""
    label = request.url.params.get("labels")
    page = request.url.params.get("page", "1")
    path = request.url.path

    if path == "/repos/pytorch/ignite/issues" and label == "good first issue" and page == "1":
        next_url = (
            "https://api.github.com/repos/pytorch/ignite/issues"
            "?state=open&labels=good+first+issue&per_page=100&page=2"
        )
        return httpx.Response(
            200,
            json=[
                issue_json(12, "Fix docstring typo", ["good first issue"]),
                pull_request_json(11),
            ],
            headers={"Link": f'<{next_url}>; rel="next"'},
        )
    if path == "/repos/pytorch/ignite/issues" and label == "good first issue" and page == "2":
        return httpx.Response(
            200, json=[issue_json(7, "Add example to Engine", ["good first issue", "help wanted"])]
        )
    if path == "/repos/pytorch/ignite/issues" and label == "help wanted":
        return httpx.Response(
            200,
            json=[
                issue_json(7, "Add example to Engine", ["good first issue", "help wanted"]),
                issue_json(20, "Support new metric", ["help wanted"]),
            ],
        )
    if path == "/repos/pytorch/ignite/commits":
        commit = {
            "sha": "0abbcab1234567890",
            "commit": {
                "author": {"name": "Maintainer"},
                "committer": {"date": "2026-11-15T10:00:00Z"},
                "message": "Pin pytest to 9.1.1 (#3932)\n\nLonger explanation.",
            },
        }
        return httpx.Response(200, json=[commit])
    if path == "/repos/pytorch/limited/issues":
        return httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1795440000"},
        )
    return httpx.Response(404, json={"message": "Not Found"})


TRANSPORT = httpx.MockTransport(fake_github)


def fake_github_oauth(request: httpx.Request) -> httpx.Response:
    """GitHub's sign-in endpoints: the code exchange and the "who am I" call."""
    if request.url.path == "/login/oauth/access_token":
        form = dict(httpx.QueryParams(request.content.decode()))
        is_good_code = form.get("code") == "good-code" and bool(form.get("code_verifier"))
        if not is_good_code:
            return httpx.Response(
                200,
                json={
                    "error": "bad_verification_code",
                    "error_description": "The code passed is incorrect or expired.",
                },
            )
        return httpx.Response(200, json={"access_token": "gho_fake", "token_type": "bearer"})
    if request.url.path == "/user":
        return httpx.Response(200, json={"id": 4242, "login": "octo-learner"})
    return httpx.Response(404, json={"message": "Not Found"})


OAUTH_TRANSPORT = httpx.MockTransport(fake_github_oauth)
