"""add pipedrive_integrations table

Revision ID: d8a3c91f6b27
Revises: f8d23e9a4b71
Create Date: 2026-04-23 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d8a3c91f6b27"
down_revision: str | Sequence[str] | None = "f8d23e9a4b71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipedrive_integrations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("api_token", sa.String(length=1024), nullable=False),
        sa.Column("company_domain", sa.String(length=128), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_pipedrive_integrations"),
        sa.UniqueConstraint("tenant_id", name="uq_pipedrive_integrations_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_pipedrive_integrations_tenant_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("pipedrive_integrations")
