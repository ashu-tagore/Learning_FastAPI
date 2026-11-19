"""Rate limits and the response cache (lesson 5.5)."""

from pathlib import Path

import httpx
import pytest
import redis.asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.api.limits import RateLimiter, ResponseCache
from atlas.api.routes.search import SEARCH_REQUESTS_PER_MINUTE
from tests.conftest import FakeClock
from tests.test_api_jobs import add_ignite
from tests.test_api_resources import admin_headers, index_sample

pytestmark = pytest.mark.anyio


@pytest.fixture
def limiter(api_redis: tuple[RateLimiter, ResponseCache], redis_client: object) -> RateLimiter:
    # redis_client: skip these tests unless TEST_REDIS_URL is set.
    return api_redis[0]


async def test_allows_the_limit_then_says_when_to_retry(
    limiter: RateLimiter, clock: FakeClock
) -> None:
    allowed = [await limiter.check("k", limit=3, window_seconds=60) for _ in range(3)]
    clock.advance(10)

    retry_after = await limiter.check("k", limit=3, window_seconds=60)

    assert allowed == [None, None, None]
    assert retry_after == 50  # the oldest request leaves the window 60 s after it was made


async def test_window_slides_one_request_at_a_time(limiter: RateLimiter, clock: FakeClock) -> None:
    await limiter.check("k", limit=3, window_seconds=60)  # at t=0
    clock.advance(59)
    await limiter.check("k", limit=3, window_seconds=60)  # t=59
    await limiter.check("k", limit=3, window_seconds=60)  # t=59
    assert await limiter.check("k", limit=3, window_seconds=60) is not None

    clock.advance(2)  # t=61: only the request from t=0 has left the window

    assert await limiter.check("k", limit=3, window_seconds=60) is None
    assert await limiter.check("k", limit=3, window_seconds=60) is not None


async def test_rejected_requests_do_not_count(limiter: RateLimiter, clock: FakeClock) -> None:
    await limiter.check("k", limit=1, window_seconds=60)
    for _ in range(10):
        clock.advance(5)
        await limiter.check("k", limit=1, window_seconds=60)  # all rejected

    clock.advance(11)  # t=61: the one accepted request has expired

    assert await limiter.check("k", limit=1, window_seconds=60) is None


async def test_unreachable_redis_allows_requests(clock: FakeClock) -> None:
    client = redis.asyncio.Redis.from_url("redis://127.0.0.1:1/0")
    limiter = RateLimiter(client, clock=clock)

    results = [await limiter.check("k", limit=1, window_seconds=60) for _ in range(3)]

    assert results == [None, None, None]
    await client.aclose()


async def test_search_answers_429_with_retry_after(
    client: httpx.AsyncClient, limiter: RateLimiter
) -> None:
    for _ in range(SEARCH_REQUESTS_PER_MINUTE):
        await client.get("/search", params={"q": "engine"})

    response = await client.get("/search", params={"q": "engine"})

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert response.headers["Retry-After"] == "60"


async def test_sync_is_limited_per_user(
    client: httpx.AsyncClient, async_session: AsyncSession, limiter: RateLimiter
) -> None:
    repo_id = await add_ignite(client, async_session)
    admin = await admin_headers(client, async_session)

    statuses = [
        (await client.post(f"/repos/{repo_id}/sync", headers=admin)).status_code for _ in range(6)
    ]

    assert statuses == [202] * 5 + [429]


async def test_search_is_cached_until_the_repository_is_reindexed(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    sample_repo: Path,
    limiter: RateLimiter,
) -> None:
    await index_sample(async_session, sample_repo)
    first = await client.get("/search", params={"q": "accuracy"})
    second = await client.get("/search", params={"q": "accuracy"})

    (sample_repo / "ignite" / "metrics.py").unlink()
    await index_sample(async_session, sample_repo)
    after_reindex = await client.get("/search", params={"q": "accuracy"})

    assert [first.headers["X-Cache"], second.headers["X-Cache"]] == ["miss", "hit"]
    assert second.json() == first.json()
    assert after_reindex.headers["X-Cache"] == "miss"
    assert after_reindex.json()["items"] == []
