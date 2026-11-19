"""Structured logs (lesson 4.15): one JSON line per request, every line carrying its request id."""

import json
import logging
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.api.deps import get_session
from atlas.logs import JsonFormatter

pytestmark = pytest.mark.anyio


def finished(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == "request finished"]


async def test_every_request_is_logged_once_with_its_id_and_timing(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="atlas")

    await client.get("/health", headers={"X-Request-ID": "trace-me-123"})

    [record] = finished(caplog)
    line = json.loads(JsonFormatter().format(record))
    assert line["request_id"] == "trace-me-123"
    assert (line["method"], line["path"], line["status"]) == ("GET", "/health", 200)
    assert line["duration_ms"] >= 0


async def test_passwords_never_reach_the_logs(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    secret = "correct-horse-battery"

    await client.post("/auth/register", json={"email": "ada@example.com", "password": secret})
    await client.post("/auth/login", data={"username": "ada@example.com", "password": secret})

    formatted = [JsonFormatter().format(record) for record in caplog.records]
    assert formatted
    assert all(secret not in line for line in formatted)


async def test_a_crash_is_logged_with_the_id_the_client_was_given(
    api_app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="atlas")

    async def broken_session() -> AsyncIterator[AsyncSession]:
        raise RuntimeError("the database driver fell over")
        yield  # never reached; makes this a generator, like the real dependency

    api_app.dependency_overrides[get_session] = broken_session
    transport = httpx.ASGITransport(app=api_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    request_id = response.headers["X-Request-ID"]
    lines = [json.loads(JsonFormatter().format(record)) for record in caplog.records]
    crash = next(line for line in lines if line["level"] == "ERROR")
    assert crash["request_id"] == request_id
    assert "the database driver fell over" in crash["exception"]
    [request_line] = [line for line in lines if line["message"] == "request finished"]
    assert (request_line["status"], request_line["request_id"]) == (500, request_id)
