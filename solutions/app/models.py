from datetime import datetime

from sqlalchemy import ForeignKey, String, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    # We only ever store the hash. The plain password never touches the database.
    password_hash: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    posts: Mapped[list["Post"]] = relationship(back_populates="owner")


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    content: Mapped[str]
    # true() and func.now() are portable: Postgres gets TRUE / now(),
    # SQLite (used in tests) gets 1 / CURRENT_TIMESTAMP.
    published: Mapped[bool] = mapped_column(server_default=true())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # ondelete=CASCADE: deleting a user removes their posts at the database level.
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    owner: Mapped[User] = relationship(back_populates="posts")


class Vote(Base):
    """Join table between users and posts. The composite primary key
    makes "one vote per user per post" a rule the database enforces."""

    __tablename__ = "votes"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    post_id: Mapped[int] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True
    )
