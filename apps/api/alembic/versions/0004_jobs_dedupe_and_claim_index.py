"""jobs: dedupe_key (idempotent enqueue) and an index for claiming

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("dedupe_key", sa.Text(), nullable=True))
    op.create_unique_constraint(op.f("uq_jobs_dedupe_key"), "jobs", ["dedupe_key"])
    op.create_index("ix_jobs_claim", "jobs", ["status", "run_after"])


def downgrade() -> None:
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.drop_constraint(op.f("uq_jobs_dedupe_key"), "jobs", type_="unique")
    op.drop_column("jobs", "dedupe_key")
