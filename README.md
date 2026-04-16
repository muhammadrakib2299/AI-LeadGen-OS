# AI LeadGen OS

Compliant, cost-controlled lead generation platform for **EU/UK B2B data**.

See [`overview.md`](overview.md) for the full blueprint, [`todo.md`](todo.md) for phased delivery, [`compliance.md`](compliance.md) for the GDPR policy, and [`sources.md`](sources.md) for the allowed data source list.

---

## Phase 0 setup

### Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — install: `pip install uv` or see the uv docs
- **Docker Desktop** (for local Postgres + Redis)
- A **Google Places API key** (required for Phase 1 work)
- An **Anthropic** or **OpenAI** API key (required for Phase 1)

### 1. Clone and install dependencies

```bash
git clone <repo-url> ai-leadgen-os
cd ai-leadgen-os
uv sync --dev
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and paste API keys
```

For production, do **not** deploy with a `.env` file. Use a secrets manager:

- [Doppler](https://www.doppler.com) — recommended, good CLI
- [Infisical](https://infisical.com) — open-source alternative

Both have FastAPI/Python SDKs and inject secrets as env vars at runtime.

### 3. Start local infrastructure

```bash
docker compose up -d
```

Brings up Postgres 16 (host port **5433**, container 5432) and Redis 7 (port 6379). Data persists in named volumes. Port 5433 avoids clash with a native Postgres install on Windows.

Check health:

```bash
docker compose ps
```

### 4. Run the dev server

```bash
uv run uvicorn app.main:app --reload
```

### 4b. Run the arq worker (Phase 2+)

Jobs submitted via `POST /jobs` are enqueued to Redis; a separate worker process executes them. Start it in another terminal:

```bash
uv run arq app.workers.worker.WorkerSettings
```

Concurrency defaults to 5 jobs in parallel per worker (`max_jobs` in `WorkerSettings`).

Visit:
- <http://localhost:8000/health>
- <http://localhost:8000/docs> (Swagger UI)
- <http://localhost:8000/privacy/opt-out> (POST only)

### 5. Run tests

```bash
uv run pytest
```

### 6. Lint and format

```bash
uv run ruff check .
uv run ruff format .
```

---

## Project layout

```
app/
  api/          FastAPI routers (health, privacy, jobs in Phase 1)
  core/         config, logging, sentry — cross-cutting setup
  db/           SQLAlchemy models, session, migrations wiring
  extractors/   HTML / LLM extractors (Phase 1)
  models/       Pydantic domain models
  services/     business logic (SmartRouter, Google Places client, ...)
  workers/      BullMQ-equivalent Celery/RQ workers (Phase 2)
tests/          pytest tests
scripts/        ops scripts (postgres-init.sql, retention sweeps, ...)
```

---

## Compliance summary (read [`compliance.md`](compliance.md) for the full policy)

- **Jurisdiction:** EU + UK (GDPR / UK GDPR).
- **Legal basis:** Legitimate Interest (B2B outreach on publicly listed business info).
- **Opt-out endpoint:** `POST /privacy/opt-out`.
- **Audit log:** every external fetch is logged.
- **Sources:** whitelisted in [`sources.md`](sources.md). Adding a source requires a ToS review PR.
- **Banned:** LinkedIn, Facebook, Instagram, Twitter/X, login-walled sites.

---

## Phase 0 exit checklist

- [x] Runtime chosen (Python + uv)
- [x] Jurisdiction chosen (EU/UK)
- [x] Compliance policy drafted
- [x] Source list drafted
- [x] Repo scaffolded
- [x] Local infra via docker-compose
- [x] Logging + Sentry wiring
- [x] FastAPI app with health + opt-out stub
- [x] CI workflow
- [ ] API keys obtained (Google Places, Anthropic)
- [ ] Secrets manager configured
- [ ] DPAs signed (Anthropic, Hunter, Sentry, hosting)

Work through the remaining items, then move to Phase 1.
