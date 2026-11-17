"""Pydantic schemas: the shapes of data crossing the HTTP boundary.

Models (models.py) describe tables. Schemas describe requests and responses.
Keeping them separate is what lets us hide password_hash from every response.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---------- Users ----------

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class UserOut(BaseModel):
    # from_attributes lets Pydantic read fields off an ORM object (user.email)
    # instead of requiring a dict (user["email"]).
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    created_at: datetime


# ---------- Posts ----------

class PostBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str
    published: bool = True


class PostCreate(PostBase):
    pass


class PostUpdate(BaseModel):
    """Every field optional, so PATCH can change just one of them."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = None
    published: bool | None = None


class PostOut(PostBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    owner_id: int
    owner: UserOut


class PostWithVotes(BaseModel):
    post: PostOut
    votes: int


# ---------- Auth ----------

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------- Votes ----------

class VoteIn(BaseModel):
    post_id: int
    # Literal reads better than a 0/1 flag: the request says what it does.
    direction: Literal["up", "remove"]
