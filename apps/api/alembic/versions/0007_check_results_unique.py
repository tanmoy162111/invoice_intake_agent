"""check_results: one result per invoice, check and rule version

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-25
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        op.f("uq_check_results_invoice_id"), "check_results",
        ["invoice_id", "check_code", "rule_version"],
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_check_results_invoice_id"), "check_results", type_="unique")
