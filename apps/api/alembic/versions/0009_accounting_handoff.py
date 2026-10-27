"""accounting hand-off: connections, supplier contact links, and the durable hand-off record

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_UUID = sa.dialects.postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "accounting_connections",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", _UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("organisation_id", sa.Text, nullable=False),
        sa.Column("organisation_name", sa.Text),
        sa.Column("tokens_encrypted", sa.Text, nullable=False),
        sa.Column("scopes", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.UniqueConstraint("tenant_id", "provider", "organisation_id"),
    )
    op.create_index("ix_accounting_connections_tenant_id", "accounting_connections", ["tenant_id"])

    op.create_table(
        "supplier_accounting_links",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", _UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("supplier_id", _UUID, sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("organisation_id", sa.Text, nullable=False),
        sa.Column("contact_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.UniqueConstraint("tenant_id", "supplier_id", "provider", "organisation_id"),
    )
    op.create_index(
        "ix_supplier_accounting_links_tenant_id", "supplier_accounting_links", ["tenant_id"]
    )
    op.create_index(
        "ix_supplier_accounting_links_supplier_id", "supplier_accounting_links", ["supplier_id"]
    )

    op.create_table(
        "accounting_handoffs",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", _UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("invoice_id", _UUID, sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("organisation_id", sa.Text, nullable=False),
        sa.Column("handoff_key", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("contact_id", sa.Text),
        sa.Column("external_bill_id", sa.Text),
        sa.Column("failure_code", sa.Text),
        sa.Column("manual_action", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("requested_by", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'succeeded', 'failed', 'unknown')",
            name="ck_accounting_handoffs_status",
        ),
        sa.UniqueConstraint("tenant_id", "invoice_id", "provider", "organisation_id"),
        sa.UniqueConstraint("handoff_key"),
    )
    op.create_index("ix_accounting_handoffs_tenant_id", "accounting_handoffs", ["tenant_id"])
    op.create_index("ix_accounting_handoffs_invoice_id", "accounting_handoffs", ["invoice_id"])
    op.create_index("ix_accounting_handoffs_status", "accounting_handoffs", ["status"])


def downgrade() -> None:
    op.drop_table("accounting_handoffs")
    op.drop_table("supplier_accounting_links")
    op.drop_table("accounting_connections")
