"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { getToken, setStoredUser, setToken } from "@/lib/auth";

type Mode = "login" | "register";

export default function LoginPage() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") ?? "/";

  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    // Already signed in → skip the form.
    if (getToken()) router.replace(next);
  }, [router, next]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const res =
        mode === "login"
          ? await api.login({ email, password })
          : await api.register({ email, password });
      setToken(res.access_token);
      setStoredUser(res.user);
      router.replace(next);
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail);
      else setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto mt-16 max-w-sm">
      <h1 className="mb-1 text-2xl font-semibold tracking-tight">
        {mode === "login" ? "Sign in" : "Create account"}
      </h1>
      <p className="mb-6 text-sm text-neutral-600 dark:text-neutral-400">
        AI LeadGen OS — operator dashboard
      </p>
      <form
        onSubmit={handleSubmit}
        className="space-y-3 rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900"
      >
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
            className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-neutral-700 dark:bg-neutral-950"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={mode === "register" ? 8 : 1}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-neutral-700 dark:bg-neutral-950"
          />
          {mode === "register" && (
            <span className="mt-1 block text-xs text-neutral-500">
              Minimum 8 characters.
            </span>
          )}
        </label>
        {error && (
          <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
            {error}
          </div>
        )}
        <button
          type="submit"
          disabled={submitting || !email || !password}
          className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {submitting
            ? "…"
            : mode === "login"
              ? "Sign in"
              : "Create account"}
        </button>
        <div className="pt-1 text-center text-xs text-neutral-600 dark:text-neutral-400">
          {mode === "login" ? (
            <>
              No account?{" "}
              <button
                type="button"
                onClick={() => {
                  setMode("register");
                  setError(null);
                }}
                className="text-blue-600 hover:underline dark:text-blue-400"
              >
                Create one
              </button>
            </>
          ) : (
            <>
              Already have an account?{" "}
              <button
                type="button"
                onClick={() => {
                  setMode("login");
                  setError(null);
                }}
                className="text-blue-600 hover:underline dark:text-blue-400"
              >
                Sign in
              </button>
            </>
          )}
        </div>
      </form>
    </div>
  );
}
