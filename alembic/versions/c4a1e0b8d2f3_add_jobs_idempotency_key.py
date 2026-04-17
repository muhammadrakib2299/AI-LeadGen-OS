"""add jobs.idempotency_key

Revision ID: c4a1e0b8d2f3
Revises: 6ef78da3bcee
Create Date: 2026-04-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4a1e0b8d2f3"
down_revision: str | Sequence[str] | None = "6ef78da3bcee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.create_unique_constraint("uq_jobs_idempotency_key", "jobs", ["idempotency_key"])


def downgrade() -> None:
    op.drop_constraint("uq_jobs_idempotency_key", "jobs", type_="unique")
    op.drop_column("jobs", "idempotency_key")
