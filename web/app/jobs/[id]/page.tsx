"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Download,
  Loader2,
} from "lucide-react";
import { FreshnessBadge } from "@/components/FreshnessBadge";
import { JobDiagnosticsPanel } from "@/components/JobDiagnostics";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ApiError,
  api,
  type Job,
  type JobEntity,
  type JobStatus,
} from "@/lib/api";

const TERMINAL_STATUSES: ReadonlySet<JobStatus> = new Set([
  "succeeded",
  "failed",
  "rejected",
  "budget_exceeded",
]);

type BadgeVariant =
  | "default"
  | "secondary"
  | "destructive"
  | "success"
  | "warning"
  | "outline"
  | "muted";

const STATUS_VARIANT: Record<JobStatus, BadgeVariant> = {
  pending: "muted",
  running: "default",
  succeeded: "success",
  failed: "destructive",
  rejected: "warning",
  budget_exceeded: "warning",
};

const STATUS_LABEL: Record<JobStatus, string> = {
  pending: "Pending",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  rejected: "Rejected",
  budget_exceeded: "Budget exceeded",
};

const REVIEW_VARIANT: Record<string, BadgeVariant> = {
  approved: "success",
  review: "warning",
  rejected: "destructive",
  duplicate: "muted",
  pending: "muted",
};

const PAGE_SIZE = 25;
const ALL = "__all__";

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
  const [reviewFilter, setReviewFilter] = useState<string>(ALL);
  const [includeDuplicates, setIncludeDuplicates] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [jobRes, entitiesRes] = await Promise.all([
        api.getJob(id),
        api.listJobEntities(id, {
          limit: PAGE_SIZE,
          offset,
          review_status: reviewFilter === ALL ? undefined : reviewFilter,
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
        <Button variant="ghost" size="sm" asChild className="-ml-2 gap-1.5">
          <Link href="/">
            <ArrowLeft className="h-4 w-4" />
            Back to dashboard
          </Link>
        </Button>
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="flex items-start gap-3 pt-6 text-sm text-destructive">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <div>{error}</div>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (job === null) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading job…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" asChild className="-ml-2 gap-1.5">
        <Link href="/">
          <ArrowLeft className="h-4 w-4" />
          Back to dashboard
        </Link>
      </Button>

      <JobHeader job={job} />

      <JobDiagnosticsPanel jobId={id} />

      <section className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Entities</h2>
            <p className="text-sm text-muted-foreground">
              {total} total
              {total > 0 &&
                ` · showing ${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`}
              {inFlight && " · refreshing every 2s"}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Review
              </span>
              <Select
                value={reviewFilter}
                onValueChange={(value) => {
                  setReviewFilter(value);
                  setOffset(0);
                }}
              >
                <SelectTrigger className="h-8 w-[140px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>All</SelectItem>
                  <SelectItem value="approved">Approved</SelectItem>
                  <SelectItem value="review">Review</SelectItem>
                  <SelectItem value="rejected">Rejected</SelectItem>
                  <SelectItem value="pending">Pending</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
              <Checkbox
                checked={includeDuplicates}
                onCheckedChange={(checked) => {
                  setIncludeDuplicates(checked === true);
                  setOffset(0);
                }}
              />
              Include duplicates
            </label>
          </div>
        </div>

        {error && (
          <Card className="border-destructive/40 bg-destructive/5">
            <CardContent className="flex items-start gap-3 pt-6 text-sm text-destructive">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>{error}</div>
            </CardContent>
          </Card>
        )}

        {entities.length === 0 ? (
          <Card>
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              {inFlight
                ? "No entities yet — pipeline is still running."
                : "No entities match the current filter."}
            </CardContent>
          </Card>
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
  const percent = job.progress_percent ?? 0;
  return (
    <Card>
      <CardContent className="space-y-5 pt-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-2">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Badge variant={STATUS_VARIANT[job.status]}>
                {STATUS_LABEL[job.status]}
              </Badge>
              <span>·</span>
              <span>{formatRelative(job.created_at)}</span>
            </div>
            <h2
              className="break-words text-xl font-semibold tracking-tight"
              title={job.query_raw}
            >
              {job.query_raw}
            </h2>
            {job.error && (
              <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                <div>{job.error}</div>
              </div>
            )}
          </div>
          {job.status === "succeeded" && (
            <Button asChild className="gap-2">
              <a href={api.exportCsvUrl(job.id)}>
                <Download className="h-4 w-4" />
                Export CSV
              </a>
            </Button>
          )}
        </div>

        <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
          <Metric label="Entities" value={String(job.entity_count)} />
          <Metric
            label="Cost"
            value={`$${Number(job.cost_usd).toFixed(3)}`}
            hint={`of $${Number(job.budget_cap_usd).toFixed(2)} cap`}
          />
          <Metric label="Limit" value={String(job.limit)} />
          <Metric
            label="Progress"
            value={
              job.progress_percent === null
                ? "—"
                : `${job.places_processed} / ${job.places_discovered}`
            }
            hint={
              job.progress_percent === null
                ? undefined
                : `${Math.round(job.progress_percent)}%`
            }
          />
        </div>

        {job.progress_percent !== null && (
          <Progress
            value={Math.max(0, Math.min(100, percent))}
            className="h-1.5"
          />
        )}
      </CardContent>
    </Card>
  );
}

function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
      {hint && (
        <div className="text-xs text-muted-foreground">{hint}</div>
      )}
    </div>
  );
}

function EntitiesTable({ entities }: { entities: JobEntity[] }) {
  return (
    <Card className="overflow-hidden p-0">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Contact</TableHead>
            <TableHead>Location</TableHead>
            <TableHead className="w-20 text-right">Quality</TableHead>
            <TableHead className="w-28">Review</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {entities.map((e) => (
            <TableRow key={e.id} className="align-top">
              <TableCell className="max-w-[260px]">
                <div className="font-medium">{e.name}</div>
                {e.website && (
                  <div className="mt-0.5 flex items-center gap-1 text-xs">
                    <a
                      href={e.website}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="max-w-[220px] truncate text-primary hover:underline"
                      title={e.website}
                    >
                      {e.website}
                    </a>
                    <FreshnessBadge
                      fieldSources={e.field_sources}
                      field="website"
                    />
                  </div>
                )}
                {e.category && (
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {e.category}
                  </div>
                )}
              </TableCell>
              <TableCell className="max-w-[240px]">
                {e.email ? (
                  <div className="flex items-center gap-1 text-sm">
                    <a
                      href={`mailto:${e.email}`}
                      className="max-w-[200px] truncate text-primary hover:underline"
                      title={e.email}
                    >
                      {e.email}
                    </a>
                    <FreshnessBadge
                      fieldSources={e.field_sources}
                      field="email"
                    />
                  </div>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
                {e.phone && (
                  <div className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
                    <span>{e.phone}</span>
                    <FreshnessBadge
                      fieldSources={e.field_sources}
                      field="phone"
                    />
                  </div>
                )}
              </TableCell>
              <TableCell className="max-w-[220px] text-sm">
                {e.city || e.country ? (
                  <div>{[e.city, e.country].filter(Boolean).join(", ")}</div>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
                {e.address && (
                  <div
                    className="mt-0.5 truncate text-xs text-muted-foreground"
                    title={e.address}
                  >
                    {e.address}
                  </div>
                )}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {e.quality_score ?? "—"}
              </TableCell>
              <TableCell>
                <Badge variant={REVIEW_VARIANT[e.review_status] ?? "muted"}>
                  {e.review_status}
                </Badge>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
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
    <div className="flex items-center justify-end gap-2">
      <Button
        variant="outline"
        size="sm"
        disabled={!hasPrev}
        onClick={() => onChange(Math.max(0, offset - pageSize))}
      >
        <ChevronLeft className="h-4 w-4" />
        Previous
      </Button>
      <Button
        variant="outline"
        size="sm"
        disabled={!hasNext}
        onClick={() => onChange(offset + pageSize)}
      >
        Next
        <ChevronRight className="h-4 w-4" />
      </Button>
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
