"""Database tables, described as Python classes (SQLAlchemy ORM).

repos ──< documents ──< chunks
  │
  ├──< issues >──< labels      (issue_labels joins the two)
  └──< labels

users ──< refresh_tokens
  ├──< saved_issues >── issues
  ├──< issue_notes             (point at repo + issue number, so they survive an issue closing)
  └──< idempotency_keys
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), unique=True)  # "pytorch/ignite"
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    documents: Mapped[list["Document"]] = relationship(back_populates="repo")


class Document(Base):
    """One source file at the time it was indexed."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("repo_id", "path"),
        CheckConstraint("kind IN ('code', 'doc')", name="documents_kind_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # ON DELETE CASCADE: deleting a repo deletes its documents in the same statement.
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(10))
    # A fingerprint of the content: unchanged files can be skipped when re-indexing (Part 5).
    content_hash: Mapped[str] = mapped_column(String(64))
    line_count: Mapped[int]

    repo: Mapped[Repo] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document")


class Chunk(Base):
    """A run of consecutive lines from one document: the unit search returns."""

    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("start_line <= end_line", name="chunks_line_range_valid"),
        # A GIN index makes "which chunks contain these words?" fast (lesson 3.6).
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    start_line: Mapped[int]
    end_line: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    # Postgres computes and stores the searchable form whenever text changes.
    # Punctuation becomes spaces first: the English parser would otherwise read
    # code like `self.fire_event` as a web address instead of the words "fire" and "event".
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', regexp_replace(text, '[^[:alnum:]]+', ' ', 'g'))",
            persisted=True,
        ),
    )

    document: Mapped[Document] = relationship(back_populates="chunks")


# A many-to-many link: an issue has many labels, a label is on many issues.
issue_labels = Table(
    "issue_labels",
    Base.metadata,
    Column("issue_id", ForeignKey("issues.id", ondelete="CASCADE"), primary_key=True),
    Column("label_id", ForeignKey("labels.id", ondelete="CASCADE"), primary_key=True),
)


class Label(Base):
    __tablename__ = "labels"
    __table_args__ = (UniqueConstraint("repo_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))


class Issue(Base):
    """An open GitHub issue, as last fetched."""

    __tablename__ = "issues"
    __table_args__ = (UniqueConstraint("repo_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    number: Mapped[int]
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(500))
    author: Mapped[str] = mapped_column(String(100))
    comments: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    labels: Mapped[list[Label]] = relationship(secondary=issue_labels)
    repo: Mapped[Repo] = relationship()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin')", name="users_role_valid"),
        # Every account signs in somehow: with an email and password, or with GitHub.
        CheckConstraint(
            "password_hash IS NOT NULL OR github_id IS NOT NULL", name="users_can_sign_in"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    # Never the password itself: an Argon2 hash (lesson 4.6).
    password_hash: Mapped[str | None] = mapped_column(String(200))
    github_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    github_login: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(10), default="user", server_default="user")
    # Free text the learner writes about their skills; Part 6 uses it to rank issues.
    skills_profile: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    saved_issues: Mapped[list[Issue]] = relationship(secondary="saved_issues")


class RefreshToken(Base):
    """A long-lived token that can be exchanged for a new access token (lesson 4.8)."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Indexed: signing a user out everywhere updates all their tokens by user_id (lesson 4.11).
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Only a hash is stored: a leaked table can't be used to sign in.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


saved_issues = Table(
    "saved_issues",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("issue_id", ForeignKey("issues.id", ondelete="CASCADE"), primary_key=True),
    Column("saved_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


class IssueNote(Base):
    """A learner's private note about an issue (built in the review lesson, 4.16)."""

    __tablename__ = "issue_notes"
    __table_args__ = (
        CheckConstraint("length(body) > 0", name="issue_notes_body_not_empty"),
        # Serves "my notes on this issue", the query behind GET /issues/{id}/notes.
        Index("ix_issue_notes_user_issue", "user_id", "repo_id", "issue_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    issue_number: Mapped[int]
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IdempotencyKey(Base):
    """Remembers the response to a POST so a retried request isn't applied twice (lesson 4.12)."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(100))
    request_path: Mapped[str] = mapped_column(String(300))
    response_status: Mapped[int]
    response_body: Mapped[Any] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
