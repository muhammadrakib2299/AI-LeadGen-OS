"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type ReviewEntity } from "@/lib/api";

const PAGE_SIZE = 20;

export default function ReviewQueuePage() {
  const [items, setItems] = useState<ReviewEntity[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Per-entity action state so buttons don't disable each other.
  const [pending, setPending] = useState<Record<string, "approve" | "reject">>({});
  const [rowError, setRowError] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    try {
      const res = await api.listReviewQueue({ limit: PAGE_SIZE, offset });
      setItems(res.items);
      setTotal(res.total);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }, [offset]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function decide(id: string, action: "approve" | "reject") {
    setPending((p) => ({ ...p, [id]: action }));
    setRowError((e) => {
      const { [id]: _, ...rest } = e;
      return rest;
    });
    try {
      if (action === "approve") await api.approveEntity(id);
      else await api.rejectEntity(id);
      // Optimistic removal — the decision moves the row out of the queue.
      setItems((rows) => rows.filter((r) => r.id !== id));
      setTotal((t) => Math.max(0, t - 1));
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : String(err);
      setRowError((e) => ({ ...e, [id]: msg }));
    } finally {
      setPending((p) => {
        const { [id]: _, ...rest } = p;
        return rest;
      });
    }
  }

  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2 className="text-xl font-medium">Review queue</h2>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Low-confidence entities that need a human call before export.
          </p>
        </div>
        <span className="text-xs text-neutral-500">
          {total} pending
          {total > 0 && ` · showing ${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`}
        </span>
      </header>

      {loadError && (
        <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          {loadError}
        </div>
      )}

      {!loadError && total === 0 && (
        <div className="rounded border border-neutral-200 bg-white p-5 text-sm text-neutral-500 dark:border-neutral-800 dark:bg-neutral-900">
          Nothing waiting. New low-confidence entities will appear here as jobs
          run.
        </div>
      )}

      {items.length > 0 && (
        <ul className="space-y-3">
          {items.map((e) => (
            <li
              key={e.id}
              className="rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{e.name}</span>
                    {e.quality_score !== null && (
                      <span
                        className={
                          "rounded px-1.5 py-0.5 text-xs font-medium tabular-nums " +
                          qualityStyle(e.quality_score)
                        }
                      >
                        {e.quality_score}
                      </span>
                    )}
                    {e.category && (
                      <span className="text-xs text-neutral-500">
                        {e.category}
                      </span>
                    )}
                  </div>
                  {e.website && (
                    <a
                      href={e.website}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block truncate text-sm text-blue-600 hover:underline dark:text-blue-400"
                      title={e.website}
                    >
                      {e.website}
                    </a>
                  )}
                  <div className="text-xs text-neutral-500">
                    from job:{" "}
                    <Link
                      href={`/jobs/${e.job_id}`}
                      className="text-blue-600 hover:underline dark:text-blue-400"
                    >
                      {e.job_query}
                    </Link>
                  </div>
                </div>
                <div className="flex shrink-0 gap-2">
                  <button
                    type="button"
                    onClick={() => decide(e.id, "reject")}
                    disabled={!!pending[e.id]}
                    className="rounded border border-red-300 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950"
                  >
                    {pending[e.id] === "reject" ? "Rejecting…" : "Reject"}
                  </button>
                  <button
                    type="button"
                    onClick={() => decide(e.id, "approve")}
                    disabled={!!pending[e.id]}
                    className="rounded bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {pending[e.id] === "approve" ? "Approving…" : "Approve"}
                  </button>
                </div>
              </div>

              <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
                <FieldRow label="Email" value={e.email} source={e.field_sources?.email?.source} />
                <FieldRow label="Phone" value={e.phone} source={e.field_sources?.phone?.source} />
                <FieldRow
                  label="Location"
                  value={[e.city, e.country].filter(Boolean).join(", ") || null}
                />
                <FieldRow label="Address" value={e.address} />
              </dl>

              {rowError[e.id] && (
                <div className="mt-2 text-sm text-red-700 dark:text-red-300">
                  {rowError[e.id]}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-end gap-2 text-sm">
          <button
            type="button"
            disabled={!hasPrev}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            className="rounded border border-neutral-300 px-3 py-1 disabled:cursor-not-allowed disabled:opacity-50 dark:border-neutral-700"
          >
            Previous
          </button>
          <button
            type="button"
            disabled={!hasNext}
            onClick={() => setOffset(offset + PAGE_SIZE)}
            className="rounded border border-neutral-300 px-3 py-1 disabled:cursor-not-allowed disabled:opacity-50 dark:border-neutral-700"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}

function FieldRow({
  label,
  value,
  source,
}: {
  label: string;
  value: string | null;
  source?: string;
}) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="w-20 shrink-0 text-xs uppercase tracking-wide text-neutral-500">
        {label}
      </dt>
      <dd className="min-w-0 truncate">
        {value ? (
          <span>{value}</span>
        ) : (
          <span className="text-neutral-400">—</span>
        )}
        {value && source && (
          <span className="ml-2 text-xs text-neutral-500">({source})</span>
        )}
      </dd>
    </div>
  );
}

function qualityStyle(score: number): string {
  if (score >= 70) return "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-100";
  if (score >= 40) return "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100";
  return "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-100";
}
