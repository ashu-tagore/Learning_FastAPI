"""The notifier: turns issue events into notifications for users who saved the issue (5.6).

    atlas notifier

One consumer group, "notifications". Run several notifier processes and the group shares
the events among them; each event goes to one of them. Another feature can read the same
stream with a group of its own, at its own pace, without affecting this one.
"""

import logging
import socket
import threading

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from atlas.events import EventStream, IssueEvent
from atlas.models import Notification

logger = logging.getLogger("atlas.notifier")

GROUP = "notifications"
# Changes worth telling someone who saved the issue. "opened" can't concern anyone yet.
WORTH_NOTIFYING = ("relabelled", "closed")


def notify(session: Session, event: IssueEvent) -> int:
    """Create the event's notifications; return how many are new (0 for a duplicate)."""
    if event.change not in WORTH_NOTIFYING or not event.saved_by:
        return 0
    rows = [
        {
            "user_id": user_id,
            "repo_full_name": event.repo,
            "issue_number": event.number,
            "title": event.title,
            "change": event.change,
            "labels": event.labels,
            "event_key": event.key,
        }
        for user_id in event.saved_by
    ]
    # A redelivered event, or a retried sync, inserts nothing: the (user_id, event_key)
    # unique constraint turns duplicates into no-ops.
    statement = (
        insert(Notification)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["user_id", "event_key"])
        .returning(Notification.id)
    )
    return len(session.scalars(statement).all())


def run_notifier(
    session_factory: sessionmaker[Session],
    events: EventStream,
    stop: threading.Event,
    consumer: str | None = None,
    stop_when_idle: bool = False,
) -> int:
    """Handle events until `stop` is set; return how many were handled."""
    # Each process needs its own name within the group; the host name and process id
    # keep it unique and tell an operator which process holds which pending events.
    consumer = consumer or f"{socket.gethostname()}-{threading.get_native_id()}"
    events.ensure_group(GROUP)
    handled = 0
    while not stop.is_set():
        batch = events.read(GROUP, consumer)
        if not batch and stop_when_idle:
            break
        for entry_id, event in batch:
            with session_factory() as session, session.begin():
                created = notify(session, event)
            # Acknowledge after the commit: a crash in between redelivers the event, and
            # the unique constraint makes the second attempt harmless.
            events.ack(GROUP, entry_id)
            handled += 1
            logger.info(
                "event handled",
                extra={"entry_id": entry_id, "key": event.key, "notifications": created},
            )
    return handled
