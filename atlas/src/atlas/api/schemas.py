"""The shapes of data crossing the HTTP boundary: what clients send and what they get back.

Models (models.py) describe tables; schemas describe requests and responses. Keeping them
separate is what keeps fields like password_hash out of every response.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

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

# ---------- Users and authentication ----------


class UserCreate(BaseModel):
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

    skills_profile: str = Field(default="", max_length=2000)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds until the access token expires


class RefreshRequest(BaseModel):
    refresh_token: str


# ---------- Repositories ----------


class RepoCreate(BaseModel):
    full_name: RepoFullName


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


# ---------- Notes (review lesson 4.20) ----------


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
