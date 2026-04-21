"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type ApiKey } from "@/lib/api";

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  // Plaintext key shown once after creation. Cleared on next action.
  const [freshKey, setFreshKey] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await api.listApiKeys();
      setKeys(res.items);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const res = await api.createApiKey(name.trim());
      setFreshKey(res.key);
      setName("");
      void refresh();
    } catch (err) {
      if (err instanceof ApiError) setCreateError(err.detail);
      else setCreateError(err instanceof Error ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(id: string) {
    if (!confirm("Revoke this API key? Any caller using it will start getting 401s.")) {
      return;
    }
    try {
      await api.revokeApiKey(id);
      void refresh();
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    }
  }

  async function copyKey(value: string) {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // Clipboard can fail in insecure contexts; user can still select + copy.
    }
  }

  return (
    <div className="space-y-8">
      <section className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
        <h2 className="mb-3 text-lg font-medium">New API key</h2>
        <p className="mb-4 text-sm text-neutral-600 dark:text-neutral-400">
          Send programmatic requests with{" "}
          <code className="rounded bg-neutral-100 px-1 py-0.5 text-xs dark:bg-neutral-800">
            X-API-Key: &lt;key&gt;
          </code>
          . Keys are shown once — store them securely.
        </p>
        <form onSubmit={handleCreate} className="flex flex-wrap items-end gap-3">
          <label className="block flex-1 min-w-[220px]">
            <span className="mb-1 block text-sm font-medium">Name</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. n8n-production"
              required
              minLength={1}
              maxLength={128}
              className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-neutral-700 dark:bg-neutral-950"
            />
          </label>
          <button
            type="submit"
            disabled={creating || !name.trim()}
            className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {creating ? "Creating…" : "Create key"}
          </button>
          {createError && (
            <span className="text-sm text-red-700 dark:text-red-300">{createError}</span>
          )}
        </form>
        {freshKey && (
          <div className="mt-4 rounded border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-700 dark:bg-amber-950">
            <div className="mb-2 font-medium text-amber-900 dark:text-amber-200">
              Copy this key now — it won&apos;t be shown again.
            </div>
            <div className="flex items-center gap-2">
              <code className="flex-1 break-all rounded bg-white px-2 py-1 font-mono text-xs text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100">
                {freshKey}
              </code>
              <button
                type="button"
                onClick={() => copyKey(freshKey)}
                className="rounded border border-amber-400 bg-white px-2 py-1 text-xs font-medium text-amber-900 hover:bg-amber-100 dark:border-amber-600 dark:bg-neutral-900 dark:text-amber-200 dark:hover:bg-neutral-800"
              >
                Copy
              </button>
              <button
                type="button"
                onClick={() => setFreshKey(null)}
                className="rounded border border-amber-400 bg-white px-2 py-1 text-xs font-medium text-amber-900 hover:bg-amber-100 dark:border-amber-600 dark:bg-neutral-900 dark:text-amber-200 dark:hover:bg-neutral-800"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-lg font-medium">Your keys</h2>
        {loadError && (
          <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
            Couldn&apos;t reach the API: {loadError}
          </div>
        )}
        {keys === null && !loadError && (
          <div className="text-sm text-neutral-500">Loading…</div>
        )}
        {keys !== null && keys.length === 0 && (
          <div className="text-sm text-neutral-500">No keys yet.</div>
        )}
        {keys !== null && keys.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-neutral-200 dark:border-neutral-800">
            <table className="min-w-full divide-y divide-neutral-200 text-sm dark:divide-neutral-800">
              <thead className="bg-neutral-100 text-left text-xs font-medium uppercase tracking-wide text-neutral-600 dark:bg-neutral-900 dark:text-neutral-300">
                <tr>
                  <th className="px-4 py-3">Name</th>
                  <th className="px-4 py-3">Prefix</th>
                  <th className="px-4 py-3">Created</th>
                  <th className="px-4 py-3">Last used</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-200 bg-white dark:divide-neutral-800 dark:bg-neutral-950">
                {keys.map((k) => (
                  <tr key={k.id}>
                    <td className="px-4 py-3 font-medium">{k.name}</td>
                    <td className="px-4 py-3 font-mono text-xs">{k.prefix}…</td>
                    <td className="px-4 py-3 text-neutral-500">
                      {formatRelative(k.created_at)}
                    </td>
                    <td className="px-4 py-3 text-neutral-500">
                      {k.last_used_at ? formatRelative(k.last_used_at) : "—"}
                    </td>
                    <td className="px-4 py-3">
                      {k.revoked_at ? (
                        <span className="rounded bg-red-100 px-2 py-0.5 text-xs font-medium text-red-800 dark:bg-red-900 dark:text-red-100">
                          revoked
                        </span>
                      ) : (
                        <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-900 dark:text-emerald-100">
                          active
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {!k.revoked_at && (
                        <button
                          type="button"
                          onClick={() => handleRevoke(k.id)}
                          className="text-sm font-medium text-red-600 hover:underline dark:text-red-400"
                        >
                          Revoke
                        </button>
                      )}
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
