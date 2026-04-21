"""add s3_export_destinations

Revision ID: f8d23e9a4b71
Revises: e3a51f7b8c92
Create Date: 2026-04-21 05:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f8d23e9a4b71"
down_revision: str | Sequence[str] | None = "e3a51f7b8c92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "s3_export_destinations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bucket", sa.String(length=255), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("access_key_id", sa.String(length=255), nullable=False),
        sa.Column("secret_access_key", sa.String(length=1024), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_s3_export_destinations"),
        sa.UniqueConstraint("tenant_id", name="uq_s3_export_destinations_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_s3_export_destinations_tenant_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("s3_export_destinations")
