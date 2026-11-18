"""Part 3 tables: repositories, documents, chunks, issues and labels

Revision ID: 0001
Revises:
Create Date: 2026-11-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("full_name"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("kind IN ('code', 'doc')", name="documents_kind_valid"),
        sa.ForeignKeyConstraint(["repo_id"], ["repos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "path"),
    )
    op.create_table(
        "issues",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("author", sa.String(length=100), nullable=False),
        sa.Column("comments", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["repo_id"], ["repos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "number"),
    )
    op.create_table(
        "labels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["repo_id"], ["repos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "name"),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('english', regexp_replace(text, '[^[:alnum:]]+', ' ', 'g'))",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.CheckConstraint("start_line <= end_line", name="chunks_line_range_valid"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], unique=False, postgresql_using="gin")
    op.create_table(
        "issue_labels",
        sa.Column("issue_id", sa.Integer(), nullable=False),
        sa.Column("label_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["issue_id"], ["issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["label_id"], ["labels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("issue_id", "label_id"),
    )


def downgrade() -> None:
    op.drop_table("issue_labels")
    op.drop_index("ix_chunks_tsv", table_name="chunks", postgresql_using="gin")
    op.drop_table("chunks")
    op.drop_table("labels")
    op.drop_table("issues")
    op.drop_table("documents")
    op.drop_table("repos")
