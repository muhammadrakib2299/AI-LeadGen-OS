"use client";

import { useEffect, useState } from "react";
import {
  api,
  type CircuitSnapshot,
  type SystemOverall,
  type SystemStatusResponse,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

const DOT_COLOR: Record<SystemOverall, string> = {
  ok: "bg-emerald-500",
  degraded: "bg-amber-500",
  impaired: "bg-red-500",
};

const LABEL: Record<SystemOverall, string> = {
  ok: "All systems go",
  degraded: "One dependency recovering",
  impaired: "Dependency down — pipeline using fallbacks",
};

/**
 * Small status dot rendered in the nav. Polls /status every 30s while the
 * tab is visible; the tooltip lists each circuit breaker's state.
 */
export function SystemStatus() {
  const [status, setStatus] = useState<SystemStatusResponse | null>(null);
  const [open, setOpen] = useState(false);
  // Defer auth-aware rendering until after mount so SSR and the first
  // client render match (getToken() reads localStorage, which is only
  // defined on the client).
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    // Skip while unauthenticated — /status is gated, and polling it from
    // the login page would produce a noisy stream of 401s.
    if (!mounted || !getToken()) return;
    let cancelled = false;
    async function fetchOnce() {
      try {
        const res = await api.getSystemStatus();
        if (!cancelled) setStatus(res);
      } catch {
        // Keep prior state; the nav badge is not worth a blocking error.
      }
    }
    void fetchOnce();
    const interval = setInterval(fetchOnce, 30_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [mounted]);

  // Hide entirely when unauthenticated — less visual noise on the login page.
  if (!mounted || !getToken()) return null;

  if (!status) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-neutral-400">
        <span className="inline-block h-2 w-2 rounded-full bg-neutral-300" />
        checking…
      </span>
    );
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-xs text-neutral-600 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
        aria-label={LABEL[status.overall]}
      >
        <span
          className={`inline-block h-2 w-2 rounded-full ${DOT_COLOR[status.overall]}`}
        />
        {LABEL[status.overall]}
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-2 w-64 rounded-md border border-neutral-200 bg-white p-3 text-xs shadow-lg dark:border-neutral-800 dark:bg-neutral-900">
          <div className="mb-2 font-medium text-neutral-700 dark:text-neutral-200">
            Circuit breakers
          </div>
          <ul className="space-y-1">
            {status.circuits.map((c) => (
              <CircuitRow key={c.name} snapshot={c} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function CircuitRow({ snapshot }: { snapshot: CircuitSnapshot }) {
  const stateStyle: Record<CircuitSnapshot["state"], string> = {
    closed: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-100",
    half_open: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100",
    open: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-100",
  };
  return (
    <li className="flex items-center justify-between gap-2">
      <span className="font-mono text-neutral-600 dark:text-neutral-300">
        {snapshot.name}
      </span>
      <span className={`rounded px-1.5 py-0.5 ${stateStyle[snapshot.state]}`}>
        {snapshot.state}
      </span>
    </li>
  );
}
