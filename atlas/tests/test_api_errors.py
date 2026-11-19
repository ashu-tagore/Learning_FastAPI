"""Error handling by design (lesson 4.14): one shape, a stable code per failure, no leaks."""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.api.deps import get_session
from tests.conftest import sign_up

pytestmark = pytest.mark.anyio


def assert_standard_error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["request_id"] == response.headers["X-Request-ID"]


async def test_unknown_routes_answer_in_the_standard_shape(client: httpx.AsyncClient) -> None:
    assert_standard_error(await client.get("/no-such-route"), 404, "not_found")


async def test_wrong_methods_answer_in_the_standard_shape(client: httpx.AsyncClient) -> None:
    assert_standard_error(await client.delete("/health"), 405, "method_not_allowed")


async def test_each_failure_has_its_own_stable_code(client: httpx.AsyncClient) -> None:
    ada = await sign_up(client, "ada@example.com")
    again = {"email": "ada@example.com", "password": "a-long-password"}
    wrong = {"username": "ada@example.com", "password": "not-her-password"}

    assert_standard_error(await client.post("/auth/register", json=again), 409, "email_taken")
    assert_standard_error(await client.post("/auth/login", data=wrong), 401, "wrong_credentials")
    assert_standard_error(await client.get("/users/me"), 401, "unauthorized")
    assert_standard_error(await client.get("/issues/999999"), 404, "issue_not_found")
    stolen = {"refresh_token": "made-up"}
    assert_standard_error(
        await client.post("/auth/refresh", json=stolen), 401, "invalid_refresh_token"
    )
    repo = {"full_name": "pytorch/ignite"}
    assert_standard_error(await client.post("/repos", json=repo, headers=ada), 403, "admins_only")


async def test_unexpected_errors_hide_their_details(api_app: FastAPI) -> None:
    async def broken_session() -> AsyncIterator[AsyncSession]:
        raise RuntimeError("connection string postgresql://atlas:s3cret@db/atlas is wrong")
        yield  # never reached; makes this a generator, like the real dependency

    api_app.dependency_overrides[get_session] = broken_session
    # raise_app_exceptions=False: receive the 500 response, as a real client would.
    transport = httpx.ASGITransport(app=api_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    assert_standard_error(response, 500, "internal_server_error")
    assert "s3cret" not in response.text
