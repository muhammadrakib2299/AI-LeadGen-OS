# AI LeadGen OS

**Compliant, cost-controlled lead generation platform for EU/UK B2B data.**

Generate verified business leads from natural-language queries ("restaurants in Paris with vegan options"), or enrich a CSV of company names you already have. Every fetch is GDPR-audited, every cost is capped, every lead is provenance-tracked.

📄 See [`leadgen.html`](leadgen.html) for the full product overview, workflows, and UI mockups.

---

## What it does

| Capability | What you get |
|---|---|
| **Discovery** | NL query → multi-source business search (Google Places, Yelp, Foursquare, OpenCorporates) |
| **Enrichment** | Crawl homepage / about / contact pages → extract email, phone, social handles |
| **Verification** | Email syntax + MX + (optional) Hunter; phone format; URL liveness |
| **Quality scoring** | 0–100 score per lead based on completeness, freshness, source trust |
| **Bulk mode** | Upload 500 company names → enriched CSV in ~10 min |
| **AI Ask Mode** | Natural-language questions over your enriched DB (filter + vector RAG) |
| **CRM connectors** | HubSpot, Pipedrive |
| **Export destinations** | CSV (signed URL), S3, Google Sheets |
| **Multi-tenant SaaS** | Stripe billing, team seats, webhooks, API keys with rotation |
| **Compliance** | GDPR opt-out, audit log per fetch, PII encrypted at rest, robots.txt enforced, Compliant Mode (Tier-1 sources only) |

**334 passing tests.** Single arq worker handles jobs + scheduled retention/reverify cron.

---

## Quickstart

### Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — install: `pip install uv`
- **Docker Desktop** (for local Postgres + Redis)
- **API keys**: Google Places (required), Anthropic (required), OpenAI (optional, for Ask Mode v2 vector search)

### 1. Clone and install

```bash
git clone https://github.com/muhammadrakib2299/AI-LeadGen-OS
cd AI-LeadGen-OS
uv sync --dev
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env, paste API keys
```

For production, use a secrets manager (Doppler, Infisical, AWS SSM) — never deploy with a `.env` file.

### 3. Start local infrastructure

```bash
docker compose up -d
```

Brings up Postgres 16 (with pgvector) on host port **55432** and Redis 7 on host port **6380**. Unusual host ports avoid clashes with other projects.

### 4. Run migrations

```bash
uv run alembic upgrade head
```

### 5. Run the API + worker

Two terminals:

```bash
# Terminal 1 — API
uv run uvicorn app.main:app --reload

# Terminal 2 — worker (executes jobs, runs cron)
uv run arq app.workers.worker.WorkerSettings
```

Visit:
- http://localhost:8000/docs — Swagger UI (every endpoint)
- http://localhost:8000/health — health check

### 6. Run the dashboard

```bash
cd web
npm install
npm run dev
```

Open http://localhost:3000.

### 7. Tests

```bash
# Backend
uv run pytest

# Frontend E2E (one-time setup, then run anytime)
cd web
npx playwright install chromium
npm run test:e2e
```

---

## Architecture

```
┌────────────────┐     ┌───────────────┐     ┌──────────────────┐
│  Next.js SPA   │────▶│ FastAPI       │────▶│ Postgres 16      │
│  (web/)        │     │ (app/)        │     │ + pgvector       │
└────────────────┘     └───────┬───────┘     └──────────────────┘
                               │                       ▲
                               ▼                       │
                       ┌───────────────┐               │
                       │ Redis (arq)   │               │
                       └───────┬───────┘               │
                               │                       │
                               ▼                       │
                       ┌───────────────┐               │
                       │ arq worker    │───────────────┘
                       │ jobs + cron   │
                       └───────┬───────┘
                               │
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
          Google Places    Yelp/4SQ      OpenCorporates
          + crawler        (fallback)    + Hunter (verify)
                │
                ▼
         Anthropic Claude (query parser, extractor)
         OpenAI (embeddings — Ask Mode v2)
```

The arq worker handles two kinds of work:
- **On-demand jobs** — `POST /jobs` enqueues; the worker runs the discovery → crawl → extract → verify → store pipeline.
- **Scheduled cron** — daily retention sweep at 03:00 UTC, daily aged-record reverify at 04:00 UTC. No separate scheduler process.

---

## Project layout

```
app/
  api/             FastAPI routers — auth, jobs, review, leads, dashboard,
                   ask, integrations, billing, webhooks, ...
  core/            config, logging, sentry, telemetry, crypto, api_keys
  db/              SQLAlchemy models, session, encrypted-string type
  extractors/      LLM + regex extraction of contacts from HTML
  models/          Pydantic request/response models
  services/        business logic — runner, discovery, crawler, dedupe,
                   quality, hubspot, pipedrive, google_sheets, ask,
                   embeddings, ...
  workers/         arq tasks (run_job, daily_retention_sweep,
                   daily_reverify_pass)
alembic/           DB migrations (~20 of them)
tests/             pytest — 334 tests, full green suite
web/               Next.js 15 dashboard (App Router, Tailwind, shadcn/ui)
  e2e/             Playwright golden-path tests
scripts/           one-shot ops scripts (retention_sweep, reverify_aged,
                   embed_entities, postgres-init.sql)
```

---

## Compliance posture (full detail in [`compliance.md`](compliance.md))

- **Jurisdiction:** EU + UK (GDPR / UK GDPR).
- **Legal basis:** Legitimate Interest (B2B outreach on publicly listed business info).
- **Opt-out endpoint:** `POST /privacy/opt-out` — fans out to every tenant holding the subject's data.
- **Audit log:** every external fetch logged in `raw_fetches` with URL, status, legal basis, content hash.
- **Encryption at rest:** PII columns (phone, address) encrypted with Fernet, key in `APP_ENCRYPTION_KEY`.
- **Retention:** raw_fetches 90d, exports 30d, entities 24mo, Yelp payloads 24h. Enforced by daily cron.
- **Sources:** whitelisted in [`sources.md`](sources.md). Adding a source requires a ToS review.
- **Banned:** LinkedIn, Facebook, Instagram, Twitter/X, login-walled sites.
- **Compliant Mode:** restricts pipeline to Tier-1 official APIs only — toggle via `COMPLIANT_MODE=true`.

---

## API surface (highlights)

| Method + Path | What it does |
|---|---|
| `POST /auth/register` / `POST /auth/login` | JWT-based account auth |
| `POST /jobs` | Submit a discovery query |
| `GET /jobs/{id}` | Job status + progress |
| `GET /jobs/{id}/export.csv` | Download results |
| `POST /jobs/bulk-csv` | Upload 500-row CSV for enrichment |
| `GET /review` / `POST /review/{id}/approve` | Review-queue workflow |
| `GET /leads` / `PATCH /leads/{id}` | Lead pipeline (new → contacted → ...) |
| `POST /ask` | NL question → structured filter → matching rows |
| `POST /ask/similar` | NL question → vector similarity search |
| `GET /dashboard` | Queue + source health + cost + recent failures |
| `GET /reports/attribution` | Per-tenant source mix and ROI |
| `POST /api-keys` / `POST /api-keys/{id}/rotate` | Programmatic credentials with 24h rotation grace |
| `POST /integrations/{hubspot,pipedrive,s3,google-sheets}/...` | Connect destinations + on-demand export |
| `POST /webhooks` | HMAC-signed event delivery (`job.completed`) |
| `POST /billing/checkout` / `POST /billing/webhook` | Stripe upgrade flow |
| `POST /privacy/opt-out` | Public GDPR erasure endpoint |

Full reference at http://localhost:8000/docs.

---

## License

Proprietary — Combosoft Ltd.

---

## Contributing

Internal project. For bugs or design changes, open a PR against `main` referencing the relevant section of `overview.md` or `compliance.md`.
