"""The shapes of data crossing the HTTP boundary: what clients send and what they get back.

Models (models.py) describe tables; schemas describe requests and responses. Keeping them
separate is what keeps fields like password_hash out of every response.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ---------- Users and authentication ----------


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)


class UserOut(BaseModel):
    # from_attributes lets Pydantic read user.email from an ORM object, not only dict keys.
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr | None
    github_login: str | None
    role: Literal["user", "admin"]
    skills_profile: str
    created_at: datetime


class UserUpdate(BaseModel):
    """PATCH body: every field optional, so a client can change just one."""

    skills_profile: str | None = Field(default=None, max_length=2000)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds until the access token expires


class RefreshRequest(BaseModel):
    refresh_token: str


# ---------- Repositories ----------


class RepoCreate(BaseModel):
    full_name: str = Field(pattern=r"^[\w.-]+/[\w.-]+$", examples=["pytorch/ignite"])


class RepoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    indexed_at: datetime | None


# ---------- Issues ----------


class IssueOut(BaseModel):
    id: int
    repo: str
    number: int
    title: str
    url: str
    author: str
    labels: list[str]
    comments: int
    created_at: datetime


class IssuePage(BaseModel):
    items: list[IssueOut]
    # Pass this back as ?cursor= to get the next page; null on the last page.
    next_cursor: str | None


# ---------- Search ----------


class SearchHit(BaseModel):
    path: str
    start_line: int
    end_line: int
    rank: float
    snippet: str


class SearchPage(BaseModel):
    items: list[SearchHit]
    limit: int
    offset: int
    next_offset: int | None


# ---------- Notes (review lesson 4.16) ----------


class NoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class NoteUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class NoteOut(BaseModel):
    id: int
    repo: str
    issue_number: int
    body: str
    created_at: datetime
    updated_at: datetime


# ---------- Errors ----------


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: list[dict[str, object]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
