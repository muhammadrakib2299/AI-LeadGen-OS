"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Coins,
  DollarSign,
  Download,
  Loader2,
  Plus,
  RefreshCcw,
  Save,
  Sparkles,
  X,
} from "lucide-react";
import { ReverifyCard } from "@/components/ReverifyCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
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
  type JobStatus,
  type SearchTemplate,
} from "@/lib/api";
import { cn } from "@/lib/utils";

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

export default function DashboardPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const res = await api.listJobs(25);
      setJobs(res.items);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const anyRunning = useMemo(
    () => jobs?.some((j) => !TERMINAL_STATUSES.has(j.status)) ?? false,
    [jobs],
  );

  useEffect(() => {
    const interval = anyRunning ? 2000 : 10_000;
    const id = setInterval(refresh, interval);
    return () => clearInterval(id);
  }, [anyRunning, refresh]);

  const stats = useMemo(() => computeStats(jobs ?? []), [jobs]);

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            Submit discovery jobs and track their status, cost, and output.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void refresh()}
          disabled={refreshing}
        >
          <RefreshCcw
            className={cn("h-4 w-4", refreshing && "animate-spin")}
          />
          Refresh
        </Button>
      </header>

      <StatsRow
        running={stats.running}
        succeeded={stats.succeeded}
        entities={stats.entities}
        cost={stats.cost}
        liveRefresh={anyRunning}
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <CreateJobForm onCreated={refresh} />
        <ReverifyCard />
      </div>

      <section className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Recent jobs</h2>
            <p className="text-sm text-muted-foreground">
              The last 25 jobs you submitted. Click a row for full detail.
            </p>
          </div>
          {anyRunning && (
            <Badge variant="default" className="gap-1.5">
              <Loader2 className="h-3 w-3 animate-spin" />
              Polling every 2s
            </Badge>
          )}
        </div>

        {loadError && (
          <Card className="border-destructive/40 bg-destructive/5">
            <CardContent className="flex items-start gap-3 pt-6 text-sm text-destructive">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <div className="font-medium">Couldn&apos;t reach the API</div>
                <div className="text-destructive/80">{loadError}</div>
              </div>
            </CardContent>
          </Card>
        )}

        {jobs === null && !loadError ? (
          <SkeletonTable />
        ) : jobs && jobs.length === 0 ? (
          <EmptyState />
        ) : jobs && jobs.length > 0 ? (
          <JobsTable jobs={jobs} />
        ) : null}
      </section>
    </div>
  );
}

function computeStats(jobs: Job[]) {
  let running = 0;
  let succeeded = 0;
  let entities = 0;
  let cost = 0;
  for (const job of jobs) {
    if (!TERMINAL_STATUSES.has(job.status)) running += 1;
    if (job.status === "succeeded") succeeded += 1;
    entities += job.entity_count;
    cost += Number(job.cost_usd);
  }
  return { running, succeeded, entities, cost };
}

function StatsRow({
  running,
  succeeded,
  entities,
  cost,
  liveRefresh,
}: {
  running: number;
  succeeded: number;
  entities: number;
  cost: number;
  liveRefresh: boolean;
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        label="In flight"
        value={running}
        icon={Activity}
        hint={liveRefresh ? "Live" : "Idle"}
        accent={running > 0 ? "text-primary" : undefined}
      />
      <StatCard
        label="Succeeded"
        value={succeeded}
        icon={CheckCircle2}
        hint="Last 25 jobs"
      />
      <StatCard
        label="Entities"
        value={entities.toLocaleString()}
        icon={Sparkles}
        hint="Across shown jobs"
      />
      <StatCard
        label="Spend"
        value={`$${cost.toFixed(3)}`}
        icon={Coins}
        hint="Across shown jobs"
      />
    </div>
  );
}

function StatCard({
  label,
  value,
  icon: Icon,
  hint,
  accent,
}: {
  label: string;
  value: string | number;
  icon: React.ComponentType<{ className?: string }>;
  hint?: string;
  accent?: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </CardTitle>
        <Icon className={cn("h-4 w-4 text-muted-foreground", accent)} />
      </CardHeader>
      <CardContent>
        <div className={cn("text-2xl font-semibold tabular-nums", accent)}>
          {value}
        </div>
        {hint && (
          <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
        )}
      </CardContent>
    </Card>
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
      // Non-critical; the form still works.
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
    <Card>
      <CardHeader>
        <CardTitle>New discovery job</CardTitle>
        <CardDescription>
          A natural-language query. We plan, route, and bill it within your cap.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {templates && templates.length > 0 && (
          <div className="space-y-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Saved templates
            </div>
            <ul className="flex flex-wrap gap-2">
              {templates.map((t) => (
                <li
                  key={t.id}
                  className="group inline-flex items-center gap-1 rounded-full border bg-secondary/60 py-0.5 pl-3 pr-1 text-xs"
                >
                  <button
                    type="button"
                    onClick={() => applyTemplate(t)}
                    className="font-medium hover:text-primary"
                    title={t.query}
                  >
                    {t.name}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDeleteTemplate(t.id)}
                    aria-label={`Delete template ${t.name}`}
                    className="ml-0.5 rounded-full p-0.5 text-muted-foreground hover:bg-destructive/15 hover:text-destructive"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="query">Query</Label>
            <Input
              id="query"
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="restaurants in Paris"
              required
              minLength={3}
              maxLength={500}
            />
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="limit">Limit</Label>
              <Input
                id="limit"
                type="number"
                value={limit}
                min={1}
                max={1000}
                onChange={(e) => setLimit(Number(e.target.value))}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="budget">Budget cap (USD)</Label>
              <div className="relative">
                <DollarSign className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="budget"
                  type="number"
                  value={budget}
                  min={0.1}
                  max={100}
                  step={0.1}
                  onChange={(e) => setBudget(Number(e.target.value))}
                  className="pl-8"
                />
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="submit"
              disabled={submitting || !query.trim()}
              className="gap-2"
            >
              {submitting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
              {submitting ? "Submitting" : "Submit job"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setSaveOpen((open) => !open);
                setSaveError(null);
              }}
              disabled={!query.trim()}
              className="gap-1.5"
            >
              <Save className="h-3.5 w-3.5" />
              {saveOpen ? "Cancel" : "Save as template"}
            </Button>
            {error && (
              <span className="text-sm text-destructive">{error}</span>
            )}
            {justCreated && !error && (
              <span className="text-xs text-muted-foreground">
                Created job {justCreated.slice(0, 8)}…
              </span>
            )}
          </div>
        </form>

        {saveOpen && (
          <form
            onSubmit={handleSaveTemplate}
            className="flex flex-wrap items-end gap-2 border-t pt-4"
          >
            <div className="flex-1 min-w-[220px] space-y-1.5">
              <Label htmlFor="template-name" className="text-xs">
                Template name
              </Label>
              <Input
                id="template-name"
                type="text"
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                placeholder="EU SaaS startups"
                required
                minLength={1}
                maxLength={128}
              />
            </div>
            <Button
              type="submit"
              variant="outline"
              disabled={!saveName.trim() || !query.trim()}
            >
              Save
            </Button>
            {saveError && (
              <span className="text-sm text-destructive">{saveError}</span>
            )}
          </form>
        )}
      </CardContent>
    </Card>
  );
}

function JobsTable({ jobs }: { jobs: Job[] }) {
  return (
    <Card className="overflow-hidden p-0">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Query</TableHead>
            <TableHead className="w-32">Status</TableHead>
            <TableHead className="w-48">Progress</TableHead>
            <TableHead className="w-24 text-right">Entities</TableHead>
            <TableHead className="w-28 text-right">Cost</TableHead>
            <TableHead className="w-28">Created</TableHead>
            <TableHead className="w-24 text-right" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {jobs.map((job) => (
            <TableRow key={job.id} className="group">
              <TableCell>
                <Link
                  href={`/jobs/${job.id}`}
                  className="block max-w-[340px] truncate font-medium text-foreground transition-colors group-hover:text-primary"
                  title={job.query_raw}
                >
                  {job.query_raw}
                </Link>
                {job.error && (
                  <div
                    className="mt-1 max-w-[340px] truncate text-xs text-destructive"
                    title={job.error}
                  >
                    {job.error}
                  </div>
                )}
              </TableCell>
              <TableCell>
                <Badge variant={STATUS_VARIANT[job.status]}>
                  {STATUS_LABEL[job.status]}
                </Badge>
              </TableCell>
              <TableCell>
                {job.progress_percent === null ? (
                  <span className="text-muted-foreground">—</span>
                ) : (
                  <div className="flex items-center gap-2">
                    <Progress
                      value={Math.max(0, Math.min(100, job.progress_percent))}
                      className="h-1.5 w-24"
                    />
                    <span className="text-xs tabular-nums text-muted-foreground">
                      {Math.round(job.progress_percent)}%
                    </span>
                  </div>
                )}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {job.entity_count}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                ${Number(job.cost_usd).toFixed(3)}
              </TableCell>
              <TableCell className="text-muted-foreground">
                {formatRelative(job.created_at)}
              </TableCell>
              <TableCell className="text-right">
                {job.status === "succeeded" ? (
                  <Button asChild size="sm" variant="ghost" className="gap-1.5">
                    <a href={api.exportCsvUrl(job.id)}>
                      <Download className="h-3.5 w-3.5" />
                      CSV
                    </a>
                  </Button>
                ) : (
                  <Button
                    asChild
                    size="sm"
                    variant="ghost"
                    className="gap-1 text-muted-foreground"
                  >
                    <Link href={`/jobs/${job.id}`}>
                      View
                      <ArrowRight className="h-3.5 w-3.5" />
                    </Link>
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

function SkeletonTable() {
  return (
    <Card className="divide-y overflow-hidden p-0">
      <div className="flex items-center gap-4 p-4">
        <div className="h-4 flex-1 animate-pulse rounded bg-muted" />
        <div className="h-4 w-20 animate-pulse rounded bg-muted" />
        <div className="h-4 w-24 animate-pulse rounded bg-muted" />
      </div>
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 p-4">
          <div className="h-4 flex-1 animate-pulse rounded bg-muted" />
          <div className="h-5 w-20 animate-pulse rounded-full bg-muted" />
          <div className="h-4 w-24 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </Card>
  );
}

function EmptyState() {
  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-2 py-12 text-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
          <Sparkles className="h-5 w-5" />
        </div>
        <div className="text-sm font-medium">No jobs yet</div>
        <p className="max-w-sm text-sm text-muted-foreground">
          Submit a query above to start a discovery run. Your recent jobs will
          appear here.
        </p>
      </CardContent>
    </Card>
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
