"""A job queue in Redis with the delivery rules of a cloud queue such as SQS (lesson 5.1).

Messages are job ids; everything about a job lives in Postgres (the jobs table). A received
message is hidden from other workers until it is acknowledged or its visibility timeout
passes, and then it is delivered again. So every message is delivered at least once, and
sometimes more than once: whoever processes a job must cope with seeing it twice.

Four Redis keys per queue:
    <name>:ready      list of ids waiting to be received (oldest at the right)
    <name>:in-flight  list of ids received but not yet acknowledged
    <name>:deadlines  sorted set: in-flight id -> time its visibility timeout ends
    <name>:delayed    sorted set: id -> time it becomes ready (retries with backoff)
"""

import time
from collections.abc import Callable
from typing import Protocol

import redis


class JobQueue(Protocol):
    """What Atlas needs from a queue. RedisQueue provides it locally; Part 7 adds SQS."""

    def send(self, job_id: int, delay_seconds: float = 0) -> None: ...
    def receive(self, wait_seconds: int = 5) -> int | None: ...
    def ack(self, job_id: int) -> None: ...


class RedisQueue:
    def __init__(
        self,
        client: redis.Redis,
        name: str = "atlas:jobs",
        visibility_timeout: float = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.client = client
        self.visibility_timeout = visibility_timeout
        self.clock = clock
        self.ready = f"{name}:ready"
        self.in_flight = f"{name}:in-flight"
        self.deadlines = f"{name}:deadlines"
        self.delayed = f"{name}:delayed"

    def send(self, job_id: int, delay_seconds: float = 0) -> None:
        if delay_seconds > 0:
            self.client.zadd(self.delayed, {str(job_id): self.clock() + delay_seconds})
            return
        self.client.lpush(self.ready, str(job_id))

    def receive(self, wait_seconds: int = 5) -> int | None:
        """The next job id, hidden from other workers until ack() or its timeout."""
        self._release_due_messages()
        # LMOVE is atomic: the id is never in neither list, so a crash here can't lose it.
        if wait_seconds > 0:
            raw = self.client.blmove(self.ready, self.in_flight, wait_seconds, "RIGHT", "LEFT")
        else:
            raw = self.client.lmove(self.ready, self.in_flight, "RIGHT", "LEFT")
        if raw is None:
            return None
        job_id = as_text(raw)
        self.client.zadd(self.deadlines, {job_id: self.clock() + self.visibility_timeout})
        return int(job_id)

    def ack(self, job_id: int) -> None:
        """The job is finished (or handed back to the queue as a new message): forget it."""
        self.client.lrem(self.in_flight, 1, str(job_id))
        self.client.zrem(self.deadlines, str(job_id))

    def _release_due_messages(self) -> None:
        """Make delayed messages that are due, and timed-out in-flight ones, ready again."""
        now = self.clock()
        for job_id in self._due(self.delayed, now):
            # Only the worker whose ZREM succeeds moves it, so it isn't released twice.
            if self.client.zrem(self.delayed, job_id):
                self.client.lpush(self.ready, job_id)
        for job_id in self._due(self.deadlines, now):
            if self.client.zrem(self.deadlines, job_id):
                self.client.lrem(self.in_flight, 1, job_id)
                self.client.lpush(self.ready, job_id)

    def _due(self, key: str, now: float) -> list[str]:
        return [as_text(raw) for raw in self.client.zrangebyscore(key, "-inf", now)]


def as_text(raw: object) -> str:
    """Redis returns bytes unless the client decodes them; job ids are handled as text."""
    if isinstance(raw, bytes):
        return raw.decode("ascii")
    return str(raw)
