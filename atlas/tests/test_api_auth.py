from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from atlas.api.app import create_app
from atlas.api.deps import get_session
from atlas.config import ApiSettings
from atlas.models import User
from tests.conftest import sign_up

pytestmark = pytest.mark.anyio


async def test_health_and_readiness(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/ready")).json() == {"status": "ready"}


async def test_ready_answers_503_when_the_database_is_unreachable(
    api_settings: ApiSettings,
) -> None:
    app = create_app(api_settings)
    # Port 1 refuses connections: as if Postgres were down.
    unreachable = create_async_engine(
        "postgresql+psycopg://atlas:unused@localhost:1/atlas", poolclass=NullPool
    )

    async def broken_session() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(unreachable) as session:
            yield session

    app.dependency_overrides[get_session] = broken_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ready = await client.get("/ready")
        health = await client.get("/health")

    assert ready.status_code == 503
    assert ready.json()["error"]["code"] == "service_unavailable"
    assert health.status_code == 200


async def test_register_never_returns_the_password(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/auth/register", json={"email": "Ada@Example.com", "password": "a-long-password"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ada@example.com"
    assert body["role"] == "user"
    assert "password" not in body and "password_hash" not in body


async def test_passwords_are_stored_as_argon2_hashes(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    await sign_up(client, "ada@example.com")

    stored = await async_session.scalar(
        select(User.password_hash).where(User.email == "ada@example.com")
    )
    assert stored is not None and stored.startswith("$argon2")


async def test_duplicate_email_is_a_conflict_in_the_standard_error_shape(
    client: httpx.AsyncClient,
) -> None:
    await sign_up(client, "ada@example.com")

    response = await client.post(
        "/auth/register", json={"email": "ada@example.com", "password": "another-long-one"}
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "conflict"
    assert error["message"] == "An account with this email exists"
    assert error["request_id"] == response.headers["X-Request-ID"]


async def test_invalid_input_lists_each_problem(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/auth/register", json={"email": "not-an-email", "password": "short"}
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    locations = [detail["location"] for detail in error["details"]]
    assert ["body", "email"] in locations and ["body", "password"] in locations


async def test_login_failures_look_identical(client: httpx.AsyncClient) -> None:
    await sign_up(client, "ada@example.com")

    wrong_password = await client.post(
        "/auth/login", data={"username": "ada@example.com", "password": "wrong-password"}
    )
    unknown_email = await client.post(
        "/auth/login", data={"username": "nobody@example.com", "password": "wrong-password"}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json()["error"]["message"] == unknown_email.json()["error"]["message"]


async def test_me_requires_a_valid_token(client: httpx.AsyncClient) -> None:
    headers = await sign_up(client, "ada@example.com")

    me = await client.get("/users/me", headers=headers)
    assert me.json()["email"] == "ada@example.com"

    missing = await client.get("/users/me")
    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"

    tampered = {"Authorization": headers["Authorization"][:-2] + "xx"}
    assert (await client.get("/users/me", headers=tampered)).status_code == 401


async def test_patch_changes_only_the_fields_sent(client: httpx.AsyncClient) -> None:
    headers = await sign_up(client, "ada@example.com")

    response = await client.patch(
        "/users/me", json={"skills_profile": "metrics, handlers"}, headers=headers
    )

    assert response.json()["skills_profile"] == "metrics, handlers"
    assert response.json()["email"] == "ada@example.com"


async def test_patch_rejects_null_for_a_field_that_cannot_be_empty(
    client: httpx.AsyncClient,
) -> None:
    headers = await sign_up(client, "ada@example.com")
    await client.patch("/users/me", json={"skills_profile": "metrics"}, headers=headers)

    response = await client.patch("/users/me", json={"skills_profile": None}, headers=headers)

    # "Absent" means "leave it alone"; null would mean "store nothing", which the
    # column forbids. It must be the client's error (422), not a crash (500).
    assert response.status_code == 422
    me = await client.get("/users/me", headers=headers)
    assert me.json()["skills_profile"] == "metrics"


async def login_tokens(client: httpx.AsyncClient) -> dict[str, str]:
    await client.post(
        "/auth/register", json={"email": "ada@example.com", "password": "a-long-password"}
    )
    response = await client.post(
        "/auth/login", data={"username": "ada@example.com", "password": "a-long-password"}
    )
    tokens: dict[str, str] = response.json()
    return tokens


async def test_refresh_rotates_tokens(client: httpx.AsyncClient) -> None:
    first = await login_tokens(client)

    response = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})

    assert response.status_code == 200
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]
    me = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {second['access_token']}"}
    )
    assert me.status_code == 200


async def test_reusing_a_refresh_token_signs_out_everywhere(client: httpx.AsyncClient) -> None:
    first = await login_tokens(client)
    rotated = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    second = rotated.json()

    # The first token comes back: maybe stolen. Every token for this user is revoked.
    reuse = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    later = await client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})

    assert reuse.status_code == 401
    assert later.status_code == 401


async def test_logout_revokes_the_refresh_token(client: httpx.AsyncClient) -> None:
    tokens = await login_tokens(client)

    logout = await client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert logout.status_code == 204
    after = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert after.status_code == 401


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


async def test_github_callback_rejects_a_state_it_did_not_issue(client: httpx.AsyncClient) -> None:
    await client.get("/auth/github/login", follow_redirects=False)

    response = await client.get(
        "/auth/github/callback", params={"code": "good-code", "state": "forged"}
    )

    assert response.status_code == 400


async def test_github_callback_creates_then_reuses_the_account(
    client: httpx.AsyncClient, async_session: AsyncSession
) -> None:
    for _ in range(2):
        start = await client.get("/auth/github/login", follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        response = await client.get(
            "/auth/github/callback", params={"code": "good-code", "state": state}
        )
        assert response.status_code == 200
        assert set(response.json()) >= {"access_token", "refresh_token"}

    accounts = (await async_session.scalars(select(User).where(User.github_id == 4242))).all()
    assert [account.github_login for account in accounts] == ["octo-learner"]


async def test_github_callback_reports_a_bad_code(client: httpx.AsyncClient) -> None:
    start = await client.get("/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    response = await client.get(
        "/auth/github/callback", params={"code": "stale-code", "state": state}
    )

    assert response.status_code == 502
    assert "incorrect or expired" in response.json()["error"]["message"]


async def test_request_id_is_echoed(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "trace-me-123"})

    assert response.headers["X-Request-ID"] == "trace-me-123"


async def test_cors_allows_only_configured_origins(client: httpx.AsyncClient) -> None:
    preflight = {"Access-Control-Request-Method": "GET"}

    allowed = await client.options(
        "/repos", headers={"Origin": "http://localhost:8000", **preflight}
    )
    blocked = await client.options(
        "/repos", headers={"Origin": "https://evil.example", **preflight}
    )

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:8000"
    assert "access-control-allow-origin" not in blocked.headers
