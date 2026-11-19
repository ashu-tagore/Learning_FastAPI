"""Issue events on a Redis Stream, read by consumer groups (lesson 5.6).

The issues sync publishes one event per meaningful change (opened, relabelled, closed).
Consumers read them in their own time, each group at its own pace, and acknowledge what
they've handled; an event a consumer took but never acknowledged is claimed by another
after a while. Delivery is at least once, so every consumer must be idempotent: each event
carries a `key` that is the same for every copy of the same change.
"""

import json
from dataclasses import asdict, dataclass
from typing import cast

import redis

from atlas.queue import as_text

# One stream entry as redis-py returns it: its id and its fields.
Entry = tuple[object, dict[object, object]]

# Waiting for new events must end before the Redis client's socket timeout (lesson 5.1).
BLOCK_MILLISECONDS = 2000


@dataclass(frozen=True)
class IssueEvent:
    """`issue.updated`: something about an issue changed on GitHub."""

    repo: str
    number: int
    title: str
    change: str  # "opened", "relabelled" or "closed"
    labels: list[str]
    # Who had saved the issue before the change. Captured by the producer, because a
    # closed issue's row, and with it who saved it, is deleted in the same transaction.
    saved_by: list[int]
    # The same for a retried sync or a redelivered event: consumers deduplicate on it.
    key: str


class EventStream:
    def __init__(
        self, client: redis.Redis, name: str = "atlas:events:issues", max_length: int = 10_000
    ) -> None:
        self.client = client
        self.name = name
        self.max_length = max_length

    def publish(self, events: list[IssueEvent]) -> None:
        for event in events:
            # approximate=True trims in whole internal blocks, which is much cheaper; the
            # stream keeps at least max_length events, sometimes a few more.
            self.client.xadd(
                self.name,
                {"type": "issue.updated", "event": json.dumps(asdict(event))},
                maxlen=self.max_length,
                approximate=True,
            )

    def ensure_group(self, group: str) -> None:
        """Create the consumer group if it doesn't exist; a new group starts at the beginning."""
        try:
            self.client.xgroup_create(self.name, group, id="0", mkstream=True)
        except redis.ResponseError as error:
            group_exists = str(error).startswith("BUSYGROUP")
            if not group_exists:
                raise

    def read(
        self,
        group: str,
        consumer: str,
        count: int = 10,
        claim_after_ms: int = 60_000,
        block_ms: int = BLOCK_MILLISECONDS,
    ) -> list[tuple[str, IssueEvent]]:
        """The next events for this consumer: abandoned ones first, then new ones."""
        # Events another consumer of the group took but hasn't acknowledged for a minute:
        # it probably crashed. XAUTOCLAIM hands them to this consumer.
        reply = self.client.xautoclaim(
            self.name, group, consumer, min_idle_time=claim_after_ms, start_id="0-0", count=count
        )
        # XAUTOCLAIM answers [next start id, claimed entries, deleted ids]. redis-py types
        # replies loosely, so the entries' shape is stated here for mypy.
        claimed = cast(list[Entry], reply[1])
        if claimed:
            return [self._parse(entry_id, fields) for entry_id, fields in claimed]

        # ">" means events never delivered to anyone in this group. BLOCK 0 would mean
        # "wait forever" to Redis, so a zero wait is sent as no BLOCK at all (lesson 5.6).
        block = block_ms if block_ms > 0 else None
        response = self.client.xreadgroup(
            group, consumer, {self.name: ">"}, count=count, block=block
        )
        if not response:
            return []
        # XREADGROUP answers [[stream name, entries]], one pair per stream read. (That's
        # the RESP2 protocol, redis-py's default; RESP3 would answer with a dict.)
        _, entries = cast(list[tuple[object, list[Entry]]], response)[0]
        return [self._parse(entry_id, fields) for entry_id, fields in entries]

    def ack(self, group: str, entry_id: str) -> None:
        self.client.xack(self.name, group, entry_id)

    @staticmethod
    def _parse(entry_id: object, fields: dict[object, object]) -> tuple[str, IssueEvent]:
        by_name = {as_text(name): as_text(value) for name, value in fields.items()}
        return as_text(entry_id), IssueEvent(**json.loads(by_name["event"]))
