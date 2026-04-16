# AI LeadGen OS — TODO

Derived from `overview.md`. Check items off as you ship. Do not start a phase until the previous phase's exit criteria are met.

---

## Phase 0 — Legal & Setup (Week 0, 3–4 days)

- [ ] Pick initial jurisdiction (EU or US)
- [ ] Write a 1-page compliance policy (GDPR/CCPA, retention, opt-out)
- [ ] List allowed data sources + ToS constraints per source
- [ ] Decide runtime: **Node.js + TypeScript** vs Python (pick one, stop debating)
- [ ] Register API keys:
  - [ ] Google Places API
  - [ ] Hunter.io (or ZeroBounce) for email verification
  - [ ] OpenAI and/or Anthropic
  - [ ] SerpAPI / Serper.dev (optional)
- [ ] Repo scaffold + GitHub Actions CI
- [ ] Provision Postgres 16 on Railway
- [ ] Provision Redis (queue + cache, same instance)
- [ ] Set up Sentry + structured logging
- [ ] Secrets manager (Doppler or Infisical) — no committed `.env`
- [ ] Publish compliance page + opt-out email endpoint

---

## Phase 1 — MVP Pipeline (Weeks 1–2)

**Goal:** single source → single worker → CSV out. No UI.

- [ ] Input API: CLI or `POST /jobs` accepting `{entity_type, query, location, limit}`
- [ ] Query Validator (LLM + rules) — rejects/disambiguates vague input
- [ ] Google Places API integration (discovery)
- [ ] Crawler: fetch homepage + `/contact` + `/about` (Playwright + Cheerio)
- [ ] Extraction: regex + 1 cheap LLM call for email / phone / social
- [ ] Postgres schema: `entities`, `sources`, `raw_fetches` (JSONB), `exports`, `audit_log`
- [ ] Provenance per field: `{source, fetched_at, confidence}`
- [ ] Audit log entry per external fetch (URL, timestamp, legal basis)
- [ ] Basic dedupe (exact domain match)
- [ ] CSV export via signed URL
- [ ] Per-job cost logging
- [ ] Retries with exponential backoff; no silent drops

**Exit criteria**
- [ ] 100 leads for "restaurants in Paris" in <10 min
- [ ] ≥70% email coverage
- [ ] ≥95% rows have website + name + address
- [ ] Cost <$0.05/lead and <$5/job
- [ ] 80% unit test coverage on extractors

---

## Phase 2 — Quality & Queue (Week 3)

- [ ] BullMQ + Redis, single worker process, concurrency=5
- [ ] Priority queue + circuit breakers per external dependency
- [ ] Quality scorer (0–100: completeness, freshness, source trust)
- [ ] Confidence threshold → route to review queue
- [ ] Email verification: syntax + MX + Hunter/ZeroBounce for top-value only
- [ ] Phone format + URL liveness checks
- [ ] Fuzzy dedupe on normalized name + domain + address
- [ ] Source precedence rules for field merge
- [ ] Review queue endpoints: list low-confidence rows, approve/reject
- [ ] Idempotent job handling

---

## Phase 3 — Multi-Source Fusion (Weeks 4–6)

- [ ] Add Yelp integration
- [ ] Add OpenCorporates integration
- [ ] Add Crunchbase (free tier) integration
- [ ] Foursquare fallback for Places
- [ ] SmartRouter: `cache → free API → paid API → compliant scrape`
- [ ] Budget guard: per-job and per-tenant spend cap with real-time tracking
- [ ] Cache layer: Redis short-lived + Postgres permanent email-pattern cache
- [ ] Tiered LLM routing: mini model default, premium only for ambiguous cases
- [ ] Per-domain rate limits + respect `Retry-After`
- [ ] Rotating proxies (only if scraping actually needed)

---

## Phase 4 — UI & API (Weeks 7–9)

- [ ] Next.js 15 (App Router) + shadcn/ui + Tailwind dashboard
  - [ ] Submit query
  - [ ] Watch job progress
  - [ ] Browse results
  - [ ] Approve/reject review queue
  - [ ] Export
- [ ] Auth: Clerk or Supabase Auth
- [ ] REST API + API keys (authenticated)
- [ ] Saved search templates (e.g. "B2B SaaS in {city}")
- [ ] Scheduled re-runs + re-verification of aged records
- [ ] Freshness badges in UI (flag fields >90 days)
- [ ] Rate-limit transparency (show why a job is slow)

---

## Phase 5 — SaaS (Month 3+)

- [ ] Multi-tenant data isolation
- [ ] Stripe billing + usage metering
- [ ] Team seats
- [ ] Webhook delivery of enriched leads
- [ ] CRM connectors: HubSpot, Pipedrive
- [ ] Lead status pipeline: New → Contacted → Responded → Converted
- [ ] Attribution report (source mix, cost, quality per export)
- [ ] Google Sheets / S3 scheduled exports
- [ ] Deliverability preview before export
- [ ] AI Ask Mode (RAG over enriched DB)

---

## Go-to-Market Shortcut — Bulk Enrichment (ship EARLY, §8)

Fastest path to revenue. Consider shipping before full discovery.

- [ ] Bulk CSV upload: 500 company names or domains
- [ ] Enrichment pipeline reusing Phase 1–2 workers
- [ ] Output: enriched CSV in ~10 min
- [ ] Pricing page: $0.50–$2.00 per verified lead or tiered monthly plans

---

## Cross-Cutting (ongoing)

### Compliance
- [ ] Compliant Mode toggle (whitelisted sources only — required for EU)
- [ ] Blacklist: domains/emails never to touch (GDPR erasure, opt-outs)
- [ ] Honor `robots.txt`
- [ ] Data retention policy enforced in DB
- [ ] PII encrypted at rest

### Observability
- [ ] Structured logs
- [ ] Per-source success rate dashboard
- [ ] Per-query cost dashboard
- [ ] Queue depth dashboard
- [ ] OpenTelemetry + Grafana Cloud (free tier)

### Security
- [ ] Least-privilege Postgres roles
- [ ] Secrets never in repo
- [ ] API key rotation policy

### Testing
- [ ] Vitest unit tests
- [ ] Playwright E2E
- [ ] nock HTTP mocks for external APIs

---

## Do NOT Build (§12)

- ❌ LinkedIn scraping
- ❌ "Real-time" claims (pipeline is batch)
- ❌ "Any query" support — constrain to validated entity types
- ❌ Multiple worker types before one is reliable
- ❌ Dashboard before pipeline produces clean data
- ❌ Multi-tenant auth before paying users
- ❌ Mobile app
- ❌ MongoDB (use Postgres JSONB)
- ❌ FastAPI *and* Node (pick one)
- ❌ n8n as core orchestrator (BullMQ instead)

---

## This Week (§13)

1. [ ] Pick runtime (Node/TS vs Python)
2. [ ] Create repo, CI, Sentry, Postgres on Railway
3. [ ] Get Google Places + OpenAI/Anthropic keys
4. [ ] Write 1-page compliance policy
5. [ ] Ship Phase 1 pipeline end-to-end for ONE query — nothing else matters until this works
