/**
 * Thin client for the FastAPI backend. All endpoints live under NEXT_PUBLIC_API_BASE_URL.
 *
 * Errors bubble up as `ApiError` so callers can render the backend's `detail`
 * message instead of a generic "Something went wrong."
 */

import { clearToken, getToken } from "./auth";

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

export interface ReviewEntity {
  id: string;
  job_id: string;
  job_query: string;
  name: string;
  website: string | null;
  email: string | null;
  phone: string | null;
  address: string | null;
  city: string | null;
  country: string | null;
  category: string | null;
  quality_score: number | null;
  review_status: string;
  field_sources: Record<string, { source?: string; confidence?: number; fetched_at?: string }>;
  created_at: string;
}

export interface ReviewListResponse {
  items: ReviewEntity[];
  total: number;
  limit: number;
  offset: number;
}

export interface ReviewDecision {
  id: string;
  review_status: string;
}

export interface SearchTemplate {
  id: string;
  name: string;
  query: string;
  default_limit: number;
  default_budget_cap_usd: number;
  created_at: string;
}

export interface CreateTemplateRequest {
  name: string;
  query: string;
  default_limit?: number;
  default_budget_cap_usd?: number;
}

export interface BlacklistEntry {
  id: string;
  email: string | null;
  domain: string | null;
  reason: string | null;
  created_at: string;
}

export interface BlacklistListResponse {
  items: BlacklistEntry[];
  total: number;
}

export interface CreateBlacklistEntryRequest {
  email?: string;
  domain?: string;
  reason?: string;
}

export interface CreateDiscoveryJobRequest {
  query: string;
  limit?: number;
  budget_cap_usd?: number;
  idempotency_key?: string;
}

export interface AuthUserResponse {
  id: string;
  email: string;
  is_active: boolean;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: AuthUserResponse;
}

export interface AuthCredentials {
  email: string;
  password: string;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface ApiKeyCreateResponse extends ApiKey {
  key: string;
}

export interface ApiKeyListResponse {
  items: ApiKey[];
  total: number;
}

export interface ReverifyResponse {
  scanned: number;
  websites_checked: number;
  websites_dead: number;
  emails_checked: number;
  emails_invalid: number;
  phones_checked: number;
  phones_invalid: number;
  errors: string[];
}

export type CircuitState = "closed" | "open" | "half_open";

export interface CircuitSnapshot {
  name: string;
  state: CircuitState;
}

export type SystemOverall = "ok" | "degraded" | "impaired";

export interface SystemStatusResponse {
  overall: SystemOverall;
  circuits: CircuitSnapshot[];
}

export interface SourceFriction {
  source: string;
  calls: number;
  success: number;
  rate_limited: number;
  server_errors: number;
  avg_duration_ms: number | null;
  slow_reason: string | null;
}

export interface JobDiagnostics {
  job_id: string;
  sources: SourceFriction[];
  retry_after_hits: number;
  summary: string;
}

export interface ComplianceSettings {
  compliant_mode: boolean;
  jurisdiction: string;
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
  const token = getToken();
  if (token && !("Authorization" in headers)) {
    headers["Authorization"] = `Bearer ${token}`;
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
    if (res.status === 401) {
      clearToken();
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  login: (payload: AuthCredentials): Promise<TokenResponse> =>
    request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  register: (payload: AuthCredentials): Promise<TokenResponse> =>
    request<TokenResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  logout: (): Promise<void> => request<void>("/auth/logout", { method: "POST" }),

  me: (): Promise<AuthUserResponse> => request<AuthUserResponse>("/auth/me"),

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

  listReviewQueue: (
    opts: { limit?: number; offset?: number; job_id?: string } = {},
  ): Promise<ReviewListResponse> => {
    const qs = new URLSearchParams();
    if (opts.limit !== undefined) qs.set("limit", String(opts.limit));
    if (opts.offset !== undefined) qs.set("offset", String(opts.offset));
    if (opts.job_id) qs.set("job_id", opts.job_id);
    const q = qs.toString();
    return request<ReviewListResponse>(`/review${q ? `?${q}` : ""}`);
  },

  approveEntity: (entityId: string): Promise<ReviewDecision> =>
    request<ReviewDecision>(`/review/${entityId}/approve`, { method: "POST" }),

  rejectEntity: (entityId: string): Promise<ReviewDecision> =>
    request<ReviewDecision>(`/review/${entityId}/reject`, { method: "POST" }),

  listTemplates: (): Promise<{ items: SearchTemplate[]; total: number }> =>
    request<{ items: SearchTemplate[]; total: number }>("/templates"),

  createTemplate: (payload: CreateTemplateRequest): Promise<SearchTemplate> =>
    request<SearchTemplate>("/templates", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  deleteTemplate: (id: string): Promise<void> => deleteRequest(`/templates/${id}`),

  listBlacklist: (q?: string): Promise<BlacklistListResponse> => {
    const qs = new URLSearchParams();
    if (q) qs.set("q", q);
    const query = qs.toString();
    return request<BlacklistListResponse>(`/blacklist${query ? `?${query}` : ""}`);
  },

  addBlacklistEntry: (payload: CreateBlacklistEntryRequest): Promise<BlacklistEntry> =>
    request<BlacklistEntry>("/blacklist", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  deleteBlacklistEntry: (id: string): Promise<void> => deleteRequest(`/blacklist/${id}`),

  listApiKeys: (): Promise<ApiKeyListResponse> =>
    request<ApiKeyListResponse>("/api-keys"),

  createApiKey: (name: string): Promise<ApiKeyCreateResponse> =>
    request<ApiKeyCreateResponse>("/api-keys", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  revokeApiKey: (id: string): Promise<void> => deleteRequest(`/api-keys/${id}`),

  runReverify: (payload: {
    max_age_days?: number;
    limit?: number;
  } = {}): Promise<ReverifyResponse> =>
    request<ReverifyResponse>("/reverify", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getSystemStatus: (): Promise<SystemStatusResponse> =>
    request<SystemStatusResponse>("/status"),

  getJobDiagnostics: (id: string): Promise<JobDiagnostics> =>
    request<JobDiagnostics>(`/jobs/${id}/diagnostics`),

  getComplianceSettings: (): Promise<ComplianceSettings> =>
    request<ComplianceSettings>("/settings/compliance"),
};

async function deleteRequest(path: string): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "DELETE",
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
}
