import { freshnessFor } from "@/lib/freshness";

type Provenance = Record<
  string,
  { source?: string; confidence?: number; fetched_at?: string } | undefined
>;

export function FreshnessBadge({
  fieldSources,
  field,
}: {
  fieldSources: Provenance | null | undefined;
  field: string;
}) {
  const info = freshnessFor(fieldSources, field);
  if (!info) return null;
  const style =
    info.tone === "very_stale"
      ? "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-100"
      : "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100";
  return (
    <span
      title={info.title}
      className={
        "ml-1 inline-block rounded px-1 py-0.5 align-middle text-[10px] font-medium " +
        style
      }
    >
      {info.label}
    </span>
  );
}
