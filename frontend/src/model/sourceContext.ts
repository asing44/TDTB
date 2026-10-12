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
