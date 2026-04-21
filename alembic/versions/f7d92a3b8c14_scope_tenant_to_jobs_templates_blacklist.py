"""scope jobs, search_templates, blacklist to a tenant

Revision ID: f7d92a3b8c14
Revises: e2b17d48f93c
Create Date: 2026-04-21 02:00:00.000000

Adds `tenant_id` to the ownership-bearing tables. Existing rows get
backfilled with the single existing tenant (for deploys that predate
multi-tenant); if the deploy has multiple tenants already, we bail —
at that point the operator owes us a manual choice.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f7d92a3b8c14"
down_revision: str | Sequence[str] | None = "e2b17d48f93c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: list[tuple[str, bool]] = [
    # (table, has_unique_constraints_to_rebuild)
    ("jobs", False),
    ("search_templates", True),
    ("blacklist", True),
]


def upgrade() -> None:
    conn = op.get_bind()
    tenants = conn.execute(sa.text("SELECT id FROM tenants ORDER BY created_at ASC")).fetchall()
    default_tenant_id: object | None = None
    if len(tenants) == 1:
        default_tenant_id = tenants[0].id
    elif len(tenants) > 1:
        # Multiple tenants already — we'd need to know who owns each job.
        # For a deploy that got here, pre-seed a mapping manually before
        # running this migration. See docs/tos-snapshots/ for the story.
        raise RuntimeError(
            "Cannot auto-backfill tenant_id with multiple tenants; "
            "seed the mapping manually and re-run."
        )

    for table, _ in _TABLES:
        op.add_column(
            table, sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True)
        )
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        if default_tenant_id is not None:
            conn.execute(
                sa.text(f"UPDATE {table} SET tenant_id = :tid WHERE tenant_id IS NULL"),
                {"tid": default_tenant_id},
            )
        op.alter_column(table, "tenant_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_tenant_id",
            table,
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # Replace global unique constraints with per-tenant ones.
    op.drop_constraint("uq_search_templates_name", "search_templates", type_="unique")
    op.create_unique_constraint(
        "uq_search_templates_tenant_name",
        "search_templates",
        ["tenant_id", "name"],
    )
    op.drop_constraint("uq_blacklist_email", "blacklist", type_="unique")
    op.drop_constraint("uq_blacklist_domain", "blacklist", type_="unique")
    op.create_unique_constraint(
        "uq_blacklist_tenant_email", "blacklist", ["tenant_id", "email"]
    )
    op.create_unique_constraint(
        "uq_blacklist_tenant_domain", "blacklist", ["tenant_id", "domain"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_blacklist_tenant_domain", "blacklist", type_="unique")
    op.drop_constraint("uq_blacklist_tenant_email", "blacklist", type_="unique")
    op.create_unique_constraint("uq_blacklist_email", "blacklist", ["email"])
    op.create_unique_constraint("uq_blacklist_domain", "blacklist", ["domain"])

    op.drop_constraint(
        "uq_search_templates_tenant_name", "search_templates", type_="unique"
    )
    op.create_unique_constraint(
        "uq_search_templates_name", "search_templates", ["name"]
    )

    for table, _ in _TABLES:
        op.drop_constraint(f"fk_{table}_tenant_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_column(table, "tenant_id")
