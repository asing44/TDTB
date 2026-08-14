/* sourceContext — T23: the bare "vault"/"todoist" chip says nothing about
   what an item IS. Vault rows expose their note kind (types) and vault
   folder; todoist rows expose their labels. Pure string builder, shared by
   the queue. */

import type { AssignedItem } from "./types";

/** Extra context beyond the source name, or "" when nothing useful exists.
    Vault: `press · Intervals` (first non-generic type + parent folder,
    deduplicated case-insensitively). Todoist: labels verbatim. */
export function sourceContext(item: AssignedItem): string {
  if (item.source === "vault") {
    const type = item.types.find((t) => t && t !== "todoist") ?? null;
    const folder =
      item.path && item.path.includes("/")
        ? (item.path.split("/").slice(-2, -1)[0] ?? null)
        : null;
    const parts: string[] = [];
    if (type) parts.push(type);
    // "task · Tasks" is noise — keep the folder only when it adds signal.
    if (folder && (!type || !folder.toLowerCase().startsWith(type.toLowerCase()))) {
      parts.push(folder);
    }
    return parts.join(" · ");
  }
  return (item.labels ?? []).join(" · ");
}

/** The item's KIND, as one short token for the allocator's type chip —
    `project`, `task`, `interval`, `press`. Vault rows carry it in `types`
    (frontmatter `type:`); a Todoist row's `types` is the literal `["todoist"]`
    provenance marker, and a Todoist row is a task by construction, so that is
    what it reports.

    Split out from `sourceContext` (2026-07-27, Adam: "types should have chip
    signifies … to increase scannability") rather than replacing it — the
    queue's source line still reconstructs the flat one-string form. */
export function typeToken(item: AssignedItem): string | null {
  if (item.source === "todoist") return "task";
  return item.types.find((t) => t && t !== "todoist") ?? null;
}

/** `sourceContext` minus the part `typeToken` already renders as a chip: the
    vault folder, or the Todoist labels. "" when nothing is left to say. */
export function sourceDetail(item: AssignedItem): string {
  const full = sourceContext(item);
  const token = item.source === "vault" ? typeToken(item) : null;
  if (!token) return full;
  const rest = full.startsWith(token) ? full.slice(token.length) : full;
  return rest.replace(/^\s*·\s*/, "");
}
