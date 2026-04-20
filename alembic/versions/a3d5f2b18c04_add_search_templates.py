"""add search_templates table

Revision ID: a3d5f2b18c04
Revises: f9c15ab7e3d4
Create Date: 2026-04-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a3d5f2b18c04"
down_revision: str | Sequence[str] | None = "f9c15ab7e3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_templates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("default_limit", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "default_budget_cap_usd",
            sa.Numeric(10, 4),
            nullable=False,
            server_default="5.0",
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_search_templates"),
        sa.UniqueConstraint("name", name="uq_search_templates_name"),
    )


def downgrade() -> None:
    op.drop_table("search_templates")
