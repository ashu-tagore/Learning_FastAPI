"""A user's notifications about issues they saved (lesson 5.6)."""

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from atlas.api.deps import CurrentUser, SessionDep
from atlas.api.schemas import NotificationOut
from atlas.models import Notification

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[Notification]:
    """Newest first. Written by the notifier, so they appear seconds after a sync, not at once."""
    statement = (
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    )
    return list(await session.scalars(statement))
