/**
 * Field-freshness helpers. A field is "stale" when its provenance timestamp
 * (`field_sources[name].fetched_at`) is older than the freshness threshold.
 * Thresholds chosen to match compliance.md §6 defaults: 90 days warn, 180 red.
 */

export const STALE_DAYS = 90;
export const VERY_STALE_DAYS = 180;

export type FreshnessTone = "stale" | "very_stale";

export interface FreshnessInfo {
  tone: FreshnessTone;
  label: string;
  title: string;
}

type ProvenanceRecord = Record<
  string,
  { source?: string; confidence?: number; fetched_at?: string } | undefined
>;

export function freshnessFor(
  fieldSources: ProvenanceRecord | null | undefined,
  fieldName: string,
  now: Date = new Date(),
): FreshnessInfo | null {
  const fetchedAt = fieldSources?.[fieldName]?.fetched_at;
  if (!fetchedAt) return null;
  const then = new Date(fetchedAt).getTime();
  if (Number.isNaN(then)) return null;
  const ageMs = now.getTime() - then;
  const ageDays = Math.floor(ageMs / (1000 * 60 * 60 * 24));
  if (ageDays < STALE_DAYS) return null;

  const tone: FreshnessTone = ageDays >= VERY_STALE_DAYS ? "very_stale" : "stale";
  return {
    tone,
    label: formatAge(ageDays),
    title: `Source last verified ${formatAge(ageDays)} ago (${new Date(
      fetchedAt,
    ).toLocaleDateString()})`,
  };
}

function formatAge(days: number): string {
  if (days < 365) {
    const months = Math.floor(days / 30);
    return months <= 1 ? `${days}d old` : `${months}mo old`;
  }
  const years = Math.floor(days / 365);
  return years === 1 ? "1y old" : `${years}y old`;
}
