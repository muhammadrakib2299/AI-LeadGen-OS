# AI LeadGen OS — Consolidated Overview

> A merged, realistic blueprint derived from the original vision (`1.txt`) and the critical review (`2.txt`). The goal: ship a compliant, cost-controlled, production-grade lead generation platform — not a demo that dies on contact with real data.

---

## 1. Vision (Refined)

**AI LeadGen OS** turns a natural-language query into a clean, enriched, exportable dataset of entities (businesses, institutions, services) by orchestrating APIs, crawlers, and AI — with compliance, cost, and data quality as first-class concerns.

**Refined positioning:** Not "scrape anything" — instead **"legally aggregate, intelligently enrich, and rigorously verify"** public business data.

---

## 2. What Changes vs. Original Plan

| Area | Original (1.txt) | Improved |
|---|---|---|
| Scope | "Any query, any source" | Constrained entity types with query validator |
| Data sources | LinkedIn, social scraping | **Official APIs first** (Google Places, Yelp, Crunchbase, Companies House, OpenCorporates) |
| "No manual effort" | Promised | Human-in-the-loop review queue for <90% confidence rows |
| "Real-time" | Implied | Batch jobs with honest ETAs (5–30 min) |
| Stack | n8n + Mongo + PG + React + Next + FastAPI + Node | **Single runtime** (Node/TS or Python), Postgres-only with JSONB |
| Workers | 4+ parallel worker types | 1 worker + priority queue in MVP, scale later |
| AI | OpenAI only | Tiered: cheap model default, premium only when needed |
| Output | CSV/API/Dashboard from day 1 | CSV → API → Dashboard (phased) |

---

## 3. Core Requirements

### 3.1 Functional
1. **Query intake** — natural language → validated structured query (entity type, location, filters). Reject or disambiguate vague input.
2. **Source routing** — `cache → free API → paid API → compliant scrape` (SmartRouter).
3. **Entity discovery** — return a deduplicated master list with provenance per field.
4. **Enrichment** — email, phone, website, social, address, custom attributes per entity type.
5. **Verification** — email syntax + MX + optional SMTP ping; phone format; URL liveness.
6. **Deduplication** — fuzzy match on name+domain+address; merge with source precedence rules.
7. **Quality scoring** — 0–100 per record based on completeness, freshness, source trust.
8. **Review queue** — low-confidence rows surfaced to a human before export.
9. **Export** — CSV/Excel first; later JSON API, webhook, Google Sheets sync, CRM push.
10. **Refresh** — scheduled re-verification of aged records.

### 3.2 Non-Functional
- **Compliance:** GDPR/CCPA opt-out honoring, robots.txt respected, ToS-safe source list, data retention policy, audit log of every fetch (URL, timestamp, legal basis).
- **Cost ceiling:** per-job budget cap; hard stop when exceeded.
- **Observability:** structured logs, per-source success rate, per-query cost, queue depth dashboards.
- **Resilience:** retries w/ exponential backoff, circuit breakers per external dependency, idempotent jobs.
- **Rate limits:** per-domain throttle, rotating proxies for scraping, respect `Retry-After`.
- **Security:** secrets in vault (not `.env` committed), least-privilege DB roles, PII encrypted at rest.

---

## 4. Phased Delivery Plan

### Phase 0 — Legal & Setup (Week 0, 3–4 days)
- Pick initial jurisdiction (EU or US) and write a one-page compliance policy.
- List allowed data sources + their ToS constraints.
- Register API keys: Google Places, Hunter.io (email verify), OpenAI/Anthropic, optional SERP API.
- Repo scaffold, CI, env management, Sentry, basic logging.

### Phase 1 — MVP Pipeline (Weeks 1–2)
**Single source → single worker → CSV out.** No UI.
- Input: CLI or simple POST endpoint accepting `{entity_type, query, location, limit}`.
- Google Places API → website → fetch homepage + `/contact` + `/about` → regex + 1 cheap LLM call for email/phone/social extraction.
- Postgres schema (entities, sources, raw_fetches, exports).
- CSV export via signed URL.
- Basic dedupe (exact domain match).
- **Exit criteria:** 100 leads for "restaurants in Paris" with ≥70% email coverage, cost <$0.05/lead.

### Phase 2 — Quality & Queue (Week 3)
- BullMQ + Redis, single worker process, concurrency=5.
- Quality scorer + confidence threshold.
- Email verification (syntax + MX + Hunter/ZeroBounce for top-value only).
- Fuzzy dedupe (name + domain + address normalized).
- Review queue endpoint (list low-confidence rows, mark approved/rejected).

### Phase 3 — Multi-Source Fusion (Weeks 4–6)
- Add second + third source (Yelp, OpenCorporates, Crunchbase free).
- Source precedence + field-level merge with provenance.
- SmartRouter with budget awareness.
- Cache layer (Redis for short-lived, Postgres for permanent email-pattern cache).

### Phase 4 — UI & API (Weeks 7–9)
- Minimal dashboard (Next.js or Retool): submit query, watch progress, browse results, approve review queue, export.
- Authenticated REST API + API keys.
- Saved search templates, scheduled re-runs.

### Phase 5 — SaaS (Month 3+)
- Multi-tenant, billing (Stripe), usage metering, team seats, CRM connectors (HubSpot, Pipedrive), webhook delivery, lead-status pipeline.

---

## 5. Recommended Technology Stack

### Keep it boring and single-language where possible.

| Layer | Recommendation | Why |
|---|---|---|
| **Runtime** | **Node.js + TypeScript** (or Python if team prefers) — pick one | Playwright + BullMQ + Prisma are first-class in TS |
| **API** | Fastify or NestJS | Faster than Express, better DX than FastAPI for Node shops |
| **Queue** | BullMQ + Redis | Replaces n8n for the core pipeline; n8n only for user-facing automations |
| **DB** | **PostgreSQL 16** with JSONB for raw payloads | Drop MongoDB — JSONB covers the use case |
| **Cache** | Redis (same instance as queue) | One less moving part |
| **Scraping** | Playwright (headless Chromium) + Cheerio for static HTML | Puppeteer is redundant |
| **Proxies** | Bright Data / Oxylabs rotating residential (only if needed) | Budget-dependent |
| **AI** | **Tiered routing**: Haiku/gpt-4o-mini for extraction, Sonnet/gpt-4o only for ambiguous cases | 5–10× cost reduction |
| **Email verify** | Hunter.io or ZeroBounce | Cheaper than building yourself |
| **Maps/Places** | Google Places API (primary), Foursquare (fallback) | Licensed, legal |
| **SERP** | SerpAPI or Serper.dev | Pay-per-query, no infra |
| **Frontend** | Next.js 15 (App Router) + shadcn/ui + Tailwind | Skip Streamlit — you'll outgrow it fast |
| **Auth** | Clerk or Supabase Auth | Don't build auth |
| **Observability** | Sentry + OpenTelemetry + Grafana Cloud (free tier) | |
| **Hosting** | Railway / Fly.io (MVP) → AWS ECS or Hetzner + Coolify (scale) | |
| **CI/CD** | GitHub Actions | |
| **Secrets** | Doppler or Infisical | |
| **Testing** | Vitest + Playwright test for E2E + nock for HTTP mocks | |

### Explicitly removed from plan
- ❌ MongoDB (JSONB is enough)
- ❌ FastAPI *and* Node (pick one)
- ❌ n8n as core orchestrator (use BullMQ; keep n8n only for customer-facing automation recipes later)
- ❌ LinkedIn scraping (lawsuit risk, breaks constantly)

---

## 6. Architecture (Revised)

```
┌──────────────┐
│  User Query  │  (NL string + filters)
└──────┬───────┘
       ▼
┌──────────────────┐
│ Query Validator  │  rejects vague queries, suggests refinements
│ (LLM + rules)    │
└──────┬───────────┘
       ▼
┌──────────────────┐    ┌───────────────┐
│  SmartRouter     │───▶│  Cost/Budget  │
│  cache→free→paid │    │  Guard        │
└──────┬───────────┘    └───────────────┘
       ▼
┌──────────────────┐
│  Discovery       │  Google Places / Yelp / OpenCorporates
│  (Entity list)   │
└──────┬───────────┘
       ▼
┌──────────────────┐
│  BullMQ Queue    │  priority, retries, circuit breakers
└──────┬───────────┘
       ▼
┌──────────────────┐
│  Worker Pool     │  crawl → extract → verify → score
│  (1→N scalable)  │
└──────┬───────────┘
       ▼
┌──────────────────┐
│  Postgres +      │  entities, raw_fetches (JSONB),
│  JSONB           │  merges, provenance, audit_log
└──────┬───────────┘
       ▼
┌──────────────────┐  score ≥ threshold? ──▶ export-ready
│  Quality Scorer  │  score <  threshold? ──▶ human review queue
└──────┬───────────┘
       ▼
┌──────────────────┐
│  Export Layer    │  CSV / API / Webhook / Sheets / CRM
└──────────────────┘
```

---

## 7. New / Missing Features Worth Adding

1. **Provenance per field** — every value stores `{source, fetched_at, confidence}`. Non-negotiable for trust and debugging.
2. **Budget guard** — per-job and per-tenant spend cap with real-time tracking.
3. **Compliant Mode toggle** — restricts to whitelisted sources only; required for EU users.
4. **Blacklist** — domains/emails never to touch (GDPR erasure requests, customer opt-outs).
5. **Freshness badges** — UI shows age of each field; auto-flag >90 days.
6. **Search templates** — saved parameterized queries ("B2B SaaS in {city}").
7. **Scheduled exports** — weekly push to Google Sheets / S3.
8. **Lead status pipeline** — New → Contacted → Responded → Converted (CRM-lite).
9. **Bulk input mode** — upload CSV of company names/URLs to enrich (often the fastest path to revenue; see §8).
10. **Attribution report** — per-export breakdown of source mix, cost, quality distribution.
11. **Webhook delivery** — push enriched leads to user systems when ready.
12. **AI Ask Mode** — RAG over the enriched DB; answer "which universities teach X in English" across collected data.
13. **Deliverability preview** — for email leads, show risk of bounces before export.
14. **Rate-limit transparency** — show user exactly why a job is slow (API cap, proxy throttle, etc.).

---

## 8. Go-to-Market Shortcut

The fastest-to-revenue slice is **bulk enrichment**, not discovery:
- **Input:** user uploads CSV of 500 company names or domains.
- **Output:** enriched CSV (email, phone, socials, headcount, industry) in ~10 minutes.
- **Why:** no discovery = no "any query" complexity, lower cost, users already have the list, ToS-safer.
- Ship this before full discovery. Use it to fund development of the discovery engine.

---

## 9. Cost Model (Realistic, per 1000 entities)

| Item | Low | High | Mitigation |
|---|---|---|---|
| Google Places | $17 | $32 | Cache results 30 days |
| SERP API | $0 | $100 | Only for discovery fallback |
| LLM (tiered) | $15 | $80 | Use mini models; cache structured outputs |
| Email verify | $4 | $10 | Verify only high-value leads |
| Proxies | $0 | $50 | Avoid until needed |
| Infra | $20 | $50 | |
| **Total** | **~$56** | **~$322** | vs. $400–1050 in original estimate |

Pricing: charge **$0.50–$2.00 per verified lead** or tiered monthly plans with included credits. Healthy margin if you enforce caching aggressively.

---

## 10. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| GDPR/CCPA complaint | Medium | Critical | Compliant Mode, opt-out endpoint, audit log |
| Source ToS violation / IP ban | High | High | API-first, rotating proxies only as last resort, per-domain rate limits |
| LLM cost runaway | High | High | Budget guard, tiered models, response cache |
| Data quality <80% | High | High | Quality scorer + review queue + provenance |
| Vendor API price hike | Medium | Medium | SmartRouter abstracts sources, swappable |
| Single-region outage | Low | Medium | Stateless workers, Postgres backups, multi-region later |

---

## 11. Definition of Done (MVP)

- [ ] Submit "restaurants in Paris, limit 100" → CSV in <10 min
- [ ] ≥70% of rows have verified email or phone
- [ ] ≥95% of rows have website + name + address
- [ ] Zero PII collected outside compliant source list
- [ ] Per-job cost logged and under $5
- [ ] Failures retry + alert; no silent drops
- [ ] 80% unit test coverage on extractors and scorer
- [ ] Audit log entry per external fetch
- [ ] Compliance page published, opt-out email endpoint live

---

## 12. What NOT to Build (Explicit)

- ❌ LinkedIn scraping — legal risk, constant breakage.
- ❌ "Real-time" claims — crawling is inherently batch.
- ❌ "Any query" — constrain to validated entity types.
- ❌ Multi-worker types before single worker is reliable.
- ❌ Dashboard before the pipeline produces clean data.
- ❌ Multi-tenant auth before you have paying users.
- ❌ Mobile app — web is enough for years.

---

## 13. Immediate Next Steps (This Week)

1. Pick runtime (Node/TS vs Python) — decide once, stop debating.
2. Create repo, CI, Sentry, Postgres on Railway.
3. Get Google Places API key + OpenAI/Anthropic key.
4. Write compliance policy (1 page).
5. Ship the Phase 1 pipeline end-to-end for ONE query. Nothing else matters until this works.

---

*Source docs: `1.txt` (original vision), `2.txt` (critical review). This overview supersedes both.*
