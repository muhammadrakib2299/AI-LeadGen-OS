"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import {
  type AuthUser,
  clearToken,
  getStoredUser,
  getToken,
  setStoredUser,
} from "@/lib/auth";

const PUBLIC_PATHS = new Set<string>(["/login", "/pricing"]);

/**
 * Client-side auth gate. Blocks rendering until we know whether the user is
 * signed in, then either redirects to /login or renders children.
 *
 * Also exposes the current user + a sign-out button in a top-right strip via
 * the `header` slot so the layout doesn't need to duplicate that wiring.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const isPublic = PUBLIC_PATHS.has(pathname ?? "");

  const [user, setUser] = useState<AuthUser | null>(() => getStoredUser());
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (isPublic) {
      setChecked(true);
      return;
    }
    const token = getToken();
    if (!token) {
      router.replace(`/login?next=${encodeURIComponent(pathname ?? "/")}`);
      return;
    }
    // Verify token with backend — catches expired/revoked sessions.
    api
      .me()
      .then((u) => {
        setUser(u);
        setStoredUser(u);
        setChecked(true);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearToken();
          router.replace(`/login?next=${encodeURIComponent(pathname ?? "/")}`);
        } else {
          setChecked(true);
        }
      });
  }, [isPublic, pathname, router]);

  async function handleLogout() {
    try {
      await api.logout();
    } catch {
      // Best effort — even if the call fails, drop local state.
    }
    clearToken();
    setUser(null);
    router.replace("/login");
  }

  if (isPublic) return <>{children}</>;
  if (!checked) {
    return (
      <div className="p-6 text-sm text-neutral-500">Checking session…</div>
    );
  }

  return (
    <>
      <div className="mb-4 flex items-center justify-end gap-3 text-xs text-neutral-500 dark:text-neutral-400">
        {user && <span>{user.email}</span>}
        <button
          type="button"
          onClick={handleLogout}
          className="rounded border border-neutral-300 px-2 py-0.5 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          Sign out
        </button>
      </div>
      {children}
    </>
  );
}
