# Playwright E2E tests

Browser-driven tests that exercise the dashboard end-to-end. Backend
unit/integration tests (Python, pytest) cover the JSON contract — these
tests cover the React UI rendering on top of it.

## One-time setup

```bash
cd web
npm install
npx playwright install chromium
```

## Running

The tests assume both the backend and the web dev server are running.

```bash
# Terminal 1 — backend (from repo root)
uv run uvicorn app.main:app --reload

# Terminal 2 — web
cd web && npm run dev

# Terminal 3 — tests
cd web && npm run test:e2e
```

For interactive debugging:

```bash
npm run test:e2e:ui
```

## What's covered

- `public-pages.spec.ts` — homepage redirect, login form renders,
  pricing page renders. No backend required.
- `auth-flow.spec.ts` — register → land on dashboard, then submit a
  discovery job and watch it appear. Requires a running backend with
  Postgres and `ANTHROPIC_API_KEY` set.

## CI

Set `CI=1` to enable retries and the GitHub-flavored reporter. The
config also flips `forbidOnly` so a stray `test.only` fails the build.
