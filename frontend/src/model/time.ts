/* time.ts — pure HH:MM helpers. Wire format is 24h HH:MM (server contract);
   display format is 12h (user preference, ported from the wizard).
   display12h is the authoritative user-facing formatter; the 24h helper
   added under FEEDBACK-09 is removed (FEEDBACK-12). */

export function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

export function toHHMM(minutes: number): string {
  const m = ((minutes % 1440) + 1440) % 1440;
  const h = Math.floor(m / 60);
  return `${String(h).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

export function addMinutes(hhmm: string, delta: number): string {
  return toHHMM(toMinutes(hhmm) + delta);
}

/** 12-hour display: "7:45 AM", "12:00 PM", "11:45 PM". */
export function display12h(hhmm: string | null): string {
  if (!hhmm) return "—";
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h < 12 ? "AM" : "PM";
  const hour = h % 12 === 0 ? 12 : h % 12;
  return m === 0 ? `${hour} ${suffix}` : `${hour}:${String(m).padStart(2, "0")} ${suffix}`;
}

/** A small tolerance for arithmetic that should have landed on a block
    boundary. Capacity subtraction can produce values such as
    9.000000000000002; those are still nine blocks to a person reading the
    cockpit. Deliberate fractional values (for example 9.166666...) remain
    durations. */
const BLOCK_INTEGER_EPSILON = 1e-9;

function wholeBlockValue(blocks: number): number | null {
  if (!Number.isFinite(blocks)) return null;
  const rounded = Math.round(blocks);
  return Math.abs(blocks - rounded) <= BLOCK_INTEGER_EPSILON ? rounded : null;
}

function durationLabel(minutes: number, zeroLabel = "0min"): string {
  if (!Number.isFinite(minutes)) return "—";
  const sign = minutes < 0 ? "-" : "";
  const absoluteMinutes = Math.round(Math.abs(minutes));
  if (absoluteMinutes === 0) return zeroLabel;
  const hours = Math.floor(absoluteMinutes / 60);
  const remainder = absoluteMinutes % 60;
  const value =
    hours === 0
      ? `${remainder}min`
      : remainder === 0
        ? `${hours}hr`
        : `${hours}hr ${remainder}min`;
  return `${sign}${value}`;
}

/** User-facing wall-clock duration: omit zero components and retain a sign. */
export function formatDurationMinutes(minutes: number): string {
  return durationLabel(minutes);
}

/** Block duration for row editors: 0 → "All day", 0.5 → "15min", 3 →
    "1hr 30min". This remains a duration label rather than a capacity count. */
export function blocksLabel(blocks: number): string {
  if (blocks === 0) return "All day";
  return durationLabel(blocks * 30);
}

/** User-facing amount for capacity and allocation surfaces. Whole block
    counts stay counts; fractional values become a rounded human duration.
    The sign is retained when the input itself is signed. */
export function formatBlockAmount(blocks: number): string {
  const whole = wholeBlockValue(blocks);
  if (whole != null) return `${whole} blk`;
  return durationLabel(blocks * 30);
}

/** Minutes → compact duration: 240 → "4h", 90 → "1h30m", 45 → "45m". */
export function compactDuration(mins: number): string {
  if (!Number.isFinite(mins)) return "—";
  const sign = mins < 0 ? "-" : "";
  const absoluteMinutes = Math.round(Math.abs(mins));
  if (absoluteMinutes < 60) return `${sign}${absoluteMinutes}m`;
  const h = Math.floor(absoluteMinutes / 60);
  const m = absoluteMinutes % 60;
  return m === 0 ? `${sign}${h}h` : `${sign}${h}h${String(m).padStart(2, "0")}m`;
}

export function minutesToBlocks(mins: number): number {
  return Math.ceil(mins / 30);
}
