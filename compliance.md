# Compliance Policy — AI LeadGen OS

**Jurisdiction:** EU + UK (GDPR and UK GDPR)
**Last updated:** 2026-04-16
**Owner:** Combosoft Ltd. (`mrabbani@combosoft.co.uk`)

This document is the operating policy for how AI LeadGen OS collects, stores, and processes data about European businesses and the individuals associated with them (e.g. business contact emails). It is the source of truth — if code contradicts this document, the code is wrong.

---

## 1. Scope

We collect **publicly available business information** about legal entities operating in the EU and UK: company name, registered address, website, business phone, business email (generic and role-based), and public social profiles.

We do **not** collect:
- Special category data (health, political opinion, religion, etc.)
- Data from behind logins (LinkedIn profiles, private directories)
- Data from sources whose ToS prohibit automated collection
- Personal data unrelated to a business role

---

## 2. Legal Basis (Art. 6 GDPR)

Primary basis: **Legitimate Interest (Art. 6(1)(f))** — B2B outreach to business contacts using publicly listed business information, where the data subject's rights do not override our interest.

A Legitimate Interest Assessment (LIA) is stored at `docs/lia.md` and reviewed quarterly.

We do **not** rely on consent for collection, because the data subjects (business contacts) have not opted in. This means:
- We must honor objections immediately (Art. 21)
- We must provide clear information about the source of the data (Art. 14)
- Sensitive personal data is out of scope

---

## 3. Data Subject Rights

We honor all GDPR rights:

| Right | Implementation |
|---|---|
| Access (Art. 15) | Email `privacy@combosoft.co.uk` — we respond within 30 days with the data we hold |
| Rectification (Art. 16) | Same endpoint — corrections applied within 30 days |
| Erasure (Art. 17) | `POST /privacy/opt-out` endpoint + email route — record is added to permanent blacklist (see §5) |
| Restriction (Art. 18) | Manual flag on record; excluded from all exports |
| Objection (Art. 21) | Same as erasure — blacklist entry |
| Portability (Art. 20) | Export in JSON/CSV on request |

Response SLA: **30 calendar days**, tracked as a ticket.

---

## 4. Source Rules (ToS Compliance)

- Only sources listed in `sources.md` may be queried by the pipeline.
- Every source has its ToS reviewed before inclusion.
- `robots.txt` is honored on all web fetches.
- Per-domain rate limits are enforced by the SmartRouter.
- **LinkedIn, Facebook, Instagram, and other social-behind-login sources are banned.**
- Scraping is a last resort — APIs first. Scraping of a non-API source requires a written ToS review entry.

---

## 5. Blacklist (Permanent Opt-Out)

- Table `blacklist` stores `(email, domain, reason, created_at)`.
- Every extraction pipeline consults the blacklist before writing to `entities`.
- Blacklist is **never deleted** — re-adding a contact after an opt-out is a policy violation.
- Opt-out requests arriving via email or the `/privacy/opt-out` endpoint are inserted within 72 hours.

---

## 6. Data Retention

| Data | Retention |
|---|---|
| Raw API/HTML fetches (`raw_fetches`) | 90 days (for debugging), then deleted |
| Enriched entity records | 24 months from last verification, or until opt-out |
| Audit log | 7 years (legal hold) |
| Blacklist | Indefinite |
| Export files (signed URLs) | 30 days |
| Customer account data | While account is active + 90 days |

A daily cron job (`scripts/retention_sweep.py`) enforces these windows.

---

## 7. Audit Log (Non-Negotiable)

Every external fetch writes one row to `audit_log`:
- `url` or API endpoint
- `timestamp`
- `source_id`
- `legal_basis` (always `legitimate_interest` for Phase 1)
- `response_status`
- `bytes_fetched`
- `job_id` (link to the job that triggered it)

Audit log is append-only and used for:
- Responding to data subject access requests
- Proving compliance during an audit
- Debugging pipeline behavior

---

## 8. Security

- Secrets live in a secrets manager (Doppler or Infisical), **never** in the repo or committed `.env`.
- Postgres roles follow least privilege: app role has no `DROP` rights; migrations run under a separate role.
- PII fields are encrypted at rest (Postgres `pgcrypto` for `email`, `phone` on the `entities` table).
- TLS is required for every outbound HTTP request. Certificate verification is never disabled.
- Backups are encrypted; backup restore drills run quarterly.

---

## 9. Sub-Processors

Current sub-processors (services that receive personal data in processing):

| Vendor | Purpose | Region | DPA signed |
|---|---|---|---|
| Google (Places API) | Entity discovery | EU/Global | Standard Contractual Clauses |
| Anthropic / OpenAI | LLM extraction (prompts may contain business names, URLs) | US | DPA available — must sign before Phase 1 launch |
| Hunter.io / ZeroBounce | Email verification | EU/US | DPA available |
| Sentry | Error tracking (no PII in payloads — scrubbing rules enforced) | EU region | DPA available |
| Railway / Fly.io | Hosting | EU region preferred | DPA available |

**Action item for Phase 0:** sign DPAs with Anthropic, Hunter, Sentry, and hosting provider before Phase 1 goes live.

---

## 10. International Transfers

When a sub-processor is outside the EU/UK (e.g. Anthropic US), we rely on:
- Standard Contractual Clauses (SCCs), and
- Transfer Risk Assessment stored at `docs/tra.md`.

No lead data is transferred outside the EU/UK except for the specific processing steps listed above (e.g. LLM extraction prompt).

---

## 11. Contact

- **Data controller:** Combosoft Ltd.
- **Privacy contact:** `privacy@combosoft.co.uk`
- **Opt-out endpoint:** `POST /privacy/opt-out { "email": "...", "reason": "optional" }`

Supervisory authority for complaints:
- UK: [Information Commissioner's Office](https://ico.org.uk)
- EU: the lead authority of the user's member state.

---

## 12. Review

This policy is reviewed:
- Quarterly by the data controller
- On any change to sub-processors or sources
- On any change to GDPR guidance from the ICO / EDPB

Changelog at the bottom of this file.

---

### Changelog

- 2026-04-16 — Initial policy drafted for Phase 0.
