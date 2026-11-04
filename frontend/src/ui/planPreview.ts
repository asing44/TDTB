/* planPreview — the Settings hub's read-only view of how Capacities items map
   to today's plan. Same rules the retired Connections preview used: an item's
   structure comes from its canonical `capacities:<space>:<structure>:<object>`
   identity (an explicit capacitiesStructureId wins); a tag in the saved
   exclusion list leaves it out; a structure with no mapping keeps it waiting. */

import { useEffect, useMemo, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { isCanonicalCapacitiesIdentity, missingGenerationWarnings } from "../adapters/wire";
import type {
  AssignedItem,
  CapacitiesSettings,
  TagExclusionSettings,
} from "../model/types";

export type PreviewReason = "shown" | "waiting" | "left out by tag";

export interface PreviewRow {
  title: string;
  reason: PreviewReason;
}

export type StructureHealth = "ok" | "needs-setup" | "off";

export interface StructureStatus {
  structureId: string;
  label: string;
  health: StructureHealth;
  line: string;
}

export interface PlanData {
  phase: "loading" | "ready" | "error";
  rows: PreviewRow[];
  shown: number;
  waiting: number;
  leftOut: number;
  structures: StructureStatus[];
  /** Mapped types the latest refresh has not published yet. */
  notYetRefreshed: number;
}

export function structureOf(item: AssignedItem): string {
  const explicit = (item.capacitiesStructureId ?? "").trim();
  if (explicit !== "") return explicit;
  const identity = item.capacitiesIdentity ?? item.identity ?? "";
  const parts = identity.split(":");
  return parts[0] === "capacities" && parts.length >= 4 ? parts[2] : "";
}

export function hasMapping(cap: CapacitiesSettings, structureId: string): boolean {
  const declaration = cap.types[structureId];
  if (!declaration) return false;
  if (declaration.mode === "off") return false;
  if (declaration.mode === "assigned" || declaration.mode === "assignedOn") {
    return (declaration.property ?? "") !== "";
  }
  return true;
}

const SHORT_ID_AFTER = 24;
export function structureLabel(cap: CapacitiesSettings, structureId: string): string {
  const title = cap.structureTitles[structureId];
  const short =
    structureId.length > SHORT_ID_AFTER ? `${structureId.slice(0, 8)}…` : structureId;
  return title && title !== structureId ? title : short;
}

/** One structure's health for the preview. Schema v2 has ONE rule per type, so
    "set up but turned off" and "never set up" are the same state: a type with
    no declaration (or mode `off`) admits nothing, and a property mode with no
    property picked is not set up yet either. */
export function structureStatus(cap: CapacitiesSettings, structureId: string): StructureStatus {
  const label = structureLabel(cap, structureId);
  if (!hasMapping(cap, structureId)) {
    return { structureId, label, health: "needs-setup", line: "Not set up yet — its items wait" };
  }
  return { structureId, label, health: "ok", line: "Set up and bringing items in" };
}

export function previewRows(
  items: readonly AssignedItem[],
  cap: CapacitiesSettings,
  tags: TagExclusionSettings,
): PreviewRow[] {
  const excluded = new Set(tags.tags.map((t) => t.tagId));
  const configured = new Set(cap.availableStructures);
  const out: PreviewRow[] = [];
  for (const item of items) {
    const structureId = structureOf(item);
    if (!configured.has(structureId)) continue;
    if ((item.tags ?? []).some((tag) => excluded.has(tag))) {
      out.push({ title: item.name, reason: "left out by tag" });
    } else if (!hasMapping(cap, structureId)) {
      out.push({ title: item.name, reason: "waiting" });
    } else {
      out.push({ title: item.name, reason: "shown" });
    }
  }
  return out;
}

/** Loads the saved Capacities settings each time `active` turns on and
    derives the preview from them, the saved tags passed in, plus the current read. */
export function usePlanData(active: boolean, tags: TagExclusionSettings | null): PlanData {
  const s = useAppState();
  const { controller } = useApp();
  const [cap, setCap] = useState<CapacitiesSettings | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    if (!active) return;
    let live = true;
    void controller
      .loadCapacitiesSettings()
      .then((loadedCap) => {
        if (!live) return;
        setCap(loadedCap);
        setPhase("ready");
      })
      .catch(() => {
        if (live) setPhase("error");
      });
    return () => {
      live = false;
    };
  }, [active, controller]);

  const assigned = s.inputs?.assigned;
  const warnings = s.inputs?.sourceWarnings;
  return useMemo<PlanData>(() => {
    if (!cap || !tags) {
      return { phase, rows: [], shown: 0, waiting: 0, leftOut: 0, structures: [], notYetRefreshed: 0 };
    }
    const seen = new Set<string>();
    const known = (assigned ?? []).filter((item) => {
      if (item.source !== "capacities") return false;
      const identity = item.capacitiesIdentity ?? item.identity ?? null;
      if (!isCanonicalCapacitiesIdentity(identity) || seen.has(identity as string)) return false;
      seen.add(identity as string);
      return true;
    });
    const rows = previewRows(known, cap, tags);
    return {
      phase,
      rows,
      shown: rows.filter((r) => r.reason === "shown").length,
      waiting: rows.filter((r) => r.reason === "waiting").length,
      leftOut: rows.filter((r) => r.reason === "left out by tag").length,
      structures: cap.availableStructures.map((id) => structureStatus(cap, id)),
      notYetRefreshed: missingGenerationWarnings(warnings ?? []).length,
    };
  }, [cap, tags, phase, assigned, warnings]);
}
