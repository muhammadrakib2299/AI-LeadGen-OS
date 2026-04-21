"""add hubspot_integrations table

Revision ID: c7b92d4f83e1
Revises: b4f72a9e3d58
Create Date: 2026-04-21 03:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c7b92d4f83e1"
down_revision: str | Sequence[str] | None = "b4f72a9e3d58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hubspot_integrations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("access_token", sa.String(length=1024), nullable=False),
        sa.Column("last_export_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hubspot_integrations"),
        sa.UniqueConstraint("tenant_id", name="uq_hubspot_integrations_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_hubspot_integrations_tenant_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("hubspot_integrations")
