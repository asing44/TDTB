/* habits.ts — today-only habit overrides on the Set up day screen.

   The artifact may list each outstanding habit (`items`); an operator can
   leave one out of today's capacity (Count today) or change its minutes. With
   no items only the aggregate exists, so a single "Habits (total)" row stands
   in for them — the backend ignores a total override once items exist. The
   capacity math itself is server-side; this module holds the pure view and
   edit rules. Overrides never reach Todoist or the calendar. */

import { formatDurationMinutes } from "./time";
import type { HabitOverride, HabitOverrides, HabitsInfo } from "./types";

export const HABIT_MINUTES_MAX = 480;
export const HABIT_TOTAL_KEY = "total";

/** A minutes field's text → integer 0..480, or null when it must not commit. */
export function parseHabitMinutes(text: string): number | null {
  const t = text.trim();
  if (!/^\d+$/.test(t)) return null;
  const n = Number(t);
  return n <= HABIT_MINUTES_MAX ? n : null;
}

export interface HabitRow {
  /** Override key: the habit id (or name), or "total" for the fallback row. */
  key: string;
  label: string;
  defaultMinutes: number;
  /** Effective minutes (override applied). */
  minutes: number;
  /** Count today. */
  on: boolean;
  /** The row differs from its default. */
  edited: boolean;
}

export interface HabitsView {
  mode: "items" | "total" | "hidden";
  rows: HabitRow[];
  outstanding: number;
  /** Minutes capacity uses: counted rows only. */
  countedMinutes: number;
  summary: string;
}

function rowOf(
  key: string,
  label: string,
  defaultMinutes: number,
  entry: HabitOverride | null | undefined,
): HabitRow {
  const on = entry?.on !== false;
  const minutes = entry?.minutes ?? defaultMinutes;
  return {
    key,
    label,
    defaultMinutes,
    minutes,
    on,
    edited: !on || minutes !== defaultMinutes,
  };
}

export function habitsView(
  info: HabitsInfo | undefined,
  overrides: HabitOverrides | undefined,
): HabitsView {
  const ov = overrides ?? { items: {}, total: null };
  let mode: HabitsView["mode"] = "hidden";
  let rows: HabitRow[] = [];
  if (info && info.items.length > 0) {
    mode = "items";
    rows = info.items.map((item) => {
      const key = item.id || item.name;
      return rowOf(key, item.name, item.minutes, ov.items[key] ?? ov.items[item.name]);
    });
  } else if (info && (info.outstanding > 0 || info.estMinutes > 0)) {
    mode = "total";
    rows = [rowOf(HABIT_TOTAL_KEY, "Habits (total)", info.estMinutes, ov.total)];
  }
  const countedMinutes = rows.reduce((sum, r) => sum + (r.on ? r.minutes : 0), 0);
  const outstanding = info?.outstanding ?? 0;
  return {
    mode,
    rows,
    outstanding,
    countedMinutes,
    summary: `${outstanding} outstanding · ${formatDurationMinutes(countedMinutes)} counted`,
  };
}

/** Apply a patch to one row's override entry. Returns a new object holding
    only what differs from the default: a row back at default loses its entry.
    Entries this call does not touch (including stale ones) are kept. */
export function withHabitEntry(
  overrides: HabitOverrides | undefined,
  key: string,
  patch: { on?: boolean; minutes?: number },
  defaultMinutes: number,
): HabitOverrides {
  const base = overrides ?? { items: {}, total: null };
  const current = key === HABIT_TOTAL_KEY ? base.total : base.items[key];
  const on = patch.on ?? current?.on !== false;
  const minutes = patch.minutes ?? current?.minutes ?? defaultMinutes;
  const entry: HabitOverride = {};
  if (!on) entry.on = false;
  if (minutes !== defaultMinutes) entry.minutes = minutes;
  const empty = Object.keys(entry).length === 0;
  if (key === HABIT_TOTAL_KEY) return { items: { ...base.items }, total: empty ? null : entry };
  const items = { ...base.items };
  if (empty) delete items[key];
  else items[key] = entry;
  return { items, total: base.total };
}
