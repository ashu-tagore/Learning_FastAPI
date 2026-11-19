"""The job queue (lesson 5.1): at-least-once delivery, in Redis."""

from atlas.queue import RedisQueue
from tests.conftest import FakeClock


def test_a_message_is_received_once_and_then_acknowledged(queue: RedisQueue) -> None:
    queue.send(7)

    received = queue.receive(wait_seconds=0)
    again = queue.receive(wait_seconds=0)
    queue.ack(7)

    assert received == 7
    assert again is None


def test_an_unacknowledged_message_comes_back_after_its_visibility_timeout(
    queue: RedisQueue, clock: FakeClock
) -> None:
    queue.send(7)
    queue.receive(wait_seconds=0)  # a worker takes it, then crashes before ack

    before_timeout = queue.receive(wait_seconds=0)
    clock.advance(61)
    after_timeout = queue.receive(wait_seconds=0)

    assert before_timeout is None
    assert after_timeout == 7


def test_a_delayed_message_waits_until_it_is_due(queue: RedisQueue, clock: FakeClock) -> None:
    queue.send(7, delay_seconds=30)

    too_early = queue.receive(wait_seconds=0)
    clock.advance(31)
    on_time = queue.receive(wait_seconds=0)

    assert too_early is None
    assert on_time == 7


def test_messages_come_out_in_the_order_they_went_in(queue: RedisQueue) -> None:
    for job_id in (1, 2, 3):
        queue.send(job_id)

    received = [queue.receive(wait_seconds=0) for _ in range(3)]

    assert received == [1, 2, 3]


def test_waiting_on_an_empty_queue_returns_none_instead_of_timing_out(
    queue: RedisQueue,
) -> None:
    # Regression (lesson 5.1): a 5-second wait on redis-py 8's default 5-second socket
    # timeout raised TimeoutError after a minute of retries, and the worker crashed.
    # This test takes WAIT_SECONDS of real time, because the bug only shows in real time.
    assert queue.receive() is None
