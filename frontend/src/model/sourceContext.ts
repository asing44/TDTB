/* sourceContext — T23: the bare source chip says nothing about what an item
   IS. Todoist rows expose their labels; Capacities rows get their KIND chip
   from the structure title (see Queue.kindLabel). Pure string builder, shared
   by the queue. */

import type { AssignedItem } from "./types";

/** Extra context beyond the source name, or "" when nothing useful exists:
    the row's labels, verbatim. */
export function sourceContext(item: AssignedItem): string {
  return (item.labels ?? []).join(" · ");
}

/** Max tag chips shown on a row's meta line before the rest fold into "+N". */
export const MAX_VISIBLE_TAGS = 3;

/** The row's visible tag chips: Todoist labels then Capacities tags, trimmed,
    de-duplicated (emoji kept verbatim). `rest` feeds the "+N" chip's title. */
export function rowTags(item: AssignedItem): { visible: string[]; rest: string[] } {
  const all: string[] = [];
  for (const raw of [...(item.labels ?? []), ...(item.tags ?? [])]) {
    const t = typeof raw === "string" ? raw.trim() : "";
    if (t && !all.includes(t)) all.push(t);
  }
  return { visible: all.slice(0, MAX_VISIBLE_TAGS), rest: all.slice(MAX_VISIBLE_TAGS) };
}
