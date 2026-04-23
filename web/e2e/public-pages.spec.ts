import { expect, test } from "@playwright/test";

// Smoke-checks that public pages render without an authenticated session.
// These don't need the FastAPI backend up — Next.js renders these as
// static / client routes.

test("homepage redirects unauthenticated visitors to /login", async ({ page }) => {
  await page.goto("/");
  // The dashboard page bails to /login when no token cookie exists.
  await expect(page).toHaveURL(/\/login/);
});

test("login page shows the sign-in form", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByLabel(/email/i)).toBeVisible();
  await expect(page.getByLabel(/password/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /sign in|log in/i })).toBeVisible();
});

test("pricing page lists at least one plan", async ({ page }) => {
  await page.goto("/pricing");
  // The marketing page lists at least one tier with a price marker.
  await expect(page.locator("text=/\\$/").first()).toBeVisible();
});
