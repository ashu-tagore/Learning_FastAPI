"""The job queue: in Redis locally (lesson 5.1), in Amazon SQS on AWS (lesson 7.5).

Messages are job ids; everything about a job lives in Postgres (the jobs table). A received
message is hidden from other workers until it is acknowledged or its visibility timeout
passes, and then it is delivered again. So every message is delivered at least once, and
sometimes more than once: whoever processes a job must cope with seeing it twice.

Four Redis keys per queue:
    <name>:ready      list of ids waiting to be received (oldest at the right)
    <name>:in-flight  list of ids received but not yet acknowledged
    <name>:deadlines  sorted set: in-flight id -> time its visibility timeout ends
    <name>:delayed    sorted set: id -> time it becomes ready (retries with backoff)

Both queues follow the same delivery rules, so the worker can't tell them apart.
"""

import json
import math
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

import boto3
import redis

from atlas.config import Settings

if TYPE_CHECKING:
    # Type stubs are a development dependency, so they're imported for the type checker only
    # (the reason is in snapshots.py).
    from mypy_boto3_sqs import SQSClient

# How long receive() waits for a message before returning None. It must stay below the
# Redis client's socket timeout (5 seconds by default in redis-py 8): a blocking command
# that outlasts it fails with TimeoutError instead of returning empty (lesson 5.1).
WAIT_SECONDS = 2


class JobQueue(Protocol):
    """What Atlas needs from a queue. RedisQueue provides it locally, SqsQueue on AWS."""

    def send(self, job_id: int, delay_seconds: float = 0) -> None: ...
    def receive(self, wait_seconds: int = WAIT_SECONDS) -> int | None: ...
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

    def receive(self, wait_seconds: int = WAIT_SECONDS) -> int | None:
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


# The longest delay SQS accepts on a message: 15 minutes. Atlas's retries can wait an hour.
SQS_MAX_DELAY_SECONDS = 900


class SqsQueue:
    """The same queue on Amazon SQS, which has its own visibility timeout and delays.

    Each message carries the job id and when it's due. SQS can delay a message by at most
    15 minutes, so a longer delay takes several hops: a message that arrives before it's due
    is sent again for the time left, then deleted.

    SQS deletes a message by the receipt handle of one particular delivery, not by its body,
    so the queue remembers the handle of each job it has handed out until ack().
    """

    def __init__(
        self, client: "SQSClient", queue_url: str, clock: Callable[[], float] = time.time
    ) -> None:
        self.client = client
        self.queue_url = queue_url
        self.clock = clock
        self.receipts: dict[int, str] = {}

    def send(self, job_id: int, delay_seconds: float = 0) -> None:
        due_at = self.clock() + delay_seconds
        # Rounded up: a delay rounded down would deliver early and cost an extra hop.
        delay = min(math.ceil(delay_seconds), SQS_MAX_DELAY_SECONDS)
        body = json.dumps({"job_id": job_id, "due_at": due_at})
        self.client.send_message(QueueUrl=self.queue_url, MessageBody=body, DelaySeconds=delay)

    def receive(self, wait_seconds: int = WAIT_SECONDS) -> int | None:
        """The next job id, hidden from other workers until ack() or its timeout."""
        response = self.client.receive_message(
            QueueUrl=self.queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=wait_seconds
        )
        messages = response.get("Messages", [])
        if not messages:
            return None
        message = messages[0]
        body = json.loads(message["Body"])
        job_id = int(body["job_id"])
        seconds_left = body["due_at"] - self.clock()
        if seconds_left > 0:
            # Sent again before deleting: a crash between the two leaves a duplicate, which
            # jobs already cope with, instead of a lost job.
            self.client.send_message(
                QueueUrl=self.queue_url,
                MessageBody=message["Body"],
                DelaySeconds=min(math.ceil(seconds_left), SQS_MAX_DELAY_SECONDS),
            )
            self._delete(message["ReceiptHandle"])
            return None
        self.receipts[job_id] = message["ReceiptHandle"]
        return job_id

    def ack(self, job_id: int) -> None:
        """The job is finished (or handed back to the queue as a new message): forget it."""
        receipt = self.receipts.pop(job_id, None)
        if receipt is None:
            return
        self._delete(receipt)

    def _delete(self, receipt: str) -> None:
        self.client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt)


def make_queue(settings: Settings, redis_client: redis.Redis) -> JobQueue:
    """SQS when a queue URL is configured (on AWS), otherwise Redis."""
    if settings.sqs_queue_url is None:
        return RedisQueue(redis_client)
    # Credentials are not passed, as for S3: boto3 finds them, on AWS in the task's role.
    client = boto3.client(
        "sqs", endpoint_url=settings.sqs_endpoint_url, region_name=settings.sqs_region
    )
    return SqsQueue(client, settings.sqs_queue_url)


def as_text(raw: object) -> str:
    """Redis returns bytes unless the client decodes them; ids and events are handled as text."""
    if isinstance(raw, bytes):
        # UTF-8, not ASCII: events carry issue titles, which can be in any language.
        return raw.decode("utf-8")
    return str(raw)
