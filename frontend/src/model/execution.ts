import { liveDisplayName } from "./anchored";
import type {
  AnchoredBlock,
  OverlapGrant,
  PlanInputs,
  SequenceRow,
} from "./types";
import { toHHMM, toMinutes } from "./time";

export interface ExecutionEntry {
  id: string;
  name: string;
  start: string;
  end: string;
  kind: "work" | "anchored" | "calendar";
  immutable: boolean;
}

export interface ExecutionMoment {
  key: string;
  start: string;
  end: string;
  entries: ExecutionEntry[];
  allowedOverlap: boolean;
  overlapReason: string | null;
}

export interface ExecutionZone {
  id: string;
  name: string;
  start: string;
  end: string;
}

export interface WorkAllotmentUsage {
  totalMinutes: number;
  usedMinutes: number;
  remainingMinutes: number;
  overMinutes: number;
}

export interface ExecutionModel {
  moments: ExecutionMoment[];
  now: ExecutionMoment | null;
  next: ExecutionMoment | null;
  zones: ExecutionZone[];
  allotment: WorkAllotmentUsage;
}

function addMinutes(start: string, minutes: number): string {
  return toHHMM(toMinutes(start) + minutes);
}

function anchoredEnd(block: AnchoredBlock): string {
  if (block.kind === "calendar" && block.end) return block.end;
  return addMinutes(block.start as string, block.durationMin);
}

function entriesOf(inputs: PlanInputs, sequence: SequenceRow[] | null): ExecutionEntry[] {
  const entries: ExecutionEntry[] = [];
  for (const block of inputs.anchored) {
    if (!block.on || block.skipToday || !block.start || block.kind === "template") continue;
    entries.push({
      id: block.id,
      name: liveDisplayName(block.name, inputs.microAdventure),
      start: block.start,
      end: anchoredEnd(block),
      kind: block.kind === "calendar" ? "calendar" : "anchored",
      immutable: block.kind === "calendar",
    });
  }
  for (const row of sequence ?? []) {
    if (row.kind !== "work") continue;
    entries.push({
      id: row.id,
      name: row.id,
      start: row.start,
      end: row.end,
      kind: "work",
      immutable: false,
    });
  }
  return entries.sort((a, b) =>
    a.start.localeCompare(b.start) || a.end.localeCompare(b.end) || a.id.localeCompare(b.id),
  );
}

function zonesOf(inputs: PlanInputs, sequence: SequenceRow[] | null): ExecutionZone[] {
  const zones: ExecutionZone[] = [];
  for (const row of sequence ?? []) {
    if (row.kind !== "zone") continue;
    zones.push({ id: row.id, name: row.zone ?? row.id, start: row.start, end: row.end });
  }
  for (const block of inputs.anchored) {
    if (block.kind !== "template" || !block.on || block.skipToday || !block.start) continue;
    zones.push({
      id: block.id,
      name: block.name,
      start: block.start,
      end: block.end ?? addMinutes(block.start, block.durationMin),
    });
  }
  const seen = new Set<string>();
  return zones
    .filter((zone) => {
      const key = `${zone.name}\u0000${zone.start}\u0000${zone.end}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((a, b) => a.start.localeCompare(b.start) || a.name.localeCompare(b.name));
}

export function workAllotmentUsage(
  totalMinutes: number,
  zones: SequenceRow[],
  work: SequenceRow[],
): WorkAllotmentUsage {
  const zoneIntervals = zones
    .filter((row) => row.kind === "zone")
    .map((row) => [toMinutes(row.start), toMinutes(row.end)] as const);
  const intersections: Array<[number, number]> = [];
  for (const row of work.filter((candidate) => candidate.kind === "work")) {
    const start = toMinutes(row.start);
    const end = toMinutes(row.end);
    for (const [zoneStart, zoneEnd] of zoneIntervals) {
      const overlapStart = Math.max(start, zoneStart);
      const overlapEnd = Math.min(end, zoneEnd);
      if (overlapStart < overlapEnd) intersections.push([overlapStart, overlapEnd]);
    }
  }
  intersections.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged: Array<[number, number]> = [];
  for (const interval of intersections) {
    const last = merged.at(-1);
    if (!last || interval[0] > last[1]) merged.push([...interval]);
    else last[1] = Math.max(last[1], interval[1]);
  }
  const usedMinutes = merged.reduce((sum, [start, end]) => sum + end - start, 0);
  return {
    totalMinutes,
    usedMinutes,
    remainingMinutes: Math.max(0, totalMinutes - usedMinutes),
    overMinutes: Math.max(0, usedMinutes - totalMinutes),
  };
}

function momentsOf(
  entries: ExecutionEntry[],
  grants: OverlapGrant[],
  planningConfigFingerprint: string,
): ExecutionMoment[] {
  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  const clustered = new Set<string>();
  const moments: ExecutionMoment[] = [];

  for (const grant of grants) {
    if (grant.planningConfigFingerprint !== planningConfigFingerprint) continue;
    const primary = byId.get(grant.primaryId);
    const companion = byId.get(grant.companionId);
    if (!primary || !companion || clustered.has(primary.id) || clustered.has(companion.id)) continue;
    if (
      primary.start !== grant.primaryInterval.start || primary.end !== grant.primaryInterval.end ||
      companion.start !== grant.companionInterval.start || companion.end !== grant.companionInterval.end
    ) continue;
    clustered.add(primary.id);
    clustered.add(companion.id);
    moments.push({
      key: `grant:${primary.id}:${companion.id}`,
      start: primary.start < companion.start ? primary.start : companion.start,
      end: primary.end > companion.end ? primary.end : companion.end,
      entries: [primary, companion],
      allowedOverlap: true,
      overlapReason: grant.reason,
    });
  }
  for (const entry of entries) {
    if (clustered.has(entry.id)) continue;
    moments.push({
      key: `${entry.kind}:${entry.id}:${entry.start}`,
      start: entry.start,
      end: entry.end,
      entries: [entry],
      allowedOverlap: false,
      overlapReason: null,
    });
  }
  return moments.sort((a, b) => a.start.localeCompare(b.start) || a.key.localeCompare(b.key));
}

export function buildExecutionModel(args: {
  inputs: PlanInputs;
  sequence: SequenceRow[] | null;
  overlapGrants: OverlapGrant[];
  planningConfigFingerprint: string;
}): ExecutionModel {
  const entries = entriesOf(args.inputs, args.sequence);
  const zones = zonesOf(args.inputs, args.sequence);
  const moments = momentsOf(entries, args.overlapGrants, args.planningConfigFingerprint);
  const nowMinute = toMinutes(args.inputs.time.now);
  const now = moments.find((moment) =>
    toMinutes(moment.start) <= nowMinute && nowMinute < toMinutes(moment.end),
  ) ?? null;
  const next = moments.find((moment) =>
    moment !== now && toMinutes(moment.start) >= nowMinute,
  ) ?? null;
  const zoneRows: SequenceRow[] = zones.map((zone) => ({
    id: zone.id, start: zone.start, end: zone.end, zone: zone.name, kind: "zone",
  }));
  const workRows = (args.sequence ?? []).filter((row) => row.kind === "work");
  return {
    moments,
    now,
    next,
    zones,
    allotment: workAllotmentUsage(
      args.inputs.daySemantics.effectiveAllotmentMinutes,
      zoneRows,
      workRows,
    ),
  };
}
