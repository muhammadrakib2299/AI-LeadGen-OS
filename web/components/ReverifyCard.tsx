"use client";

import { useState } from "react";
import { ApiError, api, type ReverifyResponse } from "@/lib/api";

/**
 * Compact card for triggering a one-shot re-verification pass.
 *
 * Production deployments should wire `scripts/reverify_aged.py` into a
 * nightly cron; this button exists for smaller setups that don't yet have
 * a scheduler, and for operators who want to flush freshness on demand
 * after importing a large batch of old leads.
 */
export function ReverifyCard() {
  const [maxAge, setMaxAge] = useState(90);
  const [limit, setLimit] = useState(50);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ReverifyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleClick() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.runReverify({ max_age_days: maxAge, limit });
      setResult(res);
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail);
      else setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
      <h2 className="mb-1 text-lg font-medium">Re-verify aged records</h2>
      <p className="mb-4 text-sm text-neutral-600 dark:text-neutral-400">
        Re-check website liveness, email MX, and phone format for the oldest
        entities. Runs inline — for nightly batches use{" "}
        <code className="rounded bg-neutral-100 px-1 py-0.5 text-xs dark:bg-neutral-800">
          scripts/reverify_aged.py
        </code>
        .
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Older than (days)</span>
          <input
            type="number"
            value={maxAge}
            min={1}
            max={365}
            onChange={(e) => setMaxAge(Number(e.target.value))}
            className="w-28 rounded border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-neutral-700 dark:bg-neutral-950"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Batch size</span>
          <input
            type="number"
            value={limit}
            min={1}
            max={500}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="w-28 rounded border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-neutral-700 dark:bg-neutral-950"
          />
        </label>
        <button
          type="button"
          onClick={handleClick}
          disabled={running}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {running ? "Re-verifying…" : "Run now"}
        </button>
      </div>
      {error && (
        <div className="mt-3 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </div>
      )}
      {result && (
        <div className="mt-3 rounded border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-100">
          Scanned {result.scanned}: {result.websites_dead} dead websites,{" "}
          {result.emails_invalid} invalid emails, {result.phones_invalid} invalid
          phones
          {result.errors.length > 0 && `, ${result.errors.length} errors`}.
        </div>
      )}
    </section>
  );
}
