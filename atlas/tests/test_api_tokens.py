"""Forged access tokens (lesson 4.8): each attack an attacker could try must answer 401."""

import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest

from atlas.config import ApiSettings
from tests.conftest import sign_up

pytestmark = pytest.mark.anyio


def encode_part(data: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def forge(kind: str, real_token: str, secret_key: str) -> str:
    """A token an attacker could build, starting from their own valid one."""
    header, payload, signature = real_token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    someone_else = {**claims, "sub": str(int(claims["sub"]) + 1)}
    if kind == "tampered payload":
        return ".".join([header, encode_part(someone_else), signature])
    if kind == "alg none":
        return ".".join([encode_part({"alg": "none", "typ": "JWT"}), encode_part(someone_else), ""])
    if kind == "wrong key":
        return jwt.encode(someone_else, "a-guessed-key-that-is-long-enough-for-hs256", "HS256")
    if kind == "expired":
        expired = {**claims, "exp": datetime.now(UTC) - timedelta(minutes=1)}
        return jwt.encode(expired, secret_key, "HS256")
    if kind == "not an access token":
        return jwt.encode({**claims, "type": "refresh"}, secret_key, "HS256")
    raise ValueError(kind)


@pytest.mark.parametrize(
    "kind", ["tampered payload", "alg none", "wrong key", "expired", "not an access token"]
)
async def test_forged_tokens_are_rejected(
    client: httpx.AsyncClient, api_settings: ApiSettings, kind: str
) -> None:
    await sign_up(client, "mallory@example.com")
    await sign_up(client, "victim@example.com")
    login = await client.post(
        "/auth/login", data={"username": "mallory@example.com", "password": "a-long-password"}
    )
    secret_key = api_settings.secret_key.get_secret_value()
    token = forge(kind, login.json()["access_token"], secret_key)

    response = await client.get("/users/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
