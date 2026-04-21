"""add lead_status pipeline columns to entities

Revision ID: d2c84f6a91b3
Revises: c7b92d4f83e1
Create Date: 2026-04-21 04:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2c84f6a91b3"
down_revision: str | Sequence[str] | None = "c7b92d4f83e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "entities",
        sa.Column(
            "lead_status",
            sa.String(length=32),
            nullable=False,
            server_default="new",
        ),
    )
    op.add_column(
        "entities",
        sa.Column("lead_status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("entities", sa.Column("lead_notes", sa.Text(), nullable=True))
    op.create_index("ix_entities_lead_status", "entities", ["lead_status"])


def downgrade() -> None:
    op.drop_index("ix_entities_lead_status", table_name="entities")
    op.drop_column("entities", "lead_notes")
    op.drop_column("entities", "lead_status_changed_at")
    op.drop_column("entities", "lead_status")
