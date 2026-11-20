"""The shapes of data crossing the HTTP boundary: what clients send and what they get back.

Models (models.py) describe tables; schemas describe requests and responses. Keeping them
separate is what keeps fields like password_hash out of every response.
"""

import base64
import binascii
import json
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, EmailStr, Field

# ---------- Domain types (lesson 4.3) ----------
# Each type is parsed once, at the edge. Code that receives an Email or a RepoFullName
# never re-checks it: an invalid value can't get that far.


def normalise_email(address: str) -> str:
    # Atlas treats addresses as case-insensitive and stores one form, so
    # "Ada@Example.com" and "ada@example.com" can't become two accounts.
    return address.lower()


Email = Annotated[EmailStr, AfterValidator(normalise_email)]

# The upper bound matters too: hashing is slow on purpose (lesson 4.7), so a megabyte
# "password" would make each login attempt expensive for the server.
Password = Annotated[str, Field(min_length=10, max_length=128)]

# "owner/name", as GitHub writes it; at most the 200 characters the repos column holds.
RepoFullName = Annotated[
    str, Field(pattern=r"^[\w.-]+/[\w.-]+$", max_length=200, examples=["pytorch/ignite"])
]

# Request bodies refuse fields they don't list. Silently ignoring them would hide client
# mistakes ("skils_profile") and attack attempts ("role": "admin", lesson 4.11) alike.
REQUEST_BODY = ConfigDict(extra="forbid")

# ---------- Users and authentication ----------


class UserCreate(BaseModel):
    model_config = REQUEST_BODY

    email: Email
    password: Password


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
    """PATCH body: a client sends only the fields it wants to change.

    Each field has a default so it may be left out, but its type doesn't allow null:
    "absent" means "leave it alone", while null would mean "store nothing", which the
    column forbids. Routes read model_dump(exclude_unset=True), so defaults never apply.
    """

    model_config = REQUEST_BODY

    skills_profile: str = Field(default="", max_length=2000)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds until the access token expires


class RefreshRequest(BaseModel):
    model_config = REQUEST_BODY

    refresh_token: str


# ---------- Repositories ----------


class RepoCreate(BaseModel):
    model_config = REQUEST_BODY

    full_name: RepoFullName


class RepoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    indexed_at: datetime | None
    indexed_commit: str | None
    issues_synced_until: datetime | None


class SnapshotOut(BaseModel):
    """Where to download the repository's files at the indexed commit (lesson 5.4)."""

    commit: str
    url: str  # works without a token, for anyone who has it, until expires_at
    expires_at: datetime


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repo_full_name: str
    issue_number: int
    title: str
    change: Literal["relabelled", "closed"]
    labels: list[str]
    created_at: datetime


class DigestIssue(BaseModel):
    repo: str
    number: int
    title: str
    url: str
    labels: list[str]


class DigestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    day: date
    issues: list[DigestIssue]
    notification_count: int


# ---------- Background jobs ----------


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: Literal["sync_repo", "sync_issues", "snapshot_repo", "embed_repo"]
    repo_id: int
    status: Literal["queued", "running", "succeeded", "dead"]
    attempts: int
    last_error: str | None
    created_at: datetime
    finished_at: datetime | None


class QueuedJobs(BaseModel):
    """The jobs a request led to; an empty list means there was nothing to do."""

    jobs: list[JobOut]


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


class FitOut(BaseModel):
    """A model's opinion of how well an issue fits me (lesson 6.3): a suggestion, not a fact."""

    issue: IssueOut
    score: int
    reasons: list[str]
    first_step: str
    model: str


def encode_issue_cursor(last_number: int) -> str:
    # Opaque to clients: they pass it back unchanged, so its format can change later.
    raw = json.dumps({"before": last_number}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_issue_cursor(cursor: str) -> int:
    """The issue number a cursor this API issued points below (lesson 4.12)."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        return int(payload["before"])
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeEncodeError):
        raise ValueError("not a cursor this API issued") from None


# Parsed at the edge like the other domain types: a route receives the issue number itself.
IssueCursor = Annotated[int, BeforeValidator(decode_issue_cursor)]


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


# ---------- Notes (review lesson 4.20) ----------


class NoteCreate(BaseModel):
    model_config = REQUEST_BODY

    body: str = Field(min_length=1, max_length=5000)


class NoteUpdate(BaseModel):
    model_config = REQUEST_BODY

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
