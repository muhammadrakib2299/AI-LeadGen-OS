"""add tenant_invites table

Revision ID: e3a51f7b8c92
Revises: d2c84f6a91b3
Create Date: 2026-04-21 04:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e3a51f7b8c92"
down_revision: str | Sequence[str] | None = "d2c84f6a91b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_invites",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_tenant_invites"),
        sa.UniqueConstraint("token_hash", name="uq_tenant_invites_token_hash"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_tenant_invites_tenant_id",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_tenant_invites_invited_by_user_id",
        ),
    )
    op.create_index("ix_tenant_invites_tenant_id", "tenant_invites", ["tenant_id"])
    op.create_index("ix_tenant_invites_email", "tenant_invites", ["email"])


def downgrade() -> None:
    op.drop_index("ix_tenant_invites_email", table_name="tenant_invites")
    op.drop_index("ix_tenant_invites_tenant_id", table_name="tenant_invites")
    op.drop_table("tenant_invites")
