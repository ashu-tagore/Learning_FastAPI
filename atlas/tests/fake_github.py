"""A fake GitHub API for tests: real httpx requests, answered in memory, no network."""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

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


class FakeGitHubSignIn:
    """GitHub's sign-in, faked closely enough to test the attacks in lesson 4.9.

    Like GitHub, it issues a code when the user approves, remembers the PKCE challenge
    that code was issued for, accepts each code once, and only for the matching verifier.
    """

    def __init__(self) -> None:
        self.issued: dict[str, tuple[str | None, dict[str, object]]] = {}
        self.access_tokens: dict[str, dict[str, object]] = {}
        self.transport = httpx.MockTransport(self.handle)

    def approve(
        self, authorize_url: str, github_id: int = 4242, login: str = "octo-learner"
    ) -> str:
        """What happens at GitHub when the user clicks "Authorize": a code for this request."""
        query = parse_qs(urlparse(authorize_url).query)
        code = f"code-{len(self.issued) + 1}"
        # Without PKCE there is no challenge, and GitHub accepts the code with any verifier.
        challenge = query["code_challenge"][0] if "code_challenge" in query else None
        self.issued[code] = (challenge, {"id": github_id, "login": login})
        return code

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return self.exchange(dict(httpx.QueryParams(request.content.decode())))
        if request.url.path == "/user":
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            user = self.access_tokens.get(token)
            if user is None:
                return httpx.Response(401, json={"message": "Bad credentials"})
            return httpx.Response(200, json=user)
        return httpx.Response(404, json={"message": "Not Found"})

    def exchange(self, form: dict[str, str]) -> httpx.Response:
        code = form.get("code", "")
        issued = self.issued.pop(code, None)  # a code works once, even if this attempt fails
        if issued is None:
            return bad_code()
        challenge, user = issued
        verifier = form.get("code_verifier", "")
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        verifier_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        verifier_is_wrong = challenge is not None and not secrets.compare_digest(
            verifier_challenge, challenge
        )
        if verifier_is_wrong:
            return bad_code()
        access_token = f"gho_fake_{code}"
        self.access_tokens[access_token] = user
        return httpx.Response(200, json={"access_token": access_token, "token_type": "bearer"})


def bad_code() -> httpx.Response:
    """The shape of GitHub's answer to a code it won't accept."""
    return httpx.Response(
        200,
        json={
            "error": "bad_verification_code",
            "error_description": "The code passed is incorrect or expired.",
        },
    )
