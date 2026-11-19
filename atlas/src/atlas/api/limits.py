"""Rate limits and a response cache, both kept in Redis (lesson 5.5).

Both are shared by every API process, which is why they live in Redis and not in a Python
dict: with four API processes behind a load balancer, a per-process limit would allow four
times the intended rate, and a per-process cache would be cold three times out of four.

Both fail open. If Redis is unreachable, requests are allowed and the cache is skipped:
a broken limiter shouldn't take search down with it. The failure is logged.
"""

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import redis.asyncio as redis
from fastapi import Depends, Request, status

from atlas.api.deps import CurrentUser
from atlas.api.errors import ApiError

logger = logging.getLogger("atlas.api")


class RateLimiter:
    """A sliding window log: each client's recent request times, in a Redis sorted set."""

    def __init__(
        self,
        client: redis.Redis,
        prefix: str = "atlas:ratelimit",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.client = client
        self.prefix = prefix
        self.clock = clock

    async def check(self, key: str, limit: int, window_seconds: float) -> float | None:
        """Count one request; None if it's allowed, else seconds until one would be."""
        now = self.clock()
        redis_key = f"{self.prefix}:{key}"
        # Unique per request: two requests in the same microsecond are still two entries.
        member = f"{now}:{uuid.uuid4().hex}"
        try:
            # transaction=True wraps the commands in MULTI/EXEC: Redis runs them as one
            # step, so no other request's commands can land in between.
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(redis_key, 0, now - window_seconds)
                pipe.zadd(redis_key, {member: now})
                pipe.zcard(redis_key)
                pipe.zrange(redis_key, 0, 0, withscores=True)
                # Idle clients' logs disappear by themselves.
                pipe.expire(redis_key, int(window_seconds) + 1)
                _, _, count, oldest, _ = await pipe.execute()
            if count <= limit:
                return None
            # Rejected requests don't count, or a client retrying too early would never
            # get back under the limit.
            await self.client.zrem(redis_key, member)
        except redis.RedisError:
            logger.warning("rate limiter unavailable; allowing the request", exc_info=True)
            return None

        oldest_time = float(oldest[0][1])
        return oldest_time + window_seconds - now


def get_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.limiter
    return limiter


LimiterDep = Annotated[RateLimiter, Depends(get_limiter)]


def too_many_requests(retry_after: float) -> ApiError:
    # Retry-After is whole seconds; rounding up means a client that obeys it succeeds.
    seconds = max(1, int(retry_after + 0.999))
    return ApiError(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "rate_limited",
        f"Too many requests; try again in {seconds} seconds",
        headers={"Retry-After": str(seconds)},
    )


def limit_per_client(
    name: str, limit: int, window_seconds: float = 60
) -> Callable[..., Awaitable[None]]:
    """A dependency allowing `limit` requests per window from each client address."""

    async def dependency(request: Request, limiter: LimiterDep) -> None:
        # The address the connection came from. Behind a load balancer this is the load
        # balancer's own address; Part 7 configures the real client's (X-Forwarded-For).
        client = request.client.host if request.client else "unknown"
        retry_after = await limiter.check(f"{name}:ip:{client}", limit, window_seconds)
        if retry_after is not None:
            raise too_many_requests(retry_after)

    return dependency


def limit_per_user(
    name: str, limit: int, window_seconds: float = 60
) -> Callable[..., Awaitable[None]]:
    """A dependency allowing `limit` requests per window from each signed-in user."""

    async def dependency(user: CurrentUser, limiter: LimiterDep) -> None:
        retry_after = await limiter.check(f"{name}:user:{user.id}", limit, window_seconds)
        if retry_after is not None:
            raise too_many_requests(retry_after)

    return dependency


class ResponseCache:
    """JSON responses kept in Redis for a while, under keys that name the data's version."""

    def __init__(
        self, client: redis.Redis, prefix: str = "atlas:cache", ttl_seconds: int = 600
    ) -> None:
        self.client = client
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds

    def key(self, name: str, version: str, params: dict[str, Any]) -> str:
        """A key for this request against this version of the data.

        When the data changes, its version changes, and so does every key: old entries
        are never read again and expire on their own. Nothing has to be deleted.
        """
        canonical = json.dumps(params, sort_keys=True)
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:32]
        return f"{self.prefix}:{name}:{version}:{digest}"

    async def get(self, key: str) -> Any | None:
        try:
            raw = await self.client.get(key)
        except redis.RedisError:
            logger.warning("cache unavailable; skipping it", exc_info=True)
            return None
        return None if raw is None else json.loads(raw)

    async def set(self, key: str, value: Any) -> None:
        try:
            await self.client.set(key, json.dumps(value), ex=self.ttl_seconds)
        except redis.RedisError:
            logger.warning("cache unavailable; skipping it", exc_info=True)


def get_cache(request: Request) -> ResponseCache:
    cache: ResponseCache = request.app.state.cache
    return cache
