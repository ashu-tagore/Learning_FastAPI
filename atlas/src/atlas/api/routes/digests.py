"""A user's daily digest (lesson 5.7)."""

from fastapi import APIRouter, status
from sqlalchemy import select

from atlas.api.deps import CurrentUser, SessionDep
from atlas.api.errors import ApiError
from atlas.api.schemas import DigestOut
from atlas.models import Digest

router = APIRouter(prefix="/digests", tags=["digests"])


@router.get("/latest", response_model=DigestOut)
async def latest_digest(user: CurrentUser, session: SessionDep) -> Digest:
    digest = await session.scalar(
        select(Digest).where(Digest.user_id == user.id).order_by(Digest.day.desc()).limit(1)
    )
    if digest is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "digest_not_found", "No digest yet")
    return digest
