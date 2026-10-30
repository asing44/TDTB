/* habitGroups.ts — the Set up habits rail's Morning / Afternoon / Evening /
   Anytime buckets.

   Todoist hands the habits over as a flat active list (id, content, minutes);
   it carries no time-of-day field, so the bucket for a habit is an explicit
   owner decision recorded here. Add or move a habit by editing this map; a
   habit that is not listed lands in "Anytime", which keeps a brand-new habit
   visible instead of hiding it. */

export const HABIT_GROUPS = ["Morning", "Afternoon", "Evening", "Anytime"] as const;

export type HabitGroup = (typeof HABIT_GROUPS)[number];

/** Habit name (case-insensitive) → bucket. Matches the owner's mockup. */
const BY_NAME: Record<string, HabitGroup> = {
  litterzz: "Morning",
  playtime: "Morning",
  "stretch and breathing": "Morning",
  tdtb: "Morning",
  messages: "Morning",
  news: "Morning",
  water: "Morning",
  cards: "Afternoon",
  actualbudget: "Afternoon",
  "quick tasks": "Afternoon",
  journal: "Evening",
  "inbox zero": "Evening",
  "pet a stray kitty": "Anytime",
  "negative visualization": "Anytime",
};

export function habitGroupOf(name: string): HabitGroup {
  return BY_NAME[name.trim().toLowerCase()] ?? "Anytime";
}
