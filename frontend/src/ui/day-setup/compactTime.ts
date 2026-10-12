/* Compact clock formats for the Set up day screen: "5:45p" in the Anchors card
   and "8a" on the strip ruler. The accessible names keep the full display12h
   form, so this is presentation only. */

const pad = (n: number) => String(n).padStart(2, "0");

/** "17:45" -> "5:45p"; "08:30" -> "8:30a". */
export function compactClock(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  return `${h % 12 === 0 ? 12 : h % 12}:${pad(m)}${h < 12 ? "a" : "p"}`;
}

/** Whole-hour ruler label from minutes since midnight: 480 -> "8a", 720 -> "12p". */
export function compactHour(minutes: number): string {
  const h = Math.floor(minutes / 60) % 24;
  return `${h % 12 === 0 ? 12 : h % 12}${h < 12 ? "a" : "p"}`;
}

/** Run caption from minutes: 900..1020 -> "3–5 PM"; crossing noon keeps both
    suffixes ("11 AM–1 PM"). Minutes show only when not on the hour. */
export function compactRange(startMin: number, endMin: number): string {
  const part = (min: number) => {
    const h = Math.floor(min / 60) % 24;
    const m = min % 60;
    const hour = h % 12 === 0 ? 12 : h % 12;
    return { text: m === 0 ? `${hour}` : `${hour}:${pad(m)}`, suffix: h < 12 ? "AM" : "PM" };
  };
  const a = part(startMin);
  const b = part(endMin);
  return a.suffix === b.suffix
    ? `${a.text}–${b.text} ${b.suffix}`
    : `${a.text} ${a.suffix}–${b.text} ${b.suffix}`;
}
