"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AlertCircle, Loader2, Shield } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

  const isLogin = mode === "login";

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-b from-background to-muted/40 p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm">
            <Shield className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">
              AI LeadGen OS
            </h1>
            <p className="text-sm text-muted-foreground">
              Compliant lead generation for EU/UK B2B
            </p>
          </div>
        </div>

        <Card>
          <CardHeader className="pb-4">
            <CardTitle>{isLogin ? "Sign in" : "Create account"}</CardTitle>
            <CardDescription>
              {isLogin
                ? "Welcome back. Sign in with your email and password."
                : "Set up a new operator account."}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                  placeholder="you@company.com"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={isLogin ? 1 : 8}
                  autoComplete={isLogin ? "current-password" : "new-password"}
                />
                {!isLogin && (
                  <p className="text-xs text-muted-foreground">
                    Minimum 8 characters.
                  </p>
                )}
              </div>

              {error && (
                <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <div>{error}</div>
                </div>
              )}

              <Button
                type="submit"
                className="w-full"
                disabled={submitting || !email || !password}
              >
                {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
                {submitting
                  ? isLogin
                    ? "Signing in…"
                    : "Creating account…"
                  : isLogin
                    ? "Sign in"
                    : "Create account"}
              </Button>
            </form>

            <div className="pt-4 text-center text-sm text-muted-foreground">
              {isLogin ? (
                <>
                  No account?{" "}
                  <button
                    type="button"
                    onClick={() => {
                      setMode("register");
                      setError(null);
                    }}
                    className="font-medium text-primary hover:underline"
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
                    className="font-medium text-primary hover:underline"
                  >
                    Sign in
                  </button>
                </>
              )}
            </div>
          </CardContent>
        </Card>

        <p className="text-center text-xs text-muted-foreground">
          By continuing you agree to the{" "}
          <a
            href="/pricing"
            className="underline-offset-2 hover:underline"
          >
            terms and compliance policy
          </a>
          .
        </p>
      </div>
    </div>
  );
}
