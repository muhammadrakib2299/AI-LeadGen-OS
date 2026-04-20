"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type BlacklistEntry } from "@/lib/api";

export default function BlacklistPage() {
  const [items, setItems] = useState<BlacklistEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);

  const [kind, setKind] = useState<"email" | "domain">("email");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await api.listBlacklist(search.trim() || undefined);
      setItems(res.items);
      setTotal(res.total);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }, [search]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!value.trim()) return;
    setSubmitting(true);
    setAddError(null);
    try {
      const payload: { email?: string; domain?: string; reason?: string } = {
        reason: reason.trim() || undefined,
      };
      if (kind === "email") payload.email = value.trim();
      else payload.domain = value.trim();
      await api.addBlacklistEntry(payload);
      setValue("");
      setReason("");
      void refresh();
    } catch (err) {
      if (err instanceof ApiError) setAddError(err.detail);
      else setAddError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRemove(id: string) {
    if (
      !window.confirm(
        "Remove this blacklist entry? Only do this for operator mistakes — never to reverse an opt-out.",
      )
    ) {
      return;
    }
    try {
      await api.deleteBlacklistEntry(id);
      void refresh();
    } catch {
      // best-effort; next refresh will reflect the truth
    }
  }

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h2 className="text-xl font-medium">Blacklist</h2>
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          Emails and domains we&apos;ll never contact. Checked before every
          entity is persisted. Opt-out requests from data subjects are added
          here automatically via <code>/privacy/opt-out</code>.
        </p>
      </header>

      <section className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
        <h3 className="mb-3 text-sm font-medium">Add entry</h3>
        <form onSubmit={handleAdd} className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="mb-1 block text-xs font-medium">Type</span>
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as "email" | "domain")}
              className="rounded border border-neutral-300 bg-white px-2 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
            >
              <option value="email">Email</option>
              <option value="domain">Domain</option>
            </select>
          </label>
          <label className="block flex-1 min-w-[240px]">
            <span className="mb-1 block text-xs font-medium">
              {kind === "email" ? "Email address" : "Domain"}
            </span>
            <input
              type={kind === "email" ? "email" : "text"}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={
                kind === "email" ? "person@example.com" : "example.com"
              }
              required
              className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
            />
          </label>
          <label className="block flex-1 min-w-[240px]">
            <span className="mb-1 block text-xs font-medium">
              Reason (optional)
            </span>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="GDPR Art. 17 request / operator error / …"
              maxLength={500}
              className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
            />
          </label>
          <button
            type="submit"
            disabled={submitting || !value.trim()}
            className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? "Adding…" : "Add to blacklist"}
          </button>
        </form>
        {addError && (
          <div className="mt-3 text-sm text-red-700 dark:text-red-300">
            {addError}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-baseline gap-3">
            <h3 className="text-sm font-medium">Entries</h3>
            <span className="text-xs text-neutral-500">{total} total</span>
          </div>
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter by substring…"
            className="w-64 rounded border border-neutral-300 bg-white px-3 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-950"
          />
        </div>

        {loadError && (
          <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
            {loadError}
          </div>
        )}

        {items.length === 0 && !loadError ? (
          <div className="rounded border border-neutral-200 bg-white p-5 text-sm text-neutral-500 dark:border-neutral-800 dark:bg-neutral-900">
            {search ? "No entries match the filter." : "Nothing blacklisted yet."}
          </div>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-neutral-200 dark:border-neutral-800">
            <table className="min-w-full divide-y divide-neutral-200 text-sm dark:divide-neutral-800">
              <thead className="bg-neutral-100 text-left text-xs font-medium uppercase tracking-wide text-neutral-600 dark:bg-neutral-900 dark:text-neutral-300">
                <tr>
                  <th className="px-4 py-3">Identifier</th>
                  <th className="px-4 py-3">Reason</th>
                  <th className="px-4 py-3">Added</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-200 bg-white dark:divide-neutral-800 dark:bg-neutral-950">
                {items.map((entry) => (
                  <tr key={entry.id}>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span
                          className={
                            "rounded px-1.5 py-0.5 text-xs font-medium " +
                            (entry.email
                              ? "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-100"
                              : "bg-teal-100 text-teal-800 dark:bg-teal-900 dark:text-teal-100")
                          }
                        >
                          {entry.email ? "email" : "domain"}
                        </span>
                        <span className="break-all font-mono text-xs">
                          {entry.email ?? entry.domain}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-neutral-600 dark:text-neutral-300">
                      {entry.reason ?? (
                        <span className="text-neutral-400">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-neutral-500">
                      {new Date(entry.created_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        type="button"
                        onClick={() => handleRemove(entry.id)}
                        className="text-xs text-red-700 hover:underline dark:text-red-300"
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
