import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the dashboard E2E suite.
 *
 * The tests assume a running Next.js dev server on PLAYWRIGHT_BASE_URL
 * (default http://localhost:3000) AND a running FastAPI backend that the
 * web app proxies to. Authenticated tests also need a seeded test user —
 * see e2e/auth.spec.ts for how it bootstraps via /auth/register.
 *
 * To run locally:
 *   1. Start the backend:    uv run uvicorn app.main:app --reload
 *   2. Start the web dev server: npm run dev
 *   3. Install browsers once: npx playwright install chromium
 *   4. Run tests:            npm run test:e2e
 */
export default defineConfig({
  testDir: "./e2e",
  // Fail fast in CI; locally let everything run for full picture.
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "github" : "list",

  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
    // Useful while debugging — capture both on failure.
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
