"""add google_sheets_destinations table

Revision ID: e9b714c2f8a3
Revises: d8a3c91f6b27
Create Date: 2026-04-23 09:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e9b714c2f8a3"
down_revision: str | Sequence[str] | None = "d8a3c91f6b27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "google_sheets_destinations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_account_json", sa.String(length=8192), nullable=False),
        sa.Column("spreadsheet_id", sa.String(length=128), nullable=False),
        sa.Column(
            "worksheet_name",
            sa.String(length=128),
            nullable=False,
            server_default="Leads",
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_google_sheets_destinations"),
        sa.UniqueConstraint(
            "tenant_id", name="uq_google_sheets_destinations_tenant_id"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_google_sheets_destinations_tenant_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("google_sheets_destinations")
