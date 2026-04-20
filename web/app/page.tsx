"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  api,
  type Job,
  type JobStatus,
  type SearchTemplate,
} from "@/lib/api";

const TERMINAL_STATUSES: ReadonlySet<JobStatus> = new Set([
  "succeeded",
  "failed",
  "rejected",
  "budget_exceeded",
]);

const STATUS_STYLE: Record<JobStatus, string> = {
  pending: "bg-neutral-200 text-neutral-800 dark:bg-neutral-800 dark:text-neutral-200",
  running: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-100",
  succeeded: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-100",
  failed: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-100",
  rejected: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100",
  budget_exceeded: "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-100",
};

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await api.listJobs(25);
      setJobs(res.items);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Poll while anything is in flight. Backs off to 10s when everything's done.
  const anyRunning = useMemo(
    () => jobs?.some((j) => !TERMINAL_STATUSES.has(j.status)) ?? false,
    [jobs],
  );
  useEffect(() => {
    const interval = anyRunning ? 2000 : 10_000;
    const id = setInterval(refresh, interval);
    return () => clearInterval(id);
  }, [anyRunning, refresh]);

  return (
    <div className="space-y-8">
      <CreateJobForm onCreated={refresh} />
      <section>
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-lg font-medium">Recent jobs</h2>
          {anyRunning && (
            <span className="text-xs text-blue-600 dark:text-blue-400">
              refreshing every 2s…
            </span>
          )}
        </div>
        {loadError && (
          <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
            Couldn&apos;t reach the API: {loadError}
          </div>
        )}
        {jobs === null && !loadError && (
          <div className="text-sm text-neutral-500">Loading…</div>
        )}
        {jobs !== null && jobs.length === 0 && (
          <div className="text-sm text-neutral-500">
            No jobs yet. Submit one above.
          </div>
        )}
        {jobs !== null && jobs.length > 0 && <JobsTable jobs={jobs} />}
      </section>
    </div>
  );
}

function CreateJobForm({ onCreated }: { onCreated: () => void }) {
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(50);
  const [budget, setBudget] = useState(5);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [justCreated, setJustCreated] = useState<string | null>(null);

  const [templates, setTemplates] = useState<SearchTemplate[] | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);

  const refreshTemplates = useCallback(async () => {
    try {
      const res = await api.listTemplates();
      setTemplates(res.items);
    } catch {
      // Non-critical; the form still works. Keep templates null on error.
    }
  }, []);

  useEffect(() => {
    void refreshTemplates();
  }, [refreshTemplates]);

  function applyTemplate(t: SearchTemplate) {
    setQuery(t.query);
    setLimit(t.default_limit);
    setBudget(Number(t.default_budget_cap_usd));
    setError(null);
    setJustCreated(null);
  }

  async function handleSaveTemplate(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim() || !saveName.trim()) return;
    setSaveError(null);
    try {
      await api.createTemplate({
        name: saveName.trim(),
        query: query.trim(),
        default_limit: limit,
        default_budget_cap_usd: budget,
      });
      setSaveName("");
      setSaveOpen(false);
      void refreshTemplates();
    } catch (err) {
      if (err instanceof ApiError) setSaveError(err.detail);
      else setSaveError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleDeleteTemplate(id: string) {
    try {
      await api.deleteTemplate(id);
      void refreshTemplates();
    } catch {
      // Silent — a stale template is not worth a blocking dialog.
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const job = await api.createDiscoveryJob({
        query: query.trim(),
        limit,
        budget_cap_usd: budget,
      });
      setJustCreated(job.id);
      setQuery("");
      onCreated();
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail);
      else setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
      <h2 className="mb-3 text-lg font-medium">New discovery job</h2>
      {templates && templates.length > 0 && (
        <div className="mb-4 space-y-2">
          <div className="text-xs font-medium uppercase tracking-wide text-neutral-500">
            Saved templates
          </div>
          <ul className="flex flex-wrap gap-2">
            {templates.map((t) => (
              <li
                key={t.id}
                className="group flex items-center gap-1 rounded-full border border-neutral-300 bg-neutral-50 pl-3 pr-1 py-0.5 text-xs dark:border-neutral-700 dark:bg-neutral-950"
              >
                <button
                  type="button"
                  onClick={() => applyTemplate(t)}
                  className="hover:text-blue-600 dark:hover:text-blue-400"
                  title={t.query}
                >
                  {t.name}
                </button>
                <button
                  type="button"
                  onClick={() => handleDeleteTemplate(t.id)}
                  aria-label={`Delete template ${t.name}`}
                  className="ml-1 rounded-full px-1.5 text-neutral-400 hover:bg-red-100 hover:text-red-700 dark:hover:bg-red-950 dark:hover:text-red-300"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <form onSubmit={handleSubmit} className="space-y-3">
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Query</span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="restaurants in Paris"
            required
            minLength={3}
            maxLength={500}
            className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-neutral-700 dark:bg-neutral-950"
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="mb-1 block text-sm font-medium">Limit</span>
            <input
              type="number"
              value={limit}
              min={1}
              max={1000}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-neutral-700 dark:bg-neutral-950"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium">Budget cap (USD)</span>
            <input
              type="number"
              value={budget}
              min={0.1}
              max={100}
              step={0.1}
              onChange={(e) => setBudget(Number(e.target.value))}
              className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-neutral-700 dark:bg-neutral-950"
            />
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={submitting || !query.trim()}
            className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? "Submitting…" : "Submit job"}
          </button>
          <button
            type="button"
            onClick={() => {
              setSaveOpen((open) => !open);
              setSaveError(null);
            }}
            disabled={!query.trim()}
            className="text-sm text-blue-600 hover:underline disabled:cursor-not-allowed disabled:opacity-50 dark:text-blue-400"
          >
            {saveOpen ? "Cancel" : "Save as template"}
          </button>
          {error && (
            <span className="text-sm text-red-700 dark:text-red-300">{error}</span>
          )}
          {justCreated && !error && (
            <span className="text-xs text-neutral-500">
              Created job {justCreated.slice(0, 8)}…
            </span>
          )}
        </div>
      </form>
      {saveOpen && (
        <form
          onSubmit={handleSaveTemplate}
          className="mt-3 flex flex-wrap items-center gap-2 border-t border-neutral-200 pt-3 dark:border-neutral-800"
        >
          <input
            type="text"
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
            placeholder="Template name (e.g. EU SaaS startups)"
            required
            minLength={1}
            maxLength={128}
            className="flex-1 min-w-[220px] rounded border border-neutral-300 bg-white px-3 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-950"
          />
          <button
            type="submit"
            disabled={!saveName.trim() || !query.trim()}
            className="rounded border border-blue-600 px-3 py-1.5 text-sm font-medium text-blue-700 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-blue-500 dark:text-blue-300 dark:hover:bg-blue-950"
          >
            Save
          </button>
          {saveError && (
            <span className="text-sm text-red-700 dark:text-red-300">
              {saveError}
            </span>
          )}
        </form>
      )}
    </section>
  );
}

function JobsTable({ jobs }: { jobs: Job[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-200 dark:border-neutral-800">
      <table className="min-w-full divide-y divide-neutral-200 text-sm dark:divide-neutral-800">
        <thead className="bg-neutral-100 text-left text-xs font-medium uppercase tracking-wide text-neutral-600 dark:bg-neutral-900 dark:text-neutral-300">
          <tr>
            <th className="px-4 py-3">Query</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Progress</th>
            <th className="px-4 py-3">Entities</th>
            <th className="px-4 py-3">Cost</th>
            <th className="px-4 py-3">Created</th>
            <th className="px-4 py-3"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-neutral-200 bg-white dark:divide-neutral-800 dark:bg-neutral-950">
          {jobs.map((job) => (
            <tr key={job.id}>
              <td className="px-4 py-3">
                <Link
                  href={`/jobs/${job.id}`}
                  className="block truncate font-medium text-blue-600 hover:underline dark:text-blue-400"
                  title={job.query_raw}
                >
                  {job.query_raw}
                </Link>
                {job.error && (
                  <div
                    className="mt-1 truncate text-xs text-red-700 dark:text-red-300"
                    title={job.error}
                  >
                    {job.error}
                  </div>
                )}
              </td>
              <td className="px-4 py-3">
                <span
                  className={
                    "inline-block rounded px-2 py-0.5 text-xs font-medium " +
                    STATUS_STYLE[job.status]
                  }
                >
                  {job.status}
                </span>
              </td>
              <td className="px-4 py-3 text-neutral-600 dark:text-neutral-300">
                {job.progress_percent === null ? (
                  <span className="text-neutral-400">—</span>
                ) : (
                  <ProgressBar percent={job.progress_percent} />
                )}
              </td>
              <td className="px-4 py-3 tabular-nums">{job.entity_count}</td>
              <td className="px-4 py-3 tabular-nums">
                ${Number(job.cost_usd).toFixed(3)}
              </td>
              <td className="px-4 py-3 text-neutral-500">
                {formatRelative(job.created_at)}
              </td>
              <td className="px-4 py-3 text-right">
                {job.status === "succeeded" && (
                  <a
                    href={api.exportCsvUrl(job.id)}
                    className="text-sm font-medium text-blue-600 hover:underline dark:text-blue-400"
                  >
                    Export CSV
                  </a>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProgressBar({ percent }: { percent: number }) {
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
        <div
          className="h-full bg-blue-500 transition-all"
          style={{ width: `${clamped}%` }}
        />
      </div>
      <span className="tabular-nums text-xs">{clamped.toFixed(0)}%</span>
    </div>
  );
}

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diff = Math.max(0, now - then);
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
