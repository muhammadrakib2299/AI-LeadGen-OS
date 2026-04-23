"""api_keys: rotation columns (expires_at, rotated_to_id)

Revision ID: f4a1c8d527e6
Revises: e9b714c2f8a3
Create Date: 2026-04-23 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f4a1c8d527e6"
down_revision: str | Sequence[str] | None = "e9b714c2f8a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "api_keys",
        sa.Column("rotated_to_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_api_keys_rotated_to_id",
        source_table="api_keys",
        referent_table="api_keys",
        local_cols=["rotated_to_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_api_keys_rotated_to_id", "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "rotated_to_id")
    op.drop_column("api_keys", "expires_at")
