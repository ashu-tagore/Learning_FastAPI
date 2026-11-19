"""Sign in with GitHub (lesson 4.9), including the two attacks it must stop."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.models import User
from tests.conftest import browser
from tests.fake_github import FakeGitHubSignIn

pytestmark = pytest.mark.anyio


async def start_sign_in(client: httpx.AsyncClient) -> tuple[str, str]:
    """Click "Sign in with GitHub": return GitHub's authorize URL and the state Atlas sent."""
    response = await client.get("/auth/github/login", follow_redirects=False)
    authorize_url = response.headers["location"]
    state = parse_qs(urlparse(authorize_url).query)["state"][0]
    return authorize_url, state


async def test_github_login_redirects_with_state_and_pkce(client: httpx.AsyncClient) -> None:
    response = await client.get("/auth/github/login", follow_redirects=False)

    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    query = parse_qs(location.query)
    assert location.netloc == "github.com"
    assert "scope" not in query
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43
    assert query["state"][0] == client.cookies["atlas_oauth"].split(".")[0]


async def test_github_callback_creates_then_reuses_the_account(
    client: httpx.AsyncClient, async_session: AsyncSession, github: FakeGitHubSignIn
) -> None:
    for _ in range(2):
        authorize_url, state = await start_sign_in(client)
        code = github.approve(authorize_url)
        response = await client.get("/auth/github/callback", params={"code": code, "state": state})
        assert response.status_code == 200
        assert set(response.json()) >= {"access_token", "refresh_token"}

    accounts = (await async_session.scalars(select(User).where(User.github_id == 4242))).all()
    assert [account.github_login for account in accounts] == ["octo-learner"]


async def test_github_callback_rejects_a_state_it_did_not_issue(
    client: httpx.AsyncClient, github: FakeGitHubSignIn
) -> None:
    authorize_url, _ = await start_sign_in(client)
    code = github.approve(authorize_url)

    response = await client.get("/auth/github/callback", params={"code": code, "state": "forged"})

    assert response.status_code == 400


async def test_github_callback_reports_a_bad_code(client: httpx.AsyncClient) -> None:
    _, state = await start_sign_in(client)

    response = await client.get(
        "/auth/github/callback", params={"code": "stale-code", "state": state}
    )

    assert response.status_code == 502
    assert "incorrect or expired" in response.json()["error"]["message"]


async def test_login_csrf_a_link_to_the_attackers_sign_in_is_refused(
    api_app: FastAPI, async_session: AsyncSession, github: FakeGitHubSignIn
) -> None:
    async with browser(api_app) as attacker, browser(api_app) as victim:
        # The attacker starts a sign-in with their own GitHub account, approves it,
        # and stops before the callback: they now hold a valid code and state.
        authorize_url, state = await start_sign_in(attacker)
        code = github.approve(authorize_url, github_id=6666, login="attacker")

        # They send the victim a link to the callback. The victim's browser has no cookie
        # from this sign-in, because it never started one.
        response = await victim.get("/auth/github/callback", params={"code": code, "state": state})

    assert response.status_code == 400
    assert await async_session.scalar(select(User).where(User.github_id == 6666)) is None


async def test_a_stolen_code_is_useless_without_its_verifier(
    api_app: FastAPI, async_session: AsyncSession, github: FakeGitHubSignIn
) -> None:
    async with browser(api_app) as victim, browser(api_app) as attacker:
        authorize_url, _ = await start_sign_in(victim)
        stolen_code = github.approve(authorize_url, github_id=4242, login="victim")

        # The attacker intercepts the code and replays it in a sign-in they started
        # themselves, so their state check passes; but their verifier is their own.
        _, attacker_state = await start_sign_in(attacker)
        response = await attacker.get(
            "/auth/github/callback", params={"code": stolen_code, "state": attacker_state}
        )

    assert response.status_code == 502
    assert await async_session.scalar(select(User).where(User.github_id == 4242)) is None
