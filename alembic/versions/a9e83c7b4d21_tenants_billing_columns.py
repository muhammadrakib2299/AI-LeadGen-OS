"""add plan, stripe_customer_id, stripe_subscription_id to tenants

Revision ID: a9e83c7b4d21
Revises: f7d92a3b8c14
Create Date: 2026-04-21 02:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9e83c7b4d21"
down_revision: str | Sequence[str] | None = "f7d92a3b8c14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "plan", sa.String(length=32), nullable=False, server_default="free"
        ),
    )
    op.add_column(
        "tenants",
        sa.Column("stripe_customer_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("stripe_subscription_id", sa.String(length=128), nullable=True),
    )
    op.create_unique_constraint(
        "uq_tenants_stripe_customer_id", "tenants", ["stripe_customer_id"]
    )
    op.create_unique_constraint(
        "uq_tenants_stripe_subscription_id", "tenants", ["stripe_subscription_id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_tenants_stripe_subscription_id", "tenants", type_="unique"
    )
    op.drop_constraint("uq_tenants_stripe_customer_id", "tenants", type_="unique")
    op.drop_column("tenants", "stripe_subscription_id")
    op.drop_column("tenants", "stripe_customer_id")
    op.drop_column("tenants", "plan")
