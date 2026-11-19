"""The daily digest (lesson 5.7): new beginner-friendly issues, and the day's notifications.

    atlas digest                  # today's digest (covering yesterday), for every user
    atlas digest --day 2026-11-19

Run once a day by a scheduler (cron locally, an EventBridge schedule on AWS). Running it
again for the same day does nothing, so a scheduler that fires twice, or a person who
re-runs it after a failure, can't send anyone two digests.
"""

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from atlas.models import Digest, Issue, Label, Notification, User

# The labels projects use to invite newcomers; Atlas's issue commands default to them too.
BEGINNER_LABELS = ("good first issue", "help wanted")


def build_digests(session: Session, day: date) -> int:
    """Create `day`'s digest for every user who has none yet; return how many were created.

    The digest of a day covers the 24 hours before it began (UTC): built in the morning,
    it describes yesterday, which is complete.
    """
    covered_until = datetime.combine(day, time(0), tzinfo=UTC)
    covered_from = covered_until - timedelta(days=1)

    new_issues = session.scalars(
        select(Issue)
        .join(Issue.labels)
        .where(
            Label.name.in_(BEGINNER_LABELS),
            Issue.created_at >= covered_from,
            Issue.created_at < covered_until,
        )
        .options(selectinload(Issue.labels), selectinload(Issue.repo))
        # One row per issue, even when it carries both beginner labels.
        .distinct()
        .order_by(Issue.created_at, Issue.id)
    ).all()
    items = [
        {
            "repo": issue.repo.full_name,
            "number": issue.number,
            "title": issue.title,
            "url": issue.url,
            "labels": sorted(label.name for label in issue.labels),
        }
        for issue in new_issues
    ]

    notification_counts = dict(
        session.execute(
            select(Notification.user_id, func.count())
            .where(
                Notification.created_at >= covered_from,
                Notification.created_at < covered_until,
            )
            .group_by(Notification.user_id)
        ).all()
    )

    user_ids = session.scalars(select(User.id)).all()
    if not user_ids:
        return 0
    rows = [
        {
            "user_id": user_id,
            "day": day,
            "issues": items,
            "notification_count": notification_counts.get(user_id, 0),
        }
        for user_id in user_ids
    ]
    statement = (
        insert(Digest)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["user_id", "day"])
        .returning(Digest.id)
    )
    return len(session.scalars(statement).all())


def today() -> date:
    return datetime.now(UTC).date()
