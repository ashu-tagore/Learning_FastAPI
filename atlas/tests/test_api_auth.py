import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas import security
from atlas.api.routes import auth
from atlas.models import User
from tests.conftest import sign_up

pytestmark = pytest.mark.anyio


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


async def test_unknown_emails_cost_one_hash_like_real_ones(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards against the timing attack in lesson 4.7 without measuring time (which is flaky)."""
    await sign_up(client, "ada@example.com")
    hashed: list[str] = []

    def counting_verify(password: str, password_hash: str) -> bool:
        hashed.append(password_hash)
        return security.verify_password(password, password_hash)

    monkeypatch.setattr(auth, "verify_password", counting_verify)

    for email in ("ada@example.com", "nobody@example.com"):
        await client.post("/auth/login", data={"username": email, "password": "wrong-password"})

    assert len(hashed) == 2


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


async def test_patch_cannot_change_the_role(client: httpx.AsyncClient) -> None:
    """Mass assignment (lesson 4.11): fields the schema doesn't list are refused, not ignored."""
    headers = await sign_up(client, "mallory@example.com")

    response = await client.patch("/users/me", json={"role": "admin"}, headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["location"] == ["body", "role"]
    me = await client.get("/users/me", headers=headers)
    assert me.json()["role"] == "user"


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
