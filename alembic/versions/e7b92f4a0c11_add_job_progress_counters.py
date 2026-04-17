"""add job progress counters

Revision ID: e7b92f4a0c11
Revises: c4a1e0b8d2f3
Create Date: 2026-04-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7b92f4a0c11"
down_revision: str | Sequence[str] | None = "c4a1e0b8d2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("places_discovered", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "jobs",
        sa.Column("places_processed", sa.Integer(), nullable=False, server_default="0"),
    )
    # Drop the server_default so future inserts go through the ORM default.
    op.alter_column("jobs", "places_discovered", server_default=None)
    op.alter_column("jobs", "places_processed", server_default=None)


def downgrade() -> None:
    op.drop_column("jobs", "places_processed")
    op.drop_column("jobs", "places_discovered")
