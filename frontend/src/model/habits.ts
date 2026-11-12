/* habits.ts — today-only habit overrides on the Set up day screen.

   Habits are an ACTIVE list kept in Grit. A Grit-sourced block marks each item
   `done`
   (done or failed, from the server's classifier); such rows stay listed but
   stop counting. A legacy block carries no completion, so nothing there is
   done and nothing is derived here. An operator switches a habit off
   today (leaving it out of today's capacity) or changes its minutes. With no
   items only the aggregate exists, so a single "Habits (total)" row stands in
   for them — the backend ignores a total override once items exist. The
   capacity math itself is server-side; this module holds the pure view and
   edit rules. Overrides never reach Grit or the calendar. */

import { formatDurationMinutes } from "./time";
import type { HabitOverride, HabitOverrides, HabitsInfo, HabitStatus } from "./types";

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
  /** Grit says this habit is finished for today (done or failed): it stops
      consuming capacity whatever the override says. Never true for a legacy
      block or the total row. */
  done: boolean;
  /** The Grit verdict behind `done`, when the block carries one. */
  status?: HabitStatus;
  /** The row differs from its default. */
  edited: boolean;
}

export interface HabitsView {
  mode: "items" | "total" | "hidden";
  rows: HabitRow[];
  /** How many habits the active list holds: the rows, or the artifact's
      aggregate total when it carries no per-habit items. */
  habitCount: number;
  /** Habits Grit reports finished today (done or failed). */
  doneCount: number;
  /** Habits still to do today: every row that is not done. */
  dueCount: number;
  /** Minutes capacity uses: rows that are on and not done. */
  countedMinutes: number;
  summary: string;
}

function rowOf(
  key: string,
  label: string,
  defaultMinutes: number,
  entry: HabitOverride | null | undefined,
  done = false,
  status?: HabitStatus,
): HabitRow {
  const on = entry?.on !== false;
  const minutes = entry?.minutes ?? defaultMinutes;
  return {
    key,
    label,
    defaultMinutes,
    minutes,
    on,
    done,
    ...(status ? { status } : {}),
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
  let habitCount = 0;
  if (info && info.items.length > 0) {
    mode = "items";
    habitCount = info.items.length;
    rows = info.items.map((item) => {
      const key = item.id || item.name;
      return rowOf(
        key,
        item.name,
        item.minutes,
        ov.items[key] ?? ov.items[item.name],
        item.done === true,
        item.status,
      );
    });
  } else if (info && info.estMinutes > 0) {
    mode = "total";
    habitCount = info.total > 0 ? info.total : 1;
    rows = [rowOf(HABIT_TOTAL_KEY, "Habits (total)", info.estMinutes, ov.total)];
  }
  const countedMinutes = rows.reduce((sum, r) => sum + (r.on && !r.done ? r.minutes : 0), 0);
  const doneCount = rows.filter((r) => r.done).length;
  const dueCount = rows.length - doneCount;
  return {
    mode,
    rows,
    habitCount,
    doneCount,
    dueCount,
    countedMinutes,
    summary:
      mode === "items"
        ? `${dueCount} due · ${doneCount} done · ~${countedMinutes} min`
        : `${habitCount} ${habitCount === 1 ? "habit" : "habits"} · ` +
          `${formatDurationMinutes(countedMinutes)} counted`,
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
