"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";

const MAX_BYTES = 1_000_000;

export default function BulkUploadPage() {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [budget, setBudget] = useState(5);
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function pickFile(next: File | null) {
    if (next && next.size > MAX_BYTES) {
      setError(`File is ${(next.size / 1_000_000).toFixed(2)} MB — max is 1 MB (~500 rows).`);
      setFile(null);
      return;
    }
    setError(null);
    setFile(next);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setSubmitting(true);
    setError(null);
    try {
      const job = await api.uploadBulkCsv(file, { budget_cap_usd: budget });
      router.push(`/jobs/${job.id}`);
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail);
      else setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
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

      <header className="space-y-1">
        <h2 className="text-xl font-medium">Bulk enrichment</h2>
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          Upload a CSV of up to 500 rows with <code>website</code> or{" "}
          <code>domain</code> columns (<code>name</code> optional). We&apos;ll
          enrich each row with email, phone, address, and quality score.
        </p>
      </header>

      <form onSubmit={handleSubmit} className="space-y-4">
        <label
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const dropped = e.dataTransfer.files?.[0];
            if (dropped) pickFile(dropped);
          }}
          className={
            "block cursor-pointer rounded-lg border-2 border-dashed p-8 text-center transition-colors " +
            (dragging
              ? "border-blue-500 bg-blue-50 dark:bg-blue-950"
              : "border-neutral-300 bg-white hover:border-neutral-400 dark:border-neutral-700 dark:bg-neutral-900 dark:hover:border-neutral-600")
          }
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,text/csv"
            className="hidden"
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
          />
          {file ? (
            <div className="space-y-1">
              <div className="font-medium">{file.name}</div>
              <div className="text-xs text-neutral-500">
                {(file.size / 1024).toFixed(1)} KB
              </div>
              <button
                type="button"
                onClick={(e) => {
                  e.preventDefault();
                  pickFile(null);
                  if (fileInputRef.current) fileInputRef.current.value = "";
                }}
                className="text-xs text-blue-600 hover:underline dark:text-blue-400"
              >
                Choose a different file
              </button>
            </div>
          ) : (
            <div className="space-y-1">
              <div className="text-sm font-medium">
                Drop a CSV here or click to browse
              </div>
              <div className="text-xs text-neutral-500">
                Up to 1 MB, ~500 rows max
              </div>
            </div>
          )}
        </label>

        <label className="block max-w-xs">
          <span className="mb-1 block text-sm font-medium">
            Budget cap (USD)
          </span>
          <input
            type="number"
            value={budget}
            min={0.1}
            max={100}
            step={0.1}
            onChange={(e) => setBudget(Number(e.target.value))}
            className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-neutral-700 dark:bg-neutral-950"
          />
          <span className="mt-1 block text-xs text-neutral-500">
            Job stops if enrichment cost exceeds this cap.
          </span>
        </label>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={submitting || !file}
            className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? "Uploading…" : "Start enrichment"}
          </button>
          {error && (
            <span className="text-sm text-red-700 dark:text-red-300">
              {error}
            </span>
          )}
        </div>
      </form>

      <details className="text-sm text-neutral-600 dark:text-neutral-400">
        <summary className="cursor-pointer font-medium">
          CSV format
        </summary>
        <div className="mt-2 space-y-2">
          <p>Header row required. Recognised column names (case-insensitive):</p>
          <ul className="ml-5 list-disc space-y-1">
            <li>
              <code>name</code> / <code>company</code> / <code>company_name</code> /{" "}
              <code>organization</code> — optional
            </li>
            <li>
              <code>website</code> / <code>url</code> / <code>homepage</code>
            </li>
            <li>
              <code>domain</code> / <code>hostname</code>
            </li>
          </ul>
          <p>Each row must have at least a website or a domain.</p>
          <pre className="mt-2 overflow-x-auto rounded bg-neutral-100 p-3 text-xs dark:bg-neutral-900">{`name,website
Acme Corp,https://acme.example
Foo Ltd,https://foo.example`}</pre>
        </div>
      </details>
    </div>
  );
}
