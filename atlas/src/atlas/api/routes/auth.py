"""Registering, signing in (password or GitHub), refreshing and signing out."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from atlas import github_oauth
from atlas.api.deps import SessionDep, SettingsDep
from atlas.api.schemas import RefreshRequest, TokenPair, UserCreate, UserOut, normalise_email
from atlas.config import ApiSettings
from atlas.github import GitHubError
from atlas.models import RefreshToken, User
from atlas.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

OAUTH_COOKIE = "atlas_oauth"
OAUTH_COOKIE_SECONDS = 600  # GitHub's codes expire after 10 minutes anyway
TIMING_DUMMY_HASH = hash_password("not-a-real-password")


async def issue_tokens(session: AsyncSession, user: User, settings: ApiSettings) -> TokenPair:
    """A short-lived access token, and a long-lived refresh token stored as a hash."""
    lifetime = timedelta(minutes=settings.access_token_minutes)
    access_token = create_access_token(user.id, settings.secret_key.get_secret_value(), lifetime)

    refresh_token = new_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
        )
    )
    await session.commit()
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=int(lifetime.total_seconds()),
    )


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def register(body: UserCreate, session: SessionDep) -> User:
    # Argon2 is deliberately slow CPU work. Run it in a worker thread so the event loop
    # keeps serving other requests meanwhile (lesson 4.17).
    password_hash = await run_in_threadpool(hash_password, body.password)
    user = User(email=body.email, password_hash=password_hash)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        # The unique constraint on email decides, so two simultaneous sign-ups can't both win.
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email exists") from None
    await session.refresh(user)
    return user


@router.post("/login", response_model=TokenPair)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
    settings: SettingsDep,
) -> TokenPair:
    # OAuth2's password flow calls the field "username"; Atlas uses the email address.
    user = await session.scalar(select(User).where(User.email == normalise_email(form.username)))

    # Same message whether the email is unknown or the password is wrong,
    # so the endpoint can't be used to discover who has an account.
    wrong_credentials = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Incorrect email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if user is None or user.password_hash is None:
        # Hash anyway, so an unknown email takes as long as a wrong password:
        # otherwise response times would reveal which emails have accounts.
        await run_in_threadpool(verify_password, form.password, TIMING_DUMMY_HASH)
        raise wrong_credentials
    password_matches = await run_in_threadpool(verify_password, form.password, user.password_hash)
    if not password_matches:
        raise wrong_credentials

    return await issue_tokens(session, user, settings)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, session: SessionDep, settings: SettingsDep) -> TokenPair:
    """Trade a refresh token for a new pair. Each refresh token works exactly once."""
    stored = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(body.refresh_token)
        )
    )
    rejected = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    if stored is None:
        raise rejected

    now = datetime.now(UTC)
    was_already_used = stored.revoked_at is not None
    if was_already_used:
        # A used token coming back means it was probably stolen. Sign the user out everywhere.
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == stored.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await session.commit()
        raise rejected

    has_expired = stored.expires_at <= now
    if has_expired:
        raise rejected

    stored.revoked_at = now
    user = await session.get(User, stored.user_id)
    if user is None:
        raise rejected
    return await issue_tokens(session, user, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, session: SessionDep) -> Response:
    """Revoke a refresh token. The current access token stays valid until it expires."""
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == hash_refresh_token(body.refresh_token),
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def github_client_id(settings: ApiSettings) -> str:
    if settings.github_client_id is None or settings.github_client_secret is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "GitHub sign-in is not configured")
    return settings.github_client_id


@router.get("/github/login", include_in_schema=False)
async def github_login(request: Request, settings: SettingsDep) -> RedirectResponse:
    client_id = github_client_id(settings)
    state = secrets.token_urlsafe(24)
    verifier, challenge = github_oauth.new_pkce_pair()
    redirect_uri = str(request.url_for("github_callback"))

    response = RedirectResponse(
        github_oauth.authorize_url(client_id, redirect_uri, state, challenge),
        status_code=status.HTTP_302_FOUND,
    )
    # The browser keeps state and verifier for the callback. HttpOnly: page scripts can't read it.
    response.set_cookie(
        OAUTH_COOKIE,
        f"{state}.{verifier}",
        max_age=OAUTH_COOKIE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/github/callback", name="github_callback", include_in_schema=False)
async def github_callback(
    request: Request, code: str, state: str, session: SessionDep, settings: SettingsDep
) -> Response:
    client_id = github_client_id(settings)
    cookie = request.cookies.get(OAUTH_COOKIE, "")
    expected_state, _, verifier = cookie.partition(".")

    # secrets.compare_digest takes the same time whatever the input, leaking nothing via timing.
    state_matches = bool(expected_state) and secrets.compare_digest(state, expected_state)
    if not state_matches:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Sign-in expired or was not started here")

    assert settings.github_client_secret is not None  # checked by github_client_id
    try:
        identity = await github_oauth.exchange_code(
            code,
            client_id,
            settings.github_client_secret.get_secret_value(),
            str(request.url_for("github_callback")),
            verifier,
            transport=request.app.state.github_transport,
        )
    except GitHubError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from None

    user = await session.scalar(select(User).where(User.github_id == identity.github_id))
    if user is None:
        user = User(github_id=identity.github_id, github_login=identity.login)
        session.add(user)
    else:
        # Logins can be renamed on GitHub; the numeric id never changes.
        user.github_login = identity.login
    await session.commit()
    await session.refresh(user)

    tokens = await issue_tokens(session, user, settings)
    serves_web_interface = settings.web_dir is not None
    response: Response
    if serves_web_interface:
        # The fragment (after #) never reaches any server or log; the page reads it and clears it.
        fragment = f"access_token={tokens.access_token}&refresh_token={tokens.refresh_token}"
        response = RedirectResponse(f"/app/#{fragment}", status_code=status.HTTP_302_FOUND)
    else:
        response = Response(tokens.model_dump_json(), media_type="application/json")
    response.delete_cookie(OAUTH_COOKIE)
    return response
