# Data Sources — Allowed List

**Rule:** if a source is not on this list, the pipeline must not query it. Adding a source requires a ToS review entry and a pull request.

Target geography: **EU + UK**.

---

## Tier 1 — Official APIs (preferred)

### Google Places API
- **What:** business name, address, phone, website, opening hours, category, rating.
- **Coverage:** excellent across EU/UK.
- **ToS:** [Google Maps Platform Terms](https://cloud.google.com/maps-platform/terms). Key rules:
  - Data may be cached **up to 30 days** for Places Details fields; `place_id` may be stored indefinitely.
  - Must display "Powered by Google" in UI where Places data is shown.
  - No mass export of Google data outside the product (CSV export of enriched records is fine if Google fields are a subset).
- **Cost:** ~$17/1k Place Details calls (with SKUs that vary). Cache aggressively.
- **Our use:** primary entity discovery in Phase 1.

### OpenCorporates
- **What:** company registry data across 140+ jurisdictions including all EU + UK.
- **ToS:** [OpenCorporates API Terms](https://opencorporates.com/info/api-terms-of-use). Attribution required. Non-commercial free tier; commercial needs a paid plan.
- **Coverage:** legal entity names, registration numbers, incorporation date, officers (limited).
- **Our use:** Phase 3 — legal entity verification and deduplication anchor.

### Companies House (UK)
- **What:** official UK companies register.
- **ToS:** [Companies House API ToS](https://developer.company-information.service.gov.uk/terms-and-conditions). Free, attribution required, fair use limits (600 requests / 5 min).
- **Our use:** Phase 3 — UK entity enrichment (registered address, officers, filing history).

### EU Business Registers (BRIS)
- **What:** EU-wide interconnection of national business registers.
- **Access:** through the [e-Justice portal](https://e-justice.europa.eu/content_find_a_company-489-en.do). No official open API yet (as of 2026-04) — country-specific APIs vary.
- **Our use:** deferred to Phase 3+; integrate per-country where APIs exist (e.g. France INSEE SIRENE, Germany Unternehmensregister, Spain RMC).

### France — INSEE SIRENE API
- **What:** French company register (SIRET / SIREN lookups).
- **ToS:** [INSEE API licence](https://api.insee.fr). Free with API key.
- **Our use:** Phase 3 — French lead enrichment.

### Yelp Fusion API
- **What:** business listings, reviews, categories.
- **ToS:** [Yelp API ToS](https://docs.developer.yelp.com/docs/fusion-api-terms-of-use). Attribution required. Cannot store Yelp data for more than **24 hours** except the Yelp ID.
- **Coverage:** mediocre in much of continental EU, good in UK/Ireland.
- **Our use:** Phase 3 — fallback for hospitality / consumer categories in UK.

### Foursquare Places API
- **What:** business listings, categories, location.
- **ToS:** [Foursquare Developer ToS](https://docs.foursquare.com/developer/docs/terms-of-service). Attribution required.
- **Our use:** Phase 3 — fallback for Google Places.

### Crunchbase Basic API
- **What:** company profiles, funding, people, categories.
- **ToS:** [Crunchbase API ToS](https://data.crunchbase.com/docs/using-the-api). Free tier discontinued in 2024 — requires paid plan. Re-evaluate before integrating.
- **Our use:** Phase 3 — tech / startup enrichment. **Gated on budget approval.**

---

## Tier 2 — Search APIs

### SerpAPI / Serper.dev
- **What:** Google Search results as a structured API.
- **ToS:** standard API terms; responses may contain third-party content — do not store raw snippets beyond 30 days.
- **Our use:** discovery fallback when Places doesn't return enough results. Phase 2+.

---

## Tier 3 — Enrichment / Verification

### Hunter.io
- **What:** email verification (syntax, MX, SMTP) and domain email pattern discovery.
- **ToS:** [Hunter ToS](https://hunter.io/terms). GDPR-compliant, EU-hosted option available.
- **Our use:** Phase 2 — verify top-value leads only.

### ZeroBounce
- **What:** alternative email verification.
- **ToS:** [ZeroBounce ToS](https://www.zerobounce.net/terms-of-use). GDPR-compliant.
- **Our use:** backup to Hunter.

### DNS / MX lookups
- **What:** direct MX record checks for deliverability pre-filtering.
- **ToS:** none — public DNS.
- **Our use:** free first-pass email verification in Phase 1 before spending on Hunter.

---

## Tier 4 — Compliant Scraping (last resort)

Only the **official website** of a discovered entity may be scraped, and only these pages:
- Homepage (`/`)
- `/contact`, `/contact-us`, `/kontakt`, `/contacto`, `/contatti`, `/nous-contacter`
- `/about`, `/about-us`, `/impressum` (required on DE sites — rich contact info), `/mentions-legales` (FR)
- `/legal`, `/privacy` (to detect opt-out language)

**Rules:**
- Must fetch and respect `robots.txt`.
- Must obey `Retry-After` headers.
- User-Agent identifies us: `AI-LeadGen-OS/0.1 (+https://combosoft.co.uk/bot)`.
- Per-domain rate limit: max 1 request / 2 seconds by default.
- No headless-browser bypass of anti-bot walls — if Cloudflare / PerimeterX blocks us, we stop.
- Payload stored in `raw_fetches` (JSONB) with retention per `compliance.md` §6.

---

## Banned Sources (do not integrate)

| Source | Reason |
|---|---|
| LinkedIn | ToS forbids scraping; `hiQ vs. LinkedIn` ended in LinkedIn's favor. Legal + technical risk. |
| Facebook / Instagram | ToS + GDPR concerns; behind login. |
| Twitter/X scraping | ToS forbids; API pricing unworkable. |
| Any site behind paywall / login | ToS violation by default. |
| Email harvester lists from dark-web sources | Obvious legal risk. |
| Data brokers with unclear GDPR lawful basis | Liability. |

---

## Review Process

Adding a source requires:
1. A PR updating this file with the new entry.
2. A link to the source's ToS (saved to `docs/tos-snapshots/` as a PDF snapshot).
3. Confirmation the source works for EU/UK geography.
4. Assessment of caching / retention constraints.
5. Sign-off by the data controller before deployment.
