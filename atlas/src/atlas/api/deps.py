"""Dependencies: things a route asks for by declaring a parameter, and FastAPI provides.

    async def read_me(user: CurrentUser) -> ...

FastAPI sees CurrentUser, runs get_current_user (which itself needs a session and the
settings), and passes the result in. Tests replace any of these with app.dependency_overrides.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.api.errors import ApiError
from atlas.config import ApiSettings
from atlas.models import User
from atlas.queue import JobQueue
from atlas.security import InvalidToken, decode_access_token

# tokenUrl tells the interactive docs where to send the login form.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_settings(request: Request) -> ApiSettings:
    settings: ApiSettings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One database session per request, always closed, even if the route raises."""
    async with request.app.state.session_factory() as session:
        yield session


def get_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.queue
    return queue


SettingsDep = Annotated[ApiSettings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: SessionDep,
    settings: SettingsDep,
) -> User:
    # One answer for every failure: callers learn nothing about why a token was rejected.
    not_authenticated = ApiError(
        status.HTTP_401_UNAUTHORIZED,
        "unauthorized",
        "Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        user_id = decode_access_token(token, settings.secret_key.get_secret_value())
    except InvalidToken:
        raise not_authenticated from None

    user = await session.get(User, user_id)
    if user is None:
        raise not_authenticated
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    is_admin = user.role == "admin"
    if not is_admin:
        raise ApiError(status.HTTP_403_FORBIDDEN, "admins_only", "Administrators only")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
