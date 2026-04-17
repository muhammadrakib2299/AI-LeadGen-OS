/**
 * Thin client for the FastAPI backend. All endpoints live under NEXT_PUBLIC_API_BASE_URL.
 *
 * Errors bubble up as `ApiError` so callers can render the backend's `detail`
 * message instead of a generic "Something went wrong."
 */

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type JobStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "rejected"
  | "budget_exceeded";

export interface Job {
  id: string;
  status: JobStatus;
  query_raw: string;
  query_validated: Record<string, unknown> | null;
  limit: number;
  budget_cap_usd: number;
  cost_usd: number;
  error: string | null;
  entity_count: number;
  places_discovered: number;
  places_processed: number;
  progress_percent: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobListResponse {
  items: Job[];
  total: number;
  limit: number;
  offset: number;
}

export interface CreateDiscoveryJobRequest {
  query: string;
  limit?: number;
  budget_cap_usd?: number;
  idempotency_key?: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail)) detail = JSON.stringify(body.detail);
    } catch {
      // fall through; status-text is fine
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listJobs: (limit = 25): Promise<JobListResponse> =>
    request<JobListResponse>(`/jobs?limit=${limit}`),

  getJob: (id: string): Promise<Job> => request<Job>(`/jobs/${id}`),

  createDiscoveryJob: (payload: CreateDiscoveryJobRequest): Promise<Job> =>
    request<Job>("/jobs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  exportCsvUrl: (id: string): string =>
    `${API_BASE}/jobs/${id}/export.csv`,
};
