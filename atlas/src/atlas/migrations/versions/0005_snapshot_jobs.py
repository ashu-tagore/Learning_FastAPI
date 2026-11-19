"""snapshot jobs

Revision ID: 0005
Revises: 0004

Written by hand: autogenerate does not compare CHECK constraints, so it would not notice
the new job kind (lesson 5.4).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("jobs_kind_valid", "jobs", type_="check")
    op.create_check_constraint(
        "jobs_kind_valid", "jobs", "kind IN ('sync_repo', 'sync_issues', 'snapshot_repo')"
    )


def downgrade() -> None:
    # Snapshot jobs would break the narrower rule, so they go first.
    op.execute("DELETE FROM jobs WHERE kind = 'snapshot_repo'")
    op.drop_constraint("jobs_kind_valid", "jobs", type_="check")
    op.create_check_constraint("jobs_kind_valid", "jobs", "kind IN ('sync_repo', 'sync_issues')")
