-- Runs once on first Postgres container start.
-- Enables the extensions Phase 1 needs.

CREATE EXTENSION IF NOT EXISTS pgcrypto;      -- encrypted PII columns
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- fuzzy dedupe (Phase 2)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";   -- uuid generation
CREATE EXTENSION IF NOT EXISTS vector;        -- embeddings (Ask Mode v2)
