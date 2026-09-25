"""invoices: index for finding a supplier's earlier invoices (duplicate detection)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-25
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_invoices_tenant_supplier", "invoices", ["tenant_id", "supplier_id"])


def downgrade() -> None:
    op.drop_index("ix_invoices_tenant_supplier", table_name="invoices")
