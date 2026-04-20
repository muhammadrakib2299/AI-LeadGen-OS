"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import {
  ApiError,
  api,
  type Job,
  type JobEntity,
  type JobStatus,
} from "@/lib/api";
import { FreshnessBadge } from "@/components/FreshnessBadge";

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

const REVIEW_STYLE: Record<string, string> = {
  approved: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-100",
  review: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100",
  rejected: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-100",
  duplicate: "bg-neutral-200 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300",
  pending: "bg-neutral-200 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300",
};

const PAGE_SIZE = 25;

export default function JobDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [job, setJob] = useState<Job | null>(null);
  const [entities, setEntities] = useState<JobEntity[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [reviewFilter, setReviewFilter] = useState<string>("");
  const [includeDuplicates, setIncludeDuplicates] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [jobRes, entitiesRes] = await Promise.all([
        api.getJob(id),
        api.listJobEntities(id, {
          limit: PAGE_SIZE,
          offset,
          review_status: reviewFilter || undefined,
          include_duplicates: includeDuplicates,
        }),
      ]);
      setJob(jobRes);
      setEntities(entitiesRes.items);
      setTotal(entitiesRes.total);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail);
      else setError(err instanceof Error ? err.message : String(err));
    }
  }, [id, offset, reviewFilter, includeDuplicates]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const inFlight = job !== null && !TERMINAL_STATUSES.has(job.status);
  useEffect(() => {
    if (!inFlight) return;
    const interval = setInterval(refresh, 2000);
    return () => clearInterval(interval);
  }, [inFlight, refresh]);

  if (error && job === null) {
    return (
      <div className="space-y-4">
        <Link
          href="/"
          className="text-sm text-blue-600 hover:underline dark:text-blue-400"
        >
          ← Back to jobs
        </Link>
        <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </div>
      </div>
    );
  }

  if (job === null) {
    return <div className="text-sm text-neutral-500">Loading…</div>;
  }

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/"
          className="text-sm text-blue-600 hover:underline dark:text-blue-400"
        >
          ← Back to jobs
        </Link>
      </div>

      <JobHeader job={job} />

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-baseline gap-3">
            <h2 className="text-lg font-medium">Entities</h2>
            <span className="text-xs text-neutral-500">
              {total} total{total > 0 && ` · showing ${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`}
            </span>
            {inFlight && (
              <span className="text-xs text-blue-600 dark:text-blue-400">
                refreshing every 2s…
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 text-sm">
            <label className="flex items-center gap-2">
              <span className="text-neutral-600 dark:text-neutral-300">Review</span>
              <select
                value={reviewFilter}
                onChange={(e) => {
                  setReviewFilter(e.target.value);
                  setOffset(0);
                }}
                className="rounded border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-950"
              >
                <option value="">all</option>
                <option value="approved">approved</option>
                <option value="review">review</option>
                <option value="rejected">rejected</option>
                <option value="pending">pending</option>
              </select>
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={includeDuplicates}
                onChange={(e) => {
                  setIncludeDuplicates(e.target.checked);
                  setOffset(0);
                }}
              />
              <span className="text-neutral-600 dark:text-neutral-300">
                include duplicates
              </span>
            </label>
          </div>
        </div>

        {error && (
          <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
            {error}
          </div>
        )}

        {entities.length === 0 ? (
          <div className="rounded border border-neutral-200 bg-white p-5 text-sm text-neutral-500 dark:border-neutral-800 dark:bg-neutral-900">
            {inFlight
              ? "No entities yet — pipeline is still running."
              : "No entities match the current filter."}
          </div>
        ) : (
          <EntitiesTable entities={entities} />
        )}

        {total > PAGE_SIZE && (
          <Pagination
            offset={offset}
            total={total}
            onChange={setOffset}
            pageSize={PAGE_SIZE}
          />
        )}
      </section>
    </div>
  );
}

function JobHeader({ job }: { job: Job }) {
  return (
    <section className="space-y-3 rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex items-center gap-2">
            <span
              className={
                "inline-block rounded px-2 py-0.5 text-xs font-medium " +
                STATUS_STYLE[job.status]
              }
            >
              {job.status}
            </span>
            <span className="text-xs text-neutral-500">
              {formatRelative(job.created_at)}
            </span>
          </div>
          <h2
            className="break-words text-xl font-medium"
            title={job.query_raw}
          >
            {job.query_raw}
          </h2>
          {job.error && (
            <div className="text-sm text-red-700 dark:text-red-300">{job.error}</div>
          )}
        </div>
        {job.status === "succeeded" && (
          <a
            href={api.exportCsvUrl(job.id)}
            className="shrink-0 rounded bg-blue-600 px-3 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
          >
            Export CSV
          </a>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
        <Field label="Entities" value={String(job.entity_count)} />
        <Field
          label="Cost"
          value={`$${Number(job.cost_usd).toFixed(3)} / $${Number(
            job.budget_cap_usd,
          ).toFixed(2)}`}
        />
        <Field label="Limit" value={String(job.limit)} />
        <Field
          label="Progress"
          value={
            job.progress_percent === null
              ? "—"
              : `${job.places_processed} / ${job.places_discovered} (${job.progress_percent.toFixed(0)}%)`
          }
        />
      </dl>
    </section>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-neutral-500">
        {label}
      </dt>
      <dd className="tabular-nums text-neutral-900 dark:text-neutral-100">
        {value}
      </dd>
    </div>
  );
}

function EntitiesTable({ entities }: { entities: JobEntity[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-200 dark:border-neutral-800">
      <table className="min-w-full divide-y divide-neutral-200 text-sm dark:divide-neutral-800">
        <thead className="bg-neutral-100 text-left text-xs font-medium uppercase tracking-wide text-neutral-600 dark:bg-neutral-900 dark:text-neutral-300">
          <tr>
            <th className="px-4 py-3">Name</th>
            <th className="px-4 py-3">Contact</th>
            <th className="px-4 py-3">Location</th>
            <th className="px-4 py-3">Quality</th>
            <th className="px-4 py-3">Review</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-neutral-200 bg-white dark:divide-neutral-800 dark:bg-neutral-950">
          {entities.map((e) => (
            <tr key={e.id} className="align-top">
              <td className="px-4 py-3">
                <div className="font-medium">{e.name}</div>
                {e.website && (
                  <div className="flex items-center gap-1">
                    <a
                      href={e.website}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block truncate text-xs text-blue-600 hover:underline dark:text-blue-400"
                      title={e.website}
                    >
                      {e.website}
                    </a>
                    <FreshnessBadge fieldSources={e.field_sources} field="website" />
                  </div>
                )}
                {e.category && (
                  <div className="text-xs text-neutral-500">{e.category}</div>
                )}
              </td>
              <td className="px-4 py-3">
                {e.email ? (
                  <div className="flex items-center gap-1">
                    <a
                      href={`mailto:${e.email}`}
                      className="block truncate text-blue-600 hover:underline dark:text-blue-400"
                      title={e.email}
                    >
                      {e.email}
                    </a>
                    <FreshnessBadge fieldSources={e.field_sources} field="email" />
                  </div>
                ) : (
                  <span className="text-neutral-400">—</span>
                )}
                {e.phone && (
                  <div className="flex items-center gap-1 text-xs text-neutral-500">
                    <span>{e.phone}</span>
                    <FreshnessBadge fieldSources={e.field_sources} field="phone" />
                  </div>
                )}
              </td>
              <td className="px-4 py-3 text-neutral-700 dark:text-neutral-200">
                {e.city || e.country ? (
                  <div>
                    {[e.city, e.country].filter(Boolean).join(", ")}
                  </div>
                ) : (
                  <span className="text-neutral-400">—</span>
                )}
                {e.address && (
                  <div
                    className="truncate text-xs text-neutral-500"
                    title={e.address}
                  >
                    {e.address}
                  </div>
                )}
              </td>
              <td className="px-4 py-3 tabular-nums">
                {e.quality_score ?? "—"}
              </td>
              <td className="px-4 py-3">
                <span
                  className={
                    "inline-block rounded px-2 py-0.5 text-xs font-medium " +
                    (REVIEW_STYLE[e.review_status] ?? REVIEW_STYLE.pending)
                  }
                >
                  {e.review_status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Pagination({
  offset,
  total,
  pageSize,
  onChange,
}: {
  offset: number;
  total: number;
  pageSize: number;
  onChange: (offset: number) => void;
}) {
  const hasPrev = offset > 0;
  const hasNext = offset + pageSize < total;
  return (
    <div className="flex items-center justify-end gap-2 text-sm">
      <button
        type="button"
        disabled={!hasPrev}
        onClick={() => onChange(Math.max(0, offset - pageSize))}
        className="rounded border border-neutral-300 px-3 py-1 disabled:cursor-not-allowed disabled:opacity-50 dark:border-neutral-700"
      >
        Previous
      </button>
      <button
        type="button"
        disabled={!hasNext}
        onClick={() => onChange(offset + pageSize)}
        className="rounded border border-neutral-300 px-3 py-1 disabled:cursor-not-allowed disabled:opacity-50 dark:border-neutral-700"
      >
        Next
      </button>
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

