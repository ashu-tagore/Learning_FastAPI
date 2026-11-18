"""Idempotency keys: a client may safely retry a POST without creating something twice.

The client sends a unique `Idempotency-Key` header with a POST. The first time, Atlas
handles it and stores the response under that key. If the same key arrives again (say
the client's connection dropped before it saw the answer), Atlas replays the stored
response instead of acting again.
"""

from typing import Any

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.models import IdempotencyKey, User


async def replay(
    session: AsyncSession, user: User, key: str | None, path: str
) -> JSONResponse | None:
    """The stored response for this key, or None if the request is new."""
    if key is None:
        return None
    stored = await session.scalar(
        select(IdempotencyKey).where(IdempotencyKey.user_id == user.id, IdempotencyKey.key == key)
    )
    if stored is None:
        return None

    used_for_another_request = stored.request_path != path
    if used_for_another_request:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This Idempotency-Key was already used for a different request",
        )
    return JSONResponse(stored.response_body, status_code=stored.response_status)


def remember(
    session: AsyncSession, user: User, key: str | None, path: str, status_code: int, body: Any
) -> None:
    """Store the response in the same transaction as the change it describes."""
    if key is None:
        return
    session.add(
        IdempotencyKey(
            user_id=user.id,
            key=key,
            request_path=path,
            response_status=status_code,
            response_body=body,
        )
    )
