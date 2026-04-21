"use client";

import { useEffect, useState } from "react";
import { api, type JobDiagnostics } from "@/lib/api";

/**
 * "Why is this job slow?" panel on the job detail page.
 *
 * Reads /jobs/{id}/diagnostics — aggregates the raw_fetches audit log so the
 * operator can see at a glance whether Google was rate-limiting us, Yelp
 * was returning 5xx, or everything just worked and the run is genuinely
 * at its natural speed.
 */
export function JobDiagnosticsPanel({ jobId }: { jobId: string }) {
  const [diag, setDiag] = useState<JobDiagnostics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await api.getJobDiagnostics(jobId);
        if (!cancelled) setDiag(res);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    }
    void load();
  }, [jobId]);

  if (error) {
    return (
      <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
        Couldn&apos;t load diagnostics: {error}
      </div>
    );
  }
  if (!diag) {
    return <div className="text-sm text-neutral-500">Loading diagnostics…</div>;
  }

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
      <h2 className="mb-1 text-lg font-medium">Why this is taking what it takes</h2>
      <p className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">
        {diag.summary}
      </p>
      {diag.sources.length === 0 ? (
        <div className="text-sm text-neutral-500">No audit rows yet.</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-neutral-200 dark:border-neutral-800">
          <table className="min-w-full divide-y divide-neutral-200 text-sm dark:divide-neutral-800">
            <thead className="bg-neutral-100 text-left text-xs font-medium uppercase tracking-wide text-neutral-600 dark:bg-neutral-900 dark:text-neutral-300">
              <tr>
                <th className="px-3 py-2">Source</th>
                <th className="px-3 py-2 text-right">Calls</th>
                <th className="px-3 py-2 text-right">2xx</th>
                <th className="px-3 py-2 text-right">429</th>
                <th className="px-3 py-2 text-right">5xx</th>
                <th className="px-3 py-2 text-right">Avg ms</th>
                <th className="px-3 py-2">Note</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-200 bg-white dark:divide-neutral-800 dark:bg-neutral-950">
              {diag.sources.map((s) => (
                <tr key={s.source}>
                  <td className="px-3 py-2 font-mono text-xs">{s.source}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{s.calls}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-emerald-700 dark:text-emerald-300">
                    {s.success}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {s.rate_limited ? (
                      <span className="text-amber-700 dark:text-amber-300">
                        {s.rate_limited}
                      </span>
                    ) : (
                      <span className="text-neutral-400">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {s.server_errors ? (
                      <span className="text-red-700 dark:text-red-300">
                        {s.server_errors}
                      </span>
                    ) : (
                      <span className="text-neutral-400">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-neutral-500">
                    {s.avg_duration_ms ?? "—"}
                  </td>
                  <td className="px-3 py-2 text-xs text-neutral-600 dark:text-neutral-400">
                    {s.slow_reason ?? "ok"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
