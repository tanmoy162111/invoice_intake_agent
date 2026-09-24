"""llm_calls: keep the validated response (extraction cache) and index daily spend lookups

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_calls", sa.Column("response", postgresql.JSONB(), nullable=True))
    op.create_index("ix_llm_calls_tenant_created", "llm_calls", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_tenant_created", table_name="llm_calls")
    op.drop_column("llm_calls", "response")
