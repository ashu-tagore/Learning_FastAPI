"""Everything about proving who a user is: password hashing, JWTs,
and the get_current_user dependency that protected routes use.

Kept in one module on purpose: these pieces only make sense together.
"""

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .database import get_db

# Argon2 is deliberately slow and memory-hungry, which makes brute-forcing
# a leaked hash expensive. Never use a fast hash like SHA-256 for passwords.
password_hasher = PasswordHash.recommended()

# tokenUrl only tells the /docs page where the login form should post to.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


def hash_password(plain: str) -> str:
    return password_hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return password_hasher.verify(plain, hashed)


def create_access_token(user_id: int) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    # "sub" (subject) is the standard JWT claim for "who this token is about".
    # The JWT spec requires it to be a string.
    payload = {"sub": str(user_id), "exp": expires_at}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    # Same error for every failure, so an attacker can't tell
    # "bad signature" from "expired" from "user deleted".
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # decode() checks the signature AND the exp claim in one call.
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
    except jwt.InvalidTokenError:
        raise unauthorized

    user_id = payload.get("sub")
    if user_id is None:
        raise unauthorized

    user = db.get(models.User, int(user_id))
    if user is None:
        raise unauthorized

    return user
