"""widen entities.phone and entities.address for encrypted PII

Revision ID: d4a8f62c0e45
Revises: c8f31e5a2b10
Create Date: 2026-04-21 01:00:00.000000

Column-level encryption via `app.db.types.EncryptedString` stores Fernet
ciphertext (base64 ASCII with a short version prefix), which is larger
than the plaintext. Phone widens 64 → 255; address becomes String(1024)
instead of open-ended Text so the length is explicit and indexable. Old
plaintext values remain legible because the column is still textual and
the decrypt helper passes through anything without the `enc:v1:` prefix.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4a8f62c0e45"
down_revision: str | Sequence[str] | None = "c8f31e5a2b10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "entities",
        "phone",
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "entities",
        "address",
        existing_type=sa.Text(),
        type_=sa.String(length=1024),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Only safe to run before any encrypted values are persisted.
    op.alter_column(
        "entities",
        "address",
        existing_type=sa.String(length=1024),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.alter_column(
        "entities",
        "phone",
        existing_type=sa.String(length=255),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
