/* relative.ts — one small relative-age formatter shared by the Sources
   details popover and the rail's refreshed readout. ``now`` is injected so the
   labels are deterministic in tests and so no timer or polling is required:
   callers compute the age at render (and on popover open). */

/** Human age for a past timestamp: "just now" / "N min ago" / "N h ago" /
    "yesterday" / a short date. Future or invalid timestamps degrade safely. */
export function formatRelative(now: Date, then: Date): string {
  const nowMs = now.getTime();
  const thenMs = then.getTime();
  if (!Number.isFinite(nowMs) || !Number.isFinite(thenMs)) return "unknown";
  const diffMs = nowMs - thenMs;
  if (diffMs < 60_000) return "just now";
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const startOfDay = (d: Date) =>
    new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const dayDiff = Math.round((startOfDay(now) - startOfDay(then)) / 86_400_000);
  if (dayDiff === 1) return "yesterday";
  return then.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(then.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}),
  });
}
