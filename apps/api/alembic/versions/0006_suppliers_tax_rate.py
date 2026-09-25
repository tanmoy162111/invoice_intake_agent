"""suppliers: usual tax rate, so a tax amount can be checked against what to expect

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("tax_rate_bp", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("suppliers", "tax_rate_bp")
