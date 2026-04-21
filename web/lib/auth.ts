/**
 * Lightweight client-side session store.
 *
 * We use a Bearer token in localStorage rather than an httpOnly cookie because
 * the frontend (localhost:3000) and API (localhost:8000) are on different
 * origins during development, and cross-site cookies require SameSite=None
 * over HTTPS — not viable for local dev. XSS is the trade-off; React escapes
 * by default and we don't render user-supplied HTML, so the risk is low for
 * a solo-operator tool. Revisit when we add multi-tenancy (Phase 5).
 */

const TOKEN_KEY = "leadgen_token";
const USER_KEY = "leadgen_user";

export interface AuthUser {
  id: string;
  email: string;
  is_active: boolean;
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
}

export function getStoredUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthUser;
  } catch {
    return null;
  }
}

export function setStoredUser(user: AuthUser): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
}
