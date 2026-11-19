"""Liveness and readiness (lessons 4.4 and 4.5)."""

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from atlas.api.app import create_app
from atlas.api.deps import get_session
from atlas.config import ApiSettings

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
    assert ready.json()["error"]["code"] == "database_unavailable"
    assert health.status_code == 200
