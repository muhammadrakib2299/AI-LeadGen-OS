"use client";

import { useEffect, useState } from "react";
import { api, type ComplianceSettings } from "@/lib/api";
import { getToken } from "@/lib/auth";

/**
 * Small nav-bar badge that surfaces which jurisdiction the backend is
 * configured for and, when Compliant Mode is on, a clear "shield" badge
 * so the operator (and any observer) can see at a glance that the
 * pipeline is restricted to Tier-1 official APIs.
 */
export function ComplianceBadge() {
  const [settings, setSettings] = useState<ComplianceSettings | null>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted || !getToken()) return;
    let cancelled = false;
    api
      .getComplianceSettings()
      .then((s) => {
        if (!cancelled) setSettings(s);
      })
      .catch(() => {
        // Non-critical; skip.
      });
    return () => {
      cancelled = true;
    };
  }, [mounted]);

  if (!mounted || !getToken()) return null;
  if (!settings) return null;

  return (
    <span className="flex items-center gap-2 text-xs">
      <span className="text-neutral-500 dark:text-neutral-400">
        {settings.jurisdiction} · GDPR-compliant
      </span>
      {settings.compliant_mode && (
        <span
          className="rounded-full bg-emerald-100 px-2 py-0.5 font-medium text-emerald-800 dark:bg-emerald-900 dark:text-emerald-100"
          title="Only Tier-1 official APIs are used; crawler and Yelp are disabled."
        >
          Compliant Mode
        </span>
      )}
    </span>
  );
}
