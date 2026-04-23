"""entities.embedding — pgvector column for Ask Mode v2

Revision ID: a6d2f8b1e094
Revises: f4a1c8d527e6
Create Date: 2026-04-23 14:00:00.000000

Enables the pgvector extension and adds an `embedding vector(1536)` column
on `entities`. 1536 matches OpenAI text-embedding-3-small, which is the
default wire format for `app/services/embeddings.py`. If you switch to a
different provider with a different dim, change this file AND the
`EMBEDDING_DIM` constant at the same time — mismatched dims will fail
silently at INSERT time.

Only non-PII entity fields are embedded (name / city / country / category);
email, phone, and address are intentionally excluded to sidestep GDPR
data-subject concerns when using a third-party embeddings API.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a6d2f8b1e094"
down_revision: str | Sequence[str] | None = "f4a1c8d527e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    # Idempotent — the dev docker image bundles pgvector; managed Postgres
    # providers (RDS, Cloud SQL, Supabase) all support CREATE EXTENSION
    # vector today. If your instance doesn't, the migration will fail
    # loudly here rather than silently later.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        f"ALTER TABLE entities ADD COLUMN IF NOT EXISTS embedding vector({EMBEDDING_DIM})"
    )
    # ivfflat is overkill until we have a lot of rows; keep the lookup
    # a seq scan for now. Add an HNSW/IVFFlat index in a later migration
    # once volume warrants it (cutoff: ~50k rows per tenant).


def downgrade() -> None:
    op.execute("ALTER TABLE entities DROP COLUMN IF EXISTS embedding")
    # Leave the extension in place — other schemas or tables might use it.
