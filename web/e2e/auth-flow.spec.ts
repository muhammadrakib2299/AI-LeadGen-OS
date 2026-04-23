import { expect, test } from "@playwright/test";

/**
 * Auth + create-job golden path.
 *
 * Requires: backend on http://localhost:8000 (or PLAYWRIGHT_API_URL),
 * Postgres reachable, ANTHROPIC_API_KEY set on the backend.
 *
 * Bootstraps a fresh user via /auth/register so the test is hermetic
 * across runs — no shared seed user.
 */

const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";

function freshEmail(): string {
  return `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

test("register, log in, then land on the dashboard", async ({ page }) => {
  const email = freshEmail();
  const password = "playwright-test-password";

  // The login page has a register toggle; using it exercises the same
  // /auth/register endpoint a real visitor would.
  await page.goto("/login");
  await page.getByRole("button", { name: /sign up|register|create account/i }).click();
  await page.getByLabel(/email/i).fill(email);
  await page.getByLabel(/password/i).fill(password);
  await page.getByRole("button", { name: /sign up|create account|register/i }).click();

  // After register, the app stores the token and replaces history with /.
  await expect(page).toHaveURL(/^\/(\?.*)?$/, { timeout: 10_000 });
  await expect(page.getByRole("heading", { name: /dashboard/i })).toBeVisible();
});

test("submit a discovery job and see it in the recent jobs table", async ({ page, request }) => {
  // Register + log in via the API to skip UI noise. We only want the
  // create-job flow under test here.
  const email = freshEmail();
  const password = "playwright-test-password";
  const reg = await request.post(`${API_URL}/auth/register`, {
    data: { email, password },
  });
  expect(reg.ok()).toBeTruthy();
  const { access_token } = (await reg.json()) as { access_token: string };

  await page.addInitScript((token: string) => {
    localStorage.setItem("leadgen_session", token);
  }, access_token);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: /dashboard/i })).toBeVisible();

  const query = `e2e-restaurants-${Date.now()}`;
  await page.getByLabel(/query/i).fill(`${query} in Paris`);
  await page.getByRole("button", { name: /submit job/i }).click();

  // The form clears the input and the new row appears in the table.
  await expect(page.getByText(query, { exact: false })).toBeVisible({
    timeout: 10_000,
  });
});
