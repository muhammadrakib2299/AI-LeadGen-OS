"""add job_type and seed_entities

Revision ID: f9c15ab7e3d4
Revises: e7b92f4a0c11
Create Date: 2026-04-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f9c15ab7e3d4"
down_revision: str | Sequence[str] | None = "e7b92f4a0c11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "job_type",
            sa.String(length=32),
            nullable=False,
            server_default="discovery",
        ),
    )
    op.add_column("jobs", sa.Column("seed_entities", postgresql.JSONB(), nullable=True))
    op.alter_column("jobs", "job_type", server_default=None)


def downgrade() -> None:
    op.drop_column("jobs", "seed_entities")
    op.drop_column("jobs", "job_type")
