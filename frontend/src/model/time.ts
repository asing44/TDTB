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

/** Blocks → human duration: 0 → "All day", 0.5 → "15min", 3 → "1hr 30min". */
export function blocksLabel(blocks: number): string {
  if (blocks === 0) return "All day";
  const mins = blocks * 30;
  if (mins < 60) return `${mins}min`;
  return mins % 60 === 0 ? `${mins / 60}hr` : `${Math.floor(mins / 60)}hr ${mins % 60}min`;
}

/** Minutes → compact duration: 240 → "4h", 90 → "1h30m", 45 → "45m". */
export function compactDuration(mins: number): string {
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m === 0 ? `${h}h` : `${h}h${String(m).padStart(2, "0")}m`;
}

export function minutesToBlocks(mins: number): number {
  return Math.ceil(mins / 30);
}
