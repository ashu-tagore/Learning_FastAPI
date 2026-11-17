"""Database tables, described as Python classes (SQLAlchemy ORM).

repos ──< documents ──< chunks
  │
  ├──< issues >──< labels      (issue_labels joins the two)
  └──< labels
"""

from datetime import datetime

from sqlalchemy import (
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
)
from sqlalchemy.dialects.postgresql import TSVECTOR
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
