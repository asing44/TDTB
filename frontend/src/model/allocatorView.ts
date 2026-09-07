/* allocatorView.ts — the pure display projection for today's work.

   Urgency remains the first scan axis. This module adds a quieter second axis:
   rows that share a parent/child relationship or a related tag travel together
   and carry a visible relationship marker. Groups are metadata only — no
   synthetic rows are created, so capacity, trim, and safety decisions still
   count the same AssignedItem objects exactly once. */

import { bandOf } from "./bands";
import { importanceKey } from "./allocator";
import type { AssignedItem, QueueState } from "./types";
import { queueState, type AppState } from "../store/store";

// Keep allocation arithmetic, inclusion, cumulative spend, and trim behavior
// on the existing store-owned seam. P6 adds relationship metadata only; it
// must not silently change capacity or safety decisions.
export {
  bandSpend,
  budgetTotal,
  includedDisplayOrder,
  localSelected,
  trimForState,
} from "../store/allocatorView";
import type { BandedRows } from "../store/allocatorView";
export type { BandedRows } from "../store/allocatorView";

export interface HierarchyRow {
  item: AssignedItem;
  /** Parent depth. A parent outside the current urgency band still gives its
      child depth 1, making cross-band relationships legible without moving
      rows out of the existing urgency sections. */
  depth: number;
  parentName: string | null;
  groupKey: string | null;
  groupLabel: string | null;
  groupStart: boolean;
}

export interface HierarchyBandedRows {
  crit: HierarchyRow[];
  high: HierarchyRow[];
  else: HierarchyRow[];
  scheduled: HierarchyRow[];
  excluded: HierarchyRow[];
}

function sourceIdentity(item: AssignedItem): string {
  return item.identity?.trim() || `${item.source}:${item.path ?? item.id}`;
}

function compareStrings(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function compareItems(a: AssignedItem, b: AssignedItem, today: string): number {
  const ka = importanceKey(a, today);
  const kb = importanceKey(b, today);
  for (let i = 0; i < ka.length; i += 1) {
    if (ka[i] < kb[i]) return -1;
    if (ka[i] > kb[i]) return 1;
  }
  return compareStrings(sourceIdentity(a), sourceIdentity(b));
}

function orderedItems(items: AssignedItem[], today: string): AssignedItem[] {
  return [...items].sort((a, b) => compareItems(a, b, today));
}

function normalized(value: string): string {
  return value.trim().replace(/\.md$/i, "").toLowerCase();
}

function relationNames(value: string | null | undefined): string[] {
  if (!value) return [];
  const raw = value.trim();
  const link = raw.match(/^\[\[([^\]|]+)(?:\|[^\]]+)?\]\]$/)?.[1] ?? raw;
  const withoutMd = link.replace(/\.md$/i, "");
  const leaf = withoutMd.split("/").pop() ?? withoutMd;
  return [raw, link, withoutMd, leaf].map(normalized).filter(Boolean);
}

function itemNames(item: AssignedItem): string[] {
  return [item.id, item.name, item.identity ?? "", item.path ?? ""]
    .flatMap((value) => {
      const normalizedValue = normalized(value);
      const leaf = normalizedValue.split("/").pop() ?? normalizedValue;
      return [normalizedValue, leaf];
    })
    .filter(Boolean);
}

function sharedTags(items: AssignedItem[]): Map<string, string[]> {
  const owners = new Map<string, string[]>();
  for (const item of items) {
    const itemTags = (item.tags ?? []).map((tag) => String(tag).trim()).filter(Boolean);
    for (const tag of new Set<string>(itemTags)) {
      const key = normalized(tag);
      const list = owners.get(key) ?? [];
      if (!list.includes(item.id)) list.push(item.id);
      owners.set(key, list);
    }
  }
  return new Map([...owners].filter(([, ids]) => ids.length > 1));
}

class DisjointSet {
  private readonly parents: number[];

  constructor(size: number) {
    this.parents = Array.from({ length: size }, (_, index) => index);
  }

  find(value: number): number {
    let root = value;
    while (this.parents[root] !== root) root = this.parents[root];
    while (this.parents[value] !== value) {
      const next = this.parents[value];
      this.parents[value] = root;
      value = next;
    }
    return root;
  }

  join(a: number, b: number): void {
    const left = this.find(a);
    const right = this.find(b);
    if (left !== right) this.parents[right] = left;
  }
}

interface RelationshipIndex {
  parentOf: Map<string, AssignedItem>;
  depthOf: Map<string, number>;
  componentOf: Map<string, string>;
  labelOf: Map<string, string>;
}

function buildRelationships(items: AssignedItem[], today: string): RelationshipIndex {
  const ordered = orderedItems(items, today);
  const indexById = new Map(ordered.map((item, index) => [item.id, index]));
  const byName = new Map<string, AssignedItem[]>();
  for (const item of ordered) {
    for (const name of itemNames(item)) {
      const matches = byName.get(name) ?? [];
      matches.push(item);
      byName.set(name, matches);
    }
  }

  const parentOf = new Map<string, AssignedItem>();
  for (const item of ordered) {
    const candidates = relationNames(item.relatesTo)
      .flatMap((name) => byName.get(name) ?? [])
      .filter((candidate) => candidate.id !== item.id);
    const parent = [...new Map(candidates.map((candidate) => [candidate.id, candidate])).values()]
      .sort((a, b) => compareItems(a, b, today))[0];
    if (parent) parentOf.set(item.id, parent);
  }

  const tags = sharedTags(ordered);
  const dsu = new DisjointSet(ordered.length);
  for (const child of ordered) {
    const parent = parentOf.get(child.id);
    if (parent) dsu.join(indexById.get(child.id)!, indexById.get(parent.id)!);
  }
  for (const ids of tags.values()) {
    const first = indexById.get(ids[0]);
    if (first == null) continue;
    for (const id of ids.slice(1)) {
      const next = indexById.get(id);
      if (next != null) dsu.join(first, next);
    }
  }

  const rootFor = (id: string): string => ordered[dsu.find(indexById.get(id)!)].id;
  const componentOf = new Map(ordered.map((item) => [item.id, rootFor(item.id)]));
  const depthOf = new Map<string, number>();
  const depth = (id: string, seen = new Set<string>()): number => {
    const known = depthOf.get(id);
    if (known != null) return known;
    if (seen.has(id)) return 0;
    const parent = parentOf.get(id);
    if (!parent) {
      depthOf.set(id, 0);
      return 0;
    }
    seen.add(id);
    const value = depth(parent.id, seen) + 1;
    depthOf.set(id, value);
    return value;
  };
  for (const item of ordered) depth(item.id);

  const tagByComponent = new Map<string, string>();
  for (const [tag, ids] of [...tags].sort(([a], [b]) => compareStrings(a, b))) {
    const component = componentOf.get(ids[0]);
    if (component && !tagByComponent.has(component)) tagByComponent.set(component, tag);
  }
  const labelOf = new Map<string, string>();
  for (const item of ordered) {
    const component = componentOf.get(item.id)!;
    if (labelOf.has(component)) continue;
    const tag = tagByComponent.get(component);
    if (tag) {
      labelOf.set(component, `Related · #${tag}`);
    } else {
      const root = ordered
        .filter((candidate) => componentOf.get(candidate.id) === component)
        .sort((a, b) =>
          (depthOf.get(a.id) ?? 0) - (depthOf.get(b.id) ?? 0) ||
          compareItems(a, b, today),
        )[0] ?? item;
      labelOf.set(component, `Group · ${root.name}`);
    }
  }

  return { parentOf, depthOf, componentOf, labelOf };
}

function orderComponentRows(
  rows: AssignedItem[],
  relationships: RelationshipIndex,
  baseIndex: Map<string, number>,
): AssignedItem[] {
  const byId = new Map(rows.map((item) => [item.id, item]));
  const children = new Map<string, AssignedItem[]>();
  for (const item of rows) {
    const parent = relationships.parentOf.get(item.id);
    if (!parent || !byId.has(parent.id)) continue;
    const siblings = children.get(parent.id) ?? [];
    siblings.push(item);
    children.set(parent.id, siblings);
  }
  const sortByBase = (a: AssignedItem, b: AssignedItem) =>
    (baseIndex.get(a.id) ?? 0) - (baseIndex.get(b.id) ?? 0) ||
    compareStrings(sourceIdentity(a), sourceIdentity(b));
  for (const siblings of children.values()) siblings.sort(sortByBase);

  const roots = rows
    .filter((item) => {
      const parent = relationships.parentOf.get(item.id);
      return !parent || !byId.has(parent.id);
    })
    .sort((a, b) => {
      const aHasChildren = children.has(a.id) ? 0 : 1;
      const bHasChildren = children.has(b.id) ? 0 : 1;
      return aHasChildren - bHasChildren || sortByBase(a, b);
    });
  const ordered: AssignedItem[] = [];
  const visited = new Set<string>();
  const visit = (item: AssignedItem) => {
    if (visited.has(item.id)) return;
    visited.add(item.id);
    ordered.push(item);
    for (const child of children.get(item.id) ?? []) visit(child);
  };
  for (const root of roots) visit(root);
  // Malformed/cyclic relationships must not hide a row. Finish any item that
  // was not reachable from a visible root in deterministic source order.
  for (const item of [...rows].sort(sortByBase)) visit(item);
  return ordered;
}

/** Order a requested slice while resolving relationships against the complete
    assigned set. This keeps urgency bands primary but prevents a child from
    appearing above its parent when both are in the same band. */
export function hierarchyRows(
  items: AssignedItem[],
  today: string,
  contextItems: AssignedItem[] = items,
): HierarchyRow[] {
  const relationships = buildRelationships(contextItems, today);
  const base = orderedItems(items, today);
  const baseIndex = new Map(base.map((item, index) => [item.id, index]));
  const components = new Map<string, AssignedItem[]>();
  for (const item of base) {
    const key = relationships.componentOf.get(item.id) ?? item.id;
    const rows = components.get(key) ?? [];
    rows.push(item);
    components.set(key, rows);
  }
  const ordered: AssignedItem[] = [];
  for (const rows of [...components.values()].sort((a, b) => {
    return (baseIndex.get(a[0].id) ?? 0) - (baseIndex.get(b[0].id) ?? 0);
  })) {
    ordered.push(...orderComponentRows(rows, relationships, baseIndex));
  }

  const firstByComponent = new Set<string>();
  return ordered.map((item) => {
    const component = relationships.componentOf.get(item.id);
    const grouped = component != null && (relationships.labelOf.has(component) &&
      ((relationships.depthOf.get(item.id) ?? 0) > 0 ||
        contextItems.filter((candidate) => relationships.componentOf.get(candidate.id) === component).length > 1));
    const groupKey = grouped ? component! : null;
    const groupStart = groupKey != null && !firstByComponent.has(groupKey);
    if (groupKey) firstByComponent.add(groupKey);
    return {
      item,
      depth: groupKey ? relationships.depthOf.get(item.id) ?? 0 : 0,
      parentName: relationships.parentOf.get(item.id)?.name ?? null,
      groupKey,
      groupLabel: groupKey ? relationships.labelOf.get(groupKey) ?? null : null,
      groupStart,
    };
  });
}

function hierarchyBuckets(s: AppState): HierarchyBandedRows {
  const empty: HierarchyBandedRows = { crit: [], high: [], else: [], scheduled: [], excluded: [] };
  if (!s.inputs) return empty;
  const today = s.inputs.validDate;
  const context = orderedItems(s.inputs.assigned, today);
  const buckets: Record<keyof HierarchyBandedRows, AssignedItem[]> = {
    crit: [], high: [], else: [], scheduled: [], excluded: [],
  };
  for (const item of context) {
    const state: QueueState = queueState(s, item.id);
    if (state === "needs-placement" || state === "background") buckets[bandOf(item)].push(item);
    else buckets[state].push(item);
  }
  return {
    crit: hierarchyRows(buckets.crit, today, context),
    high: hierarchyRows(buckets.high, today, context),
    else: hierarchyRows(buckets.else, today, context),
    scheduled: hierarchyRows(buckets.scheduled, today, context),
    excluded: hierarchyRows(buckets.excluded, today, context),
  };
}

export function hierarchyBandedRows(s: AppState): HierarchyBandedRows {
  return hierarchyBuckets(s);
}

/** Compatibility projection for callers that only need the existing band
    axis. Hierarchy keeps ordering semantics (parent before its child within
    the same band); this drops the metadata and returns plain items, so the
    flat band contract stays byte-shape-compatible with the store seam. */
export function bandedRows(s: AppState): BandedRows {
  const groups = hierarchyBuckets(s);
  const strip = (rows: HierarchyRow[]): AssignedItem[] => rows.map((entry) => entry.item);
  return {
    crit: strip(groups.crit),
    high: strip(groups.high),
    else: strip(groups.else),
    scheduled: strip(groups.scheduled),
    excluded: strip(groups.excluded),
  };
}
