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

export interface JobEntity {
  id: string;
  name: string;
  domain: string | null;
  website: string | null;
  email: string | null;
  phone: string | null;
  address: string | null;
  city: string | null;
  country: string | null;
  category: string | null;
  socials: Record<string, string> | null;
  quality_score: number | null;
  review_status: string;
  field_sources: Record<string, { source?: string; confidence?: number; fetched_at?: string }>;
  created_at: string;
}

export interface JobEntityListResponse {
  items: JobEntity[];
  total: number;
  limit: number;
  offset: number;
}

export interface ListJobEntitiesParams {
  limit?: number;
  offset?: number;
  review_status?: string;
  include_duplicates?: boolean;
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
  // FormData uploads must not have Content-Type set manually — the browser
  // generates it with the multipart boundary.
  const isFormData =
    typeof FormData !== "undefined" && init?.body instanceof FormData;
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (!isFormData && !("Content-Type" in headers)) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
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

  listJobEntities: (
    id: string,
    params: ListJobEntitiesParams = {},
  ): Promise<JobEntityListResponse> => {
    const qs = new URLSearchParams();
    if (params.limit !== undefined) qs.set("limit", String(params.limit));
    if (params.offset !== undefined) qs.set("offset", String(params.offset));
    if (params.review_status) qs.set("review_status", params.review_status);
    if (params.include_duplicates) qs.set("include_duplicates", "true");
    const q = qs.toString();
    return request<JobEntityListResponse>(`/jobs/${id}/entities${q ? `?${q}` : ""}`);
  },

  createDiscoveryJob: (payload: CreateDiscoveryJobRequest): Promise<Job> =>
    request<Job>("/jobs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  uploadBulkCsv: (
    file: File,
    opts: { budget_cap_usd?: number; idempotency_key?: string } = {},
  ): Promise<Job> => {
    const fd = new FormData();
    fd.append("file", file);
    if (opts.budget_cap_usd !== undefined) {
      fd.append("budget_cap_usd", String(opts.budget_cap_usd));
    }
    if (opts.idempotency_key) fd.append("idempotency_key", opts.idempotency_key);
    return request<Job>("/jobs/bulk/csv", { method: "POST", body: fd });
  },

  exportCsvUrl: (id: string): string =>
    `${API_BASE}/jobs/${id}/export.csv`,
};
