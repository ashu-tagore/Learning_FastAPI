"""The job queue on SQS (lesson 7.5), against ElasticMQ, a local SQS from compose.yml.

The same delivery rules as the Redis queue's tests. SQS keeps its own time for visibility
timeouts and delays, so these tests wait real seconds (kept to one or two).
"""

import os
import time
import uuid
from collections.abc import Iterator

import boto3
import pytest
import redis

from atlas.config import Settings
from atlas.queue import RedisQueue, SqsQueue, make_queue
from tests.conftest import FakeClock

VISIBILITY_TIMEOUT_SECONDS = 1


@pytest.fixture
def sqs_queue(clock: FakeClock) -> Iterator[SqsQueue]:
    """A real SQS API with a queue of its own, deleted after."""
    url = os.environ.get("TEST_SQS_URL")
    if url is None:
        pytest.skip("set TEST_SQS_URL to run SQS tests (see lesson 7.5)")
    # ElasticMQ accepts any credentials; boto3 refuses to sign a request without some.
    client = boto3.client(
        "sqs",
        endpoint_url=url,
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )
    created = client.create_queue(
        QueueName=f"test-{uuid.uuid4().hex}",
        Attributes={"VisibilityTimeout": str(VISIBILITY_TIMEOUT_SECONDS)},
    )
    queue_url = created["QueueUrl"]
    yield SqsQueue(client, queue_url, clock=clock)
    client.delete_queue(QueueUrl=queue_url)


def test_a_message_is_received_once_and_then_acknowledged(sqs_queue: SqsQueue) -> None:
    sqs_queue.send(7)

    received = sqs_queue.receive(wait_seconds=1)
    sqs_queue.ack(7)
    time.sleep(VISIBILITY_TIMEOUT_SECONDS + 0.5)
    after_timeout = sqs_queue.receive(wait_seconds=0)

    assert received == 7
    assert after_timeout is None


def test_an_unacknowledged_message_comes_back_after_its_visibility_timeout(
    sqs_queue: SqsQueue,
) -> None:
    sqs_queue.send(7)
    sqs_queue.receive(wait_seconds=1)  # a worker takes it, then crashes before ack

    before_timeout = sqs_queue.receive(wait_seconds=0)
    time.sleep(VISIBILITY_TIMEOUT_SECONDS + 0.5)
    after_timeout = sqs_queue.receive(wait_seconds=1)

    assert before_timeout is None
    assert after_timeout == 7


def test_a_delayed_message_waits_until_it_is_due(sqs_queue: SqsQueue, clock: FakeClock) -> None:
    sqs_queue.send(7, delay_seconds=1)

    too_early = sqs_queue.receive(wait_seconds=0)
    clock.advance(1)
    time.sleep(1.5)
    on_time = sqs_queue.receive(wait_seconds=1)

    assert too_early is None
    assert on_time == 7


def test_a_message_that_arrives_before_it_is_due_is_sent_again(
    sqs_queue: SqsQueue, clock: FakeClock
) -> None:
    # SQS delivers this after one real second, but Atlas's clock hasn't moved, so it isn't
    # due yet: what happens to an hour-long retry after SQS's 15-minute maximum delay.
    sqs_queue.send(7, delay_seconds=1)
    time.sleep(1.5)

    early = sqs_queue.receive(wait_seconds=1)
    clock.advance(1)
    time.sleep(1.5)
    on_time = sqs_queue.receive(wait_seconds=1)

    assert early is None
    assert on_time == 7


def test_acknowledging_a_job_it_never_handed_out_does_nothing(sqs_queue: SqsQueue) -> None:
    sqs_queue.ack(99)

    assert sqs_queue.receive(wait_seconds=0) is None


def test_atlas_uses_sqs_only_when_a_queue_url_is_configured() -> None:
    redis_client = redis.Redis.from_url("redis://127.0.0.1:1/0")  # never contacted
    on_aws = Settings(sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/atlas-jobs")

    assert isinstance(make_queue(on_aws, redis_client), SqsQueue)
    assert isinstance(make_queue(Settings(sqs_queue_url=None), redis_client), RedisQueue)
