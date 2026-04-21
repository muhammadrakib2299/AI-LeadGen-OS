"use client";

import { useEffect, useState } from "react";
import { ApiError, api, type BillingStatus } from "@/lib/api";
import { getToken } from "@/lib/auth";

const LABEL: Record<string, { text: string; cls: string }> = {
  free: {
    text: "Free",
    cls: "bg-neutral-200 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-200",
  },
  standard: {
    text: "Standard",
    cls: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-100",
  },
  past_due: {
    text: "Past due",
    cls: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100",
  },
  canceled: {
    text: "Canceled",
    cls: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-100",
  },
};

export function BillingBadge() {
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [upgrading, setUpgrading] = useState(false);

  useEffect(() => {
    if (!getToken()) return;
    let cancelled = false;
    api
      .getBillingStatus()
      .then((s) => {
        if (!cancelled) setStatus(s);
      })
      .catch(() => {
        // 503 before billing is wired in is expected; skip silently.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleUpgrade() {
    setUpgrading(true);
    try {
      const res = await api.createBillingCheckout();
      window.location.href = res.checkout_url;
    } catch (err) {
      if (err instanceof ApiError) alert(err.detail);
      else alert(err instanceof Error ? err.message : String(err));
    } finally {
      setUpgrading(false);
    }
  }

  if (typeof window !== "undefined" && !getToken()) return null;
  if (!status) return null;

  const label = LABEL[status.plan] ?? LABEL.free;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className={`rounded-full px-2 py-0.5 font-medium ${label.cls}`}>
        {label.text}
      </span>
      {(status.plan === "free" || status.plan === "canceled") && (
        <button
          type="button"
          onClick={handleUpgrade}
          disabled={upgrading}
          className="rounded border border-blue-600 px-2 py-0.5 font-medium text-blue-700 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-blue-500 dark:text-blue-300 dark:hover:bg-blue-950"
        >
          {upgrading ? "…" : "Upgrade"}
        </button>
      )}
    </div>
  );
}
