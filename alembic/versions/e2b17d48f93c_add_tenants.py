"""add tenants table and users.tenant_id

Revision ID: e2b17d48f93c
Revises: d4a8f62c0e45
Create Date: 2026-04-21 01:30:00.000000

Multi-tenant foundation. Creates `tenants` and attaches `users.tenant_id`.
Existing users (there aren't many yet — single-operator deploys) are
backfilled: we create one tenant per user and set the FK.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e2b17d48f93c"
down_revision: str | Sequence[str] | None = "d4a8f62c0e45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
    )

    # tenant_id starts nullable so we can backfill before enforcing NOT NULL.
    op.add_column(
        "users",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    # Backfill: one tenant per existing user, named after their email
    # domain. Done in Python so we can correlate each INSERT back to its
    # source user row — INSERT ... RETURNING in a CTE can't carry that
    # correlation in plain SQL.
    conn = op.get_bind()
    users = conn.execute(
        sa.text("SELECT id, email FROM users WHERE tenant_id IS NULL")
    ).fetchall()
    for user in users:
        domain = user.email.split("@", 1)[1] if "@" in user.email else user.email
        row = conn.execute(
            sa.text(
                "INSERT INTO tenants (id, name) "
                "VALUES (gen_random_uuid(), :name) RETURNING id"
            ),
            {"name": domain},
        ).first()
        tenant_id = row.id if row else None
        conn.execute(
            sa.text("UPDATE users SET tenant_id = :tid WHERE id = :uid"),
            {"tid": tenant_id, "uid": user.id},
        )

    op.alter_column("users", "tenant_id", nullable=False)
    op.create_foreign_key(
        "fk_users_tenant_id",
        "users",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_tenant_id", "users", type_="foreignkey")
    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_column("users", "tenant_id")
    op.drop_table("tenants")
