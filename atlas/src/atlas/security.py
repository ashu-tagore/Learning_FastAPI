"""Proving who someone is: password hashes, signed access tokens, and refresh tokens.

Kept free of FastAPI so the same functions work in the API, the CLI and tests.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash

# Argon2 is deliberately slow and memory-hungry, so guessing passwords from a
# leaked hash is expensive. Never use a fast hash like SHA-256 for passwords.
_password_hasher = PasswordHash.recommended()

JWT_ALGORITHM = "HS256"


class InvalidToken(Exception):
    """The token is malformed, has a bad signature, has expired, or is the wrong kind."""


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hasher.verify(password, password_hash)


def create_access_token(user_id: int, secret_key: str, lifetime: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        # "sub" (subject) is the standard claim for who the token is about; it must be a string.
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(payload, secret_key, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, secret_key: str) -> int:
    """Return the user id inside a valid access token, or raise InvalidToken."""
    try:
        # Pinning the algorithm stops a forged header from choosing a weaker one.
        payload: dict[str, Any] = jwt.decode(token, secret_key, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError as error:
        raise InvalidToken(str(error)) from error

    is_access_token = payload.get("type") == "access"
    if not is_access_token:
        raise InvalidToken("not an access token")
    return int(payload["sub"])


def new_refresh_token() -> str:
    """A random, unguessable token. Unlike a JWT it means nothing until looked up in a table."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    # Refresh tokens are long and random, so a fast hash is enough here (unlike passwords).
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
