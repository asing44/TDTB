/* wire.ts — pure wire↔model mapping for the production API adapter (T5).
   One function per endpoint response, pinned by contract-fixture tests
   (src/adapters/contract-fixtures/*.json — captured from the real FastAPI
   routes, sanitized by construction). The assigned-only projection lives
   here: digest.suggested never crosses into component state (locked
   decision 2). Raw wire payloads stay adapter-internal for POST bodies.

   Allocator-rewrite T6 revises that constraint deliberately, for exactly two
   fields: digest.unassigned_candidates and digest.stale_assigned DO cross, as
   locked decision 8 makes the forgot-strip a first-class load-time surface.
   They are server-capped {name, path, reason} summaries — the ranked pool
   still never crosses, and projectPlanInputs still drops digest.suggested. */

import type {
  AnchoredBlock,
  AnchoredKind,
  AssignedItem,
  Capacity,
  CapacitiesCoverage,
  CapacitiesLimit,
  CapacitiesSettings,
  CapacitiesSettingsDraft,
  CapacitiesNativeTaskAutoPolicy,
  CommitReport,
  CommitSurface,
  DaySetup,
  DurationSourceLabel,
  FixedInputs,
  ForgotItem,
  Ledger,
  PlanInputs,
  SequenceRow,
  TagCatalog,
  TagCatalogTag,
  TagExclusionIdentity,
  TagExclusionSettings,
  TagExclusionSettingsDraft,
  ShadowDiff,
  ShadowClassification,
  ShadowEntry,
  SourceHealth,
  Validation,
  ValidationDiagnostic,
  DayPreset,
  DaySemantics,
  MicroAdventure,
  MicroIdea,
  MintSession,
  OverlapGrant,
  SchedulableOverride,
} from "../model/types";
import { blocksLabel } from "../model/time";
import type {
  DurationMemoryResetResult,
  DurationMemorySaveResult,
} from "./adapter";

// Wire payloads are untyped JSON — this alias marks the boundary.
export type Wire = Record<string, any>;

// -- time helpers ------------------------------------------------------------

/** "7:45 AM" | "12:00 PM" | "09:15" → "HH:MM" 24h; null when unparseable. */
export function to24h(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const m = value.trim().match(/^(\d{1,2}):(\d{2})(?:\s*(AM|PM))?$/i);
  if (!m) return null;
  let h = Number(m[1]);
  const min = Number(m[2]);
  if (min > 59) return null;
  const ap = m[3]?.toUpperCase();
  if (ap) {
    if (h < 1 || h > 12) return null;
    if (ap === "PM" && h !== 12) h += 12;
    if (ap === "AM" && h === 12) h = 0;
  } else if (h > 23) {
    return null;
  }
  return `${String(h).padStart(2, "0")}:${String(min).padStart(2, "0")}`;
}

/** "80m" | "1h20m" | "2h" | bare minutes → minutes; null when unparseable. */
export function durationMinutes(value: unknown): number | null {
  if (value == null) return null;
  if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
  const s = String(value).trim();
  const hm = s.match(/(\d+)\s*h/i);
  const mm = s.match(/(\d+)\s*m/i);
  if (hm || mm) return (hm ? Number(hm[1]) : 0) * 60 + (mm ? Number(mm[1]) : 0);
  return /^\d+(\.\d+)?$/.test(s) ? Math.trunc(Number(s)) : null;
}

function minutesOf(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

// Keep the historical adapter export for callers while sharing the one
// duration formatter with the UI and the other wire projections.
export { blocksLabel } from "../model/time";

// -- duration memory (MVP) ---------------------------------------------------

/** Coerce the backend resolver's source_label onto the model's closed set.
    Unknown or absent labels fail OPEN to "default" (source-resolved) so
    legacy payloads without duration-memory metadata behave exactly like no
    remembered duration. */
export function durationSourceOf(label: unknown): DurationSourceLabel {
  const s = String(label ?? "");
  if (s === "remembered") return "remembered";
  if (s === "native") return "native";
  if (s === "preset") return "preset";
  if (s === "type") return "type";
  if (s.startsWith("tag:")) return "tag";
  return "default";
}

/** Canonical stable source identity for a row (mirrors the backend
    item_identity rule): todoist:<id> for todoist rows, the normalized vault
    path otherwise, null when neither is present. A display name alone is
    never an identity. */
export function itemIdentity(item: {
  source?: string;
  identity?: string | null;
  todoistId?: string | null;
  path?: string | null;
}): string | null {
  if (item.source === "capacities" && item.identity) return String(item.identity);
  if (item.source === "todoist" && item.todoistId) return `todoist:${item.todoistId}`;
  if (item.path && !String(item.path).startsWith("todoist://")) return String(item.path);
  return null;
}

/** Exact minutes from a duration-memory wire payload: null when absent,
    null, or non-finite — a missing reset fallback (found:false) must never
    coerce to zero (FT-05 F2). */
function projectDurationMemoryMinutes(wire: Wire): number | null {
  const raw = wire.duration_minutes ?? wire.minutes;
  if (raw == null) return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

function projectDurationMemoryResult(wire: Wire): {
  identity: string;
  minutes: number | null;
  source: DurationSourceLabel;
} {
  return {
    identity: String(wire.identity ?? ""),
    // Reset returns `duration_minutes` (FT-01 backend); save returns
    // `minutes`. Read both so the two routes share one projection.
    minutes: projectDurationMemoryMinutes(wire),
    source: durationSourceOf(wire.duration_source ?? (wire.source ?? "default")),
  };
}

/** Stable Capacities identities are the only object keys the settings route
    accepts. Keep this check at the frontend boundary too, so a malformed
    response or a hand-built fixture cannot become a title/id exclusion. */
export function isCanonicalCapacitiesIdentity(value: unknown): value is string {
  if (typeof value !== "string" || value !== value.trim()) return false;
  const parts = value.split(":");
  return (
    parts.length >= 4 &&
    parts[0] === "capacities" &&
    parts.slice(1).every((part) => part.length > 0 && part === part.trim())
  );
}

const CAPACITIES_SETTINGS_NATIVE_KEYS = [
  "active_enabled",
  "due_enabled",
  "deadline_enabled",
  "deadline_horizon_days",
] as const;

/** Validate and return an Active-enabled custom structure id, mirroring the
    backend's ``canonical_active_structure_id``: a non-empty, whitespace-free
    string with no leading or trailing whitespace. */
export function isCanonicalCapacitiesStructureId(value: unknown): value is string {
  if (typeof value !== "string" || value.length === 0) return false;
  return value === value.trim() && !/\s/.test(value);
}

function capacitiesSettingsError(detail: string): Error {
  return new Error(`invalid Capacities settings response: ${detail}`);
}

/** One configured admission value, mirroring the backend's
    ``canonical_admission_value``: a non-empty string kept exactly as given
    (a status name may legitimately contain whitespace). */
function isCanonicalAdmissionValue(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/** Project one plain admission list (``native_task_structures`` /
    ``active_statuses``) — a strict array of non-empty strings, deduplicated
    and sorted like the advisory ``available_structures`` inventory. */
function projectAdmissionValues(raw: unknown, key: string): string[] {
  if (!Array.isArray(raw)) {
    throw capacitiesSettingsError(`${key} must be an array`);
  }
  const values = raw.map((value) => {
    if (!isCanonicalAdmissionValue(value)) {
      throw capacitiesSettingsError(`${key} contains an invalid value`);
    }
    return value;
  });
  return [...new Set(values)].sort();
}

/** Project the strict local settings response. The backend is fail-closed;
    the client must not turn a malformed response into a plausible default. */
export function projectCapacitiesSettings(wire: Wire): CapacitiesSettings {
  const raw = wire?.settings;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw capacitiesSettingsError("missing settings object");
  }
  if (typeof wire.persisted !== "boolean") {
    throw capacitiesSettingsError("persisted must be a boolean");
  }
  if (raw.version !== 1) {
    throw capacitiesSettingsError("version is unsupported");
  }
  if (typeof raw.revision !== "number" || !Number.isSafeInteger(raw.revision) || raw.revision < 0) {
    throw capacitiesSettingsError("revision must be a nonnegative safe integer");
  }
  const native = raw.native_task_auto;
  if (!native || typeof native !== "object" || Array.isArray(native)) {
    throw capacitiesSettingsError("native_task_auto is malformed");
  }
  if (
    Object.keys(native).some((key) => !(CAPACITIES_SETTINGS_NATIVE_KEYS as readonly string[]).includes(key)) ||
    CAPACITIES_SETTINGS_NATIVE_KEYS.some((key) => !Object.prototype.hasOwnProperty.call(native, key))
  ) {
    throw capacitiesSettingsError("native_task_auto has unknown or missing keys");
  }
  const boolKeys = ["active_enabled", "due_enabled", "deadline_enabled"] as const;
  for (const key of boolKeys) {
    if (typeof native[key] !== "boolean") {
      throw capacitiesSettingsError(`${key} must be a boolean`);
    }
  }
  if (
    typeof native.deadline_horizon_days !== "number" ||
    !Number.isSafeInteger(native.deadline_horizon_days) ||
    native.deadline_horizon_days < 0
  ) {
    throw capacitiesSettingsError("deadline_horizon_days must be a nonnegative integer");
  }
  const excluded = raw.excluded;
  if (!excluded || typeof excluded !== "object" || Array.isArray(excluded)) {
    throw capacitiesSettingsError("excluded must be an object");
  }
  const identities = Object.entries(excluded).map(([identity, flag]) => {
    if (!isCanonicalCapacitiesIdentity(identity) || flag !== true) {
      throw capacitiesSettingsError("excluded contains an invalid identity");
    }
    return identity;
  });
  // ``active_structures`` was added additively to schema version 1: an older
  // backend/response that omits the key means the empty set. When present it
  // is strict, matching the excluded-object treatment above.
  const activeRaw = raw.active_structures;
  let activeStructures: string[] = [];
  if (activeRaw !== undefined) {
    if (!activeRaw || typeof activeRaw !== "object" || Array.isArray(activeRaw)) {
      throw capacitiesSettingsError("active_structures must be an object");
    }
    activeStructures = Object.entries(activeRaw).map(([structureId, flag]) => {
      if (!isCanonicalCapacitiesStructureId(structureId) || flag !== true) {
        throw capacitiesSettingsError("active_structures contains an invalid structure id");
      }
      return structureId;
    });
  }
  // ``native_task_structures`` and ``active_statuses`` were added additively
  // to schema version 1 with the same optional-on-read rule as
  // ``active_structures``. Their documented defaults are non-empty (the store
  // reads a legacy file that way), so an absent key projects the default
  // rather than the dangerous empty set.
  const nativeRaw = raw.native_task_structures;
  const nativeTaskStructures =
    nativeRaw === undefined
      ? ["RootTask", "Task"]
      : projectAdmissionValues(nativeRaw, "native_task_structures");
  const statusRaw = raw.active_statuses;
  const activeStatuses =
    statusRaw === undefined
      ? ["active"]
      : projectAdmissionValues(statusRaw, "active_statuses");
  // ``assigned_structures`` is optional on read like the admission keys; an
  // absent key means no declarations. When present it is strict: canonical
  // structure ids with non-empty opaque property ids kept exactly as stored.
  const assignedRaw = raw.assigned_structures;
  let assignedStructures: Record<string, string> = {};
  if (assignedRaw !== undefined) {
    if (!assignedRaw || typeof assignedRaw !== "object" || Array.isArray(assignedRaw)) {
      throw capacitiesSettingsError("assigned_structures must be an object");
    }
    for (const [structureId, propertyId] of Object.entries(assignedRaw)) {
      if (!isCanonicalCapacitiesStructureId(structureId) || !isCanonicalAdmissionValue(propertyId)) {
        throw capacitiesSettingsError("assigned_structures contains an invalid declaration");
      }
      assignedStructures[structureId] = propertyId;
    }
  }
  // ``available_structures`` is read-only advisory metadata added at the top
  // level of the settings response. It is tolerant when absent (an older
  // backend) and strict when present.
  const availableRaw = wire.available_structures;
  let availableStructures: string[] = [];
  if (availableRaw !== undefined) {
    if (!Array.isArray(availableRaw)) {
      throw capacitiesSettingsError("available_structures must be an array");
    }
    const usable = availableRaw.map((structureId) => {
      if (!isCanonicalCapacitiesStructureId(structureId)) {
        throw capacitiesSettingsError("available_structures contains an invalid structure id");
      }
      return structureId;
    });
    availableStructures = [...new Set(usable)].sort();
  }
  return {
    version: raw.version,
    revision: raw.revision,
    persisted: wire.persisted,
    nativeTaskAuto: {
      activeEnabled: native.active_enabled,
      dueEnabled: native.due_enabled,
      deadlineEnabled: native.deadline_enabled,
      deadlineHorizonDays: native.deadline_horizon_days,
    },
    excluded: identities.sort(),
    activeStructures: activeStructures.sort(),
    nativeTaskStructures,
    activeStatuses,
    assignedStructures,
    availableStructures,
  };
}

/** Build the full-replacement body expected by POST /settings/capacities/save. */
export function capacitiesSettingsToWire(draft: CapacitiesSettingsDraft): Wire {
  if (!Number.isSafeInteger(draft.expectedRevision) || draft.expectedRevision < 0) {
    throw new Error("expectedRevision must be a nonnegative safe integer");
  }
  const policy: CapacitiesNativeTaskAutoPolicy = draft.nativeTaskAuto;
  if (
    typeof policy.activeEnabled !== "boolean" ||
    typeof policy.dueEnabled !== "boolean" ||
    typeof policy.deadlineEnabled !== "boolean" ||
    !Number.isSafeInteger(policy.deadlineHorizonDays) ||
    policy.deadlineHorizonDays < 0
  ) {
    throw new Error("native Capacities Task Auto policy is malformed");
  }
  const identities = [...new Set(draft.excluded)].sort();
  if (!identities.every((identity) => isCanonicalCapacitiesIdentity(identity))) {
    throw new Error("excluded contains an invalid Capacities identity");
  }
  const activeStructures = [...new Set(draft.activeStructures)].sort();
  if (!activeStructures.every((structureId) => isCanonicalCapacitiesStructureId(structureId))) {
    throw new Error("activeStructures contains an invalid structure id");
  }
  // The admission keys are plain string lists, and every save carries both:
  // omission would make the full-replacement route reset them to the
  // documented defaults instead of leaving them unchanged.
  const nativeTaskStructures = [...new Set(draft.nativeTaskStructures)].sort();
  if (!nativeTaskStructures.every(isCanonicalAdmissionValue)) {
    throw new Error("nativeTaskStructures contains an invalid value");
  }
  const activeStatuses = [...new Set(draft.activeStatuses)].sort();
  if (!activeStatuses.every(isCanonicalAdmissionValue)) {
    throw new Error("activeStatuses contains an invalid value");
  }
  // The declaration map is always emitted too: the full-replacement route
  // replaces it wholesale, so omission would wipe every declaration.
  const assignedEntries = Object.entries(draft.assignedStructures);
  for (const [structureId, propertyId] of assignedEntries) {
    if (!isCanonicalCapacitiesStructureId(structureId) || !isCanonicalAdmissionValue(propertyId)) {
      throw new Error("assignedStructures contains an invalid declaration");
    }
  }
  const assignedStructures = Object.fromEntries(
    assignedEntries.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
  );
  return {
    expected_revision: draft.expectedRevision,
    native_task_auto: {
      active_enabled: policy.activeEnabled,
      due_enabled: policy.dueEnabled,
      deadline_enabled: policy.deadlineEnabled,
      deadline_horizon_days: policy.deadlineHorizonDays,
    },
    excluded: Object.fromEntries(identities.map((identity) => [identity, true])),
    active_structures: Object.fromEntries(activeStructures.map((structureId) => [structureId, true])),
    native_task_structures: nativeTaskStructures,
    active_statuses: activeStatuses,
    assigned_structures: assignedStructures,
  };
}

// -- tag exclusion policy (stable-identity tag filter) -----------------------

const TAG_CATALOG_STATUSES = ["complete", "partial", "unavailable", "unconfigured"] as const;
const TAG_ENTRY_KEYS = ["source", "space_id", "tag_id"] as const;

/** Canonical UUID tag ids only — the same strictness the backend store
    applies. A non-canonical spelling is a different identity and never
    silently normalized. */
export function isCanonicalTagId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)
  );
}

/** The empty catalog used when a save response (which carries no catalog)
    must be projected without a previously loaded inventory. */
export function emptyTagCatalog(): TagCatalog {
  return { status: "unconfigured", spaceId: null, tags: [], warnings: [] };
}

function tagExclusionError(detail: string): Error {
  return new Error(`invalid tag exclusion settings response: ${detail}`);
}

function tagIdentityKey(identity: TagExclusionIdentity): string {
  return `${identity.source}:${identity.spaceId}:${identity.tagId}`;
}

function projectTagCatalog(raw: unknown): TagCatalog {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw tagExclusionError("tag_catalog is malformed");
  }
  const catalog = raw as Wire;
  const status = catalog.status;
  if (!(TAG_CATALOG_STATUSES as readonly string[]).includes(status)) {
    throw tagExclusionError("tag_catalog status is unsupported");
  }
  const spaceRaw = catalog.space_id;
  if (spaceRaw !== null && spaceRaw !== undefined && typeof spaceRaw !== "string") {
    throw tagExclusionError("tag_catalog space_id is malformed");
  }
  const spaceId = typeof spaceRaw === "string" && spaceRaw !== "" ? spaceRaw : null;
  if (!Array.isArray(catalog.tags)) {
    throw tagExclusionError("tag_catalog tags must be an array");
  }
  const tags: TagCatalogTag[] = catalog.tags.map((row: unknown) => {
    if (!row || typeof row !== "object" || Array.isArray(row)) {
      throw tagExclusionError("tag_catalog contains a malformed tag row");
    }
    const tag = row as Wire;
    if (typeof tag.id !== "string" || tag.id === "" || typeof tag.title !== "string" || tag.title === "") {
      throw tagExclusionError("tag_catalog tag rows require id and title");
    }
    return { id: tag.id, title: tag.title };
  });
  if (!Array.isArray(catalog.warnings)) {
    throw tagExclusionError("tag_catalog warnings must be an array");
  }
  return {
    status: status as TagCatalog["status"],
    spaceId,
    tags,
    warnings: catalog.warnings.map((warning: unknown) => String(warning)),
  };
}

/** Project the strict local tag-exclusion settings response. The backend is
    fail-closed; the client must not turn a malformed response into a
    plausible default. A response without ``tag_catalog`` (the save route)
    uses the caller-supplied previously loaded catalog, or the empty one. */
export function projectTagExclusionSettings(
  wire: Wire,
  catalog?: TagCatalog,
): TagExclusionSettings {
  const raw = wire?.settings;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw tagExclusionError("missing settings object");
  }
  if (typeof wire.persisted !== "boolean") {
    throw tagExclusionError("persisted must be a boolean");
  }
  if (raw.version !== 1) {
    throw tagExclusionError("version is unsupported");
  }
  if (typeof raw.revision !== "number" || !Number.isSafeInteger(raw.revision) || raw.revision < 0) {
    throw tagExclusionError("revision must be a nonnegative safe integer");
  }
  const exclusions = raw.exclusions;
  if (!exclusions || typeof exclusions !== "object" || Array.isArray(exclusions)) {
    throw tagExclusionError("exclusions must be an object");
  }
  const dimensionKeys = Object.keys(exclusions);
  if (dimensionKeys.length !== 1 || dimensionKeys[0] !== "tags") {
    throw tagExclusionError("exclusions has an unknown or missing dimension");
  }
  if (!Array.isArray(exclusions.tags)) {
    throw tagExclusionError("exclusions.tags must be an array");
  }
  const tags: TagExclusionIdentity[] = exclusions.tags.map((entry: unknown) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw tagExclusionError("exclusions.tags contains a malformed entry");
    }
    const row = entry as Wire;
    const keys = Object.keys(row).sort().join(",");
    if (keys !== [...TAG_ENTRY_KEYS].sort().join(",")) {
      throw tagExclusionError("tag entries have unknown or missing keys");
    }
    if (row.source !== "capacities") {
      throw tagExclusionError("tag entry source is unsupported");
    }
    if (
      typeof row.space_id !== "string" ||
      row.space_id === "" ||
      row.space_id !== row.space_id.trim() ||
      /\s/.test(row.space_id)
    ) {
      throw tagExclusionError("tag entry space_id is malformed");
    }
    if (!isCanonicalTagId(row.tag_id)) {
      throw tagExclusionError("tag entry tag_id is not a canonical UUID");
    }
    return { source: "capacities" as const, spaceId: row.space_id, tagId: row.tag_id };
  });
  const seen = new Set<string>();
  for (const tag of tags) {
    const key = tagIdentityKey(tag);
    if (seen.has(key)) {
      throw tagExclusionError("duplicate tag exclusion identity");
    }
    seen.add(key);
  }
  tags.sort((a, b) => (tagIdentityKey(a) < tagIdentityKey(b) ? -1 : 1));
  return {
    version: raw.version,
    revision: raw.revision,
    persisted: wire.persisted,
    tags,
    catalog:
      wire.tag_catalog !== undefined
        ? projectTagCatalog(wire.tag_catalog)
        : (catalog ?? emptyTagCatalog()),
  };
}

/** Build the full-replacement body expected by POST /settings/exclusions/save. */
export function tagExclusionSettingsToWire(draft: TagExclusionSettingsDraft): Wire {
  if (!Number.isSafeInteger(draft.expectedRevision) || draft.expectedRevision < 0) {
    throw new Error("expectedRevision must be a nonnegative safe integer");
  }
  const tags = draft.tags.map((tag) => {
    if (tag.source !== "capacities") {
      throw new Error("tag exclusion source is unsupported");
    }
    if (
      typeof tag.spaceId !== "string" ||
      tag.spaceId === "" ||
      tag.spaceId !== tag.spaceId.trim() ||
      /\s/.test(tag.spaceId)
    ) {
      throw new Error("tag exclusion spaceId is malformed");
    }
    if (!isCanonicalTagId(tag.tagId)) {
      throw new Error("tag exclusion tagId is not a canonical UUID");
    }
    return { source: tag.source, space_id: tag.spaceId, tag_id: tag.tagId };
  });
  const seen = new Set<string>();
  for (const tag of tags) {
    const key = `${tag.source}:${tag.space_id}:${tag.tag_id}`;
    if (seen.has(key)) {
      throw new Error("duplicate tag exclusion identity");
    }
    seen.add(key);
  }
  return {
    expected_revision: draft.expectedRevision,
    exclusions: { tags },
  };
}

/** A successful save makes the row durable-remembered by definition — the
    client owns the success label even if the wire omits the echo. The save
    route always echoes the exact persisted minutes. */
export function projectDurationMemorySave(wire: Wire): DurationMemorySaveResult {
  const base = projectDurationMemoryResult(wire);
  return { identity: base.identity, minutes: base.minutes ?? 0, source: "remembered" };
}

/** Reset returns the current source-resolved fallback and its label, or
    minutes null when no fallback exists (found:false) — the caller then
    preserves authoritative state instead of applying zero. */
export function projectDurationMemoryReset(wire: Wire): DurationMemoryResetResult {
  return projectDurationMemoryResult(wire);
}

// -- per-endpoint projections ------------------------------------------------

export function projectAssigned(row: Wire): AssignedItem {
  const source =
    row.source === "todoist"
      ? "todoist"
      : row.source === "capacities"
        ? "capacities"
        : "vault";
  const blocks =
    typeof row.blocks === "number" && Number.isFinite(row.blocks) ? row.blocks : 1;
  // FT-05 F1: exact remembered minutes win for the user-visible label —
  // 45 minutes renders "45min" even if a stale payload carries a
  // 30-minute-grid-rounded blocks value. Absent minutes fall back to the
  // block grid (legacy payloads).
  const exactMinutes =
    typeof row.duration_minutes === "number" && Number.isFinite(row.duration_minutes)
      ? row.duration_minutes
      : null;
  return {
    id: String(row.name),
    name: String(row.name),
    path: source === "todoist" ? null : (row.path ?? null),
    source,
    types: Array.isArray(row.types) ? row.types.map(String) : [],
    urgency: row.urgency == null ? null : String(row.urgency),
    deadline: row.deadline ?? null,
    priorityScore: Number(row.priority_score ?? 0),
    blocks,
    durationLabel:
      exactMinutes != null ? blocksLabel(exactMinutes / 30) : blocksLabel(blocks),
    todoistId:
      source === "todoist" && row.todoist_id != null && String(row.todoist_id) !== ""
        ? String(row.todoist_id)
        : null,
    // Duration memory (MVP): the canonical identity the mutation API keys on
    // plus the resolver's source label. Absent/unknown wire labels project to
    // "default" so legacy payloads keep source-resolved behavior.
    identity: itemIdentity({
      source,
      identity: row.identity ?? null,
      todoistId:
        source === "todoist" && row.todoist_id != null && String(row.todoist_id) !== ""
          ? String(row.todoist_id)
          : null,
      path: row.path ?? null,
    }),
    durationSource: durationSourceOf(row.duration_source),
    isRecurring: source === "todoist" && row.is_recurring === true,
    scheduledStart: source === "todoist" ? to24h(row.scheduled_start) : null,
    labels: Array.isArray(row.labels) ? row.labels.map(String) : [],
    tags: Array.isArray(row.tags) ? row.tags.map(String) : [],
    relatesTo: row.relates_to == null ? null : String(row.relates_to),
  };
}

export function projectAnchored(row: Wire): AnchoredBlock {
  const name = String(row.Block ?? row.Start ?? "(unnamed)");
  const kind: AnchoredKind =
    row.source === "calendar"
      ? "calendar"
      : row.Type === "window"
        ? row.overlap_allowed === true || String(row.overlap_allowed).toLowerCase() === "yes"
          ? "template"
          : "window"
        : "hard";
  const start = to24h(row.time) ?? to24h(row.Start);
  const end = to24h(row.End);
  let durationMin = durationMinutes(row.Duration) ?? 0;
  if (!durationMin && start && end) {
    durationMin = (minutesOf(end) - minutesOf(start) + 1440) % 1440;
  }
  return {
    id: name,
    name,
    kind,
    start,
    end,
    durationMin,
    overlapAllowed:
      row.overlap_allowed === true ||
      String(row.overlap_allowed ?? "").toLowerCase() === "yes",
    on: row.on !== false && row.skip_today !== true,
    skipToday: row.skip_today === true,
    calendarId:
      row.source === "calendar" && row.calendar_id != null
        ? String(row.calendar_id)
        : null,
    calendarTitle:
      row.source === "calendar" && row.calendar_title != null
        ? String(row.calendar_title)
        : null,
    capacityClass:
      row.source === "calendar" &&
      (row.capacity_class === "work" ||
        row.capacity_class === "ignored" ||
        row.capacity_class === "quarantined")
        ? row.capacity_class
        : row.source === "calendar"
          ? "fixed"
          : undefined,
  };
}

/** Two routes emit capacity in two shapes: /plan-inputs flattens the segments
    onto the capacity object, /capacity-preview nests them under `segments`.
    Read both, and coerce — a missing key must land as 0, never undefined.

    This was a live defect before T7/T8 and invisible: only `mint` carried the
    fallback, so after ANY duration change the other five segments became
    undefined, the segmented bar silently emptied, and the chips row filtered
    itself away on `> 0`. The server's `remaining` string stayed correct, so
    nothing looked wrong. T7's live readout computes FROM these numbers, which
    turned the silent hole into a visible "NaN blk". */
function segment(wire: Wire, key: string): number {
  return Number(wire[key] ?? wire.segments?.[key] ?? 0);
}

export function projectCapacity(wire: Wire): Capacity {
  return {
    total: Number(wire.total ?? 0),
    fixed: segment(wire, "fixed"),
    anchored: segment(wire, "anchored"),
    habits: segment(wire, "habits"),
    mint: segment(wire, "mint"),
    selected: segment(wire, "selected"),
    buffer: segment(wire, "buffer"),
    free: Number(wire.free ?? 0),
    overassigned: wire.overassigned === true,
    availableForSelection: Number(wire.available_for_selection ?? 0),
    remaining: String(wire.remaining ?? ""),
    ratio: String(wire.ratio ?? ""),
    legend: String(wire.legend ?? ""),
    counters: String(wire.counters ?? ""),
    workBusy: Number(wire.work_busy ?? wire.workBusy ?? 0),
    workOverflow: Number(wire.work_overflow ?? wire.workOverflow ?? 0),
  };
}

export function projectLedger(wire: Wire): Ledger {
  return {
    today: String(wire.today),
    spent: Number(wire.spent),
    cap: Number(wire.cap),
    remaining: Number(wire.remaining),
  };
}

export function projectDaySetup(wire: Wire, confirmed: boolean): DaySetup {
  const anchored: DaySetup["anchored"] = {};
  for (const o of wire.anchored ?? []) {
    if (o && o.id != null) {
      anchored[String(o.id)] = {
        on: o.on === true,
        skipToday: o.skip_today === true,
        time: to24h(o.time),
        blocks:
          typeof o.blocks === "number" && Number.isFinite(o.blocks)
            ? Math.max(0, Math.trunc(o.blocks))
            : null,
      };
    }
  }
  const buffering =
    wire.buffering === "minimal" || wire.buffering === "off"
      ? wire.buffering
      : "standard";
  const schedulable: Record<string, SchedulableOverride> = {};
  if (wire.schedulable && typeof wire.schedulable === "object") {
    for (const [key, raw] of Object.entries(wire.schedulable)) {
      if (!raw || typeof raw !== "object") continue;
      const value = raw as Wire;
      schedulable[key] = {
        ...(typeof value.on === "boolean" ? { on: value.on } : {}),
        ...(typeof value.n === "number" && Number.isFinite(value.n)
          ? { n: value.n }
          : {}),
        ...(Array.isArray(value.sessions)
          ? { sessions: value.sessions.map(String) }
          : {}),
      };
    }
  }
  const result: DaySetup = {
    anchor: to24h(wire.anchor),
    eod: to24h(wire.eod),
    buffering,
    anchored,
    captures: {
      intention: String(wire.intention ?? ""),
      forMeegy: String(wire.megan_nicety ?? ""),
      stoic: String(wire.stoic_intention ?? ""),
    },
    // FEEDBACK-24: confirmation is the server's explicit flag
    // (day_setup_confirmed on /plan-inputs), never inferred from "any key
    // echoed" — a skeleton runstate can echo schedulable/anchor keys without
    // the user ever confirming Day Setup.
    confirmed,
  };
  if (Object.prototype.hasOwnProperty.call(wire, "day_preset")) {
    result.dayPreset = wire.day_preset == null ? null : String(wire.day_preset);
  }
  if (Object.prototype.hasOwnProperty.call(wire, "work_allotment_minutes")) {
    result.workAllotmentMinutes = wire.work_allotment_minutes == null
      ? null
      : Number(wire.work_allotment_minutes);
  }
  if (Object.keys(schedulable).length > 0) result.schedulable = schedulable;
  return result;
}

function projectEnabledZones(raw: unknown): string[] {
  return (Array.isArray(raw) ? raw : [])
    .map((zone) =>
      typeof zone === "string" ? zone : String((zone as Wire)?.name ?? ""),
    )
    .filter(Boolean);
}

function projectPreset(wire: Wire): DayPreset {
  return {
    name: String(wire.name ?? ""),
    days: (wire.days ?? []).map(String),
    enabledZones: projectEnabledZones(wire.enabled_zones),
    workAllotmentMinutes: wire.work_allotment_minutes == null
      ? null
      : Number(wire.work_allotment_minutes),
  };
}

export function projectDaySemantics(wire: Wire): DaySemantics {
  const mintSessions = Array.isArray(wire.mint_sessions)
    ? wire.mint_sessions
        .map((raw: Wire): MintSession | null => {
          const start = to24h(raw?.start);
          const end = to24h(raw?.end);
          const id = String(raw?.id ?? "").trim();
          const name = String(raw?.name ?? "").trim();
          if (!id || !name || !start || !end) return null;
          return {
            id,
            name,
            slot: String(raw?.slot ?? name).trim(),
            start,
            end,
          };
        })
        .filter((session): session is MintSession => session !== null)
    : [];
  return {
    availablePresets: (wire.available_presets ?? []).map(projectPreset),
    selectedPreset: wire.selected_preset ? projectPreset(wire.selected_preset) : null,
    resolutionSource: String(wire.resolution_source ?? ""),
    enabledZones: projectEnabledZones(wire.enabled_zones),
    effectiveAllotmentMinutes: Number(wire.effective_allotment_minutes ?? 0),
    defaultAllotmentMinutes: Number(wire.default_allotment_minutes ?? 0),
    mintEnabled: wire.mint_enabled === true,
    ...(Object.prototype.hasOwnProperty.call(wire, "mint_below_default")
      ? { mintBelowDefault: wire.mint_below_default === true }
      : {}),
    warnings: (wire.warnings ?? []).map(String),
    errors: (wire.errors ?? []).map(String),
    overlapPermissionsRaw: String(wire.overlap_permissions_raw ?? ""),
    mintSessions,
  };
}

const advisoryReminderOmission =
  /^\s*calendar omission: .*zero or negative duration \(reminder-style marker\)\s*$/i;

const capacitiesPartialPrefix = "Capacities partial";
const capacitiesPartialCounts = /^Capacities partial — (\d+) evaluated · (\d+) deferred\b/;

/** Capacities partial-coverage projection over the VERBATIM source warnings.
    The adapter owns the wording (content-read budget vs provider rate limit)
    and this projection only parses counts/reason so the refresh summary can
    say a completed refresh did not mean complete coverage. `warnings` stays
    authoritative for display — it is never rewritten from these fields.
    Null means the read had full coverage. */
export function capacitiesCoverageOf(warnings: string[]): CapacitiesCoverage | null {
  const rows = warnings.filter((warning) => warning.startsWith(capacitiesPartialPrefix));
  if (rows.length === 0) return null;
  let evaluated: number | null = null;
  let deferred: number | null = null;
  let limit: CapacitiesLimit = "unknown";
  for (const row of rows) {
    const counts = capacitiesPartialCounts.exec(row);
    if (counts && evaluated === null) {
      evaluated = Number(counts[1]);
      deferred = Number(counts[2]);
    }
    if (limit === "unknown") {
      if (/provider rate limit/i.test(row)) limit = "provider rate limit";
      else if (/content-read budget/i.test(row)) limit = "content-read budget";
    }
  }
  return { warnings: rows, evaluated, deferred, limit };
}

/** Capacities assignment-declaration diagnostics over the VERBATIM source
    warnings. The adapter owns the wording; this filter only projects the
    rows that report a declared property id the read could not honour, so the
    settings panel can show them beside the control that declares them. It is
    deliberately NOT part of the readiness rail (the operator flagged the rail
    as too verbose, and a configuration fact belongs beside its control).
    Null-equivalent ([]) means every declaration was honoured. */
export function capacitiesAssignmentWarnings(warnings: string[]): string[] {
  return warnings.filter((warning) =>
    warning.startsWith("ignored Capacities assignment declaration"),
  );
}

export function sourceHealthOf(warnings: string[]): SourceHealth {
  return warnings.some((warning) => !advisoryReminderOmission.test(warning))
    ? "degraded"
    : "ok";
}

/** Calendar is a FIXED input: the known zero-duration reminder marker is an
    intentional non-busy omission, not a failed read. Keep that diagnostic in
    sourceWarnings, but do not invalidate the fresh fixed-input snapshot; all
    other calendar warnings retain the locked decision-17 gate. */
export function calendarWarnings(warnings: string[]): string[] {
  return warnings.filter(
    (warning) => /calendar/i.test(warning) && !advisoryReminderOmission.test(warning),
  );
}

/** T19: micro_adventure projection — tolerant of the field's absence (older
    backends) and of partial shapes; degrades to a plain-Live no-pick state. */
export function projectMicroAdventure(raw: Wire | null | undefined): MicroAdventure {
  const idea = (r: Wire | null | undefined): MicroIdea | null => {
    if (!r || typeof r !== "object") return null;
    const id = String(r.id ?? "").trim();
    const text = String(r.idea ?? "").trim();
    if (!id || !text) return null;
    return { id, idea: text, category: String(r.category ?? "") };
  };
  const pick = idea(raw?.pick);
  const pending = raw?.pending_confirm;
  return {
    pick,
    source: raw?.source === "override" ? "override" : "auto",
    pool: Array.isArray(raw?.live_pool)
      ? (raw!.live_pool as Wire[]).map(idea).filter((p: MicroIdea | null): p is MicroIdea => p !== null)
      : [],
    streak: Number(raw?.streak ?? 0) || 0,
    pendingConfirm:
      pending && typeof pending === "object" && pending.id
        ? {
            date: String(pending.date ?? ""),
            id: String(pending.id),
            idea: String(pending.idea ?? ""),
          }
        : null,
  };
}

/** T6: forgot-strip rows. Server caps the list at 5 and the reason at 140
    chars; the cap is re-applied here so a stale or hand-edited payload can
    never flood the strip. A row with no name is dropped — there'd be nothing
    to act on. */
export function projectForgotList(rows: unknown): ForgotItem[] {
  if (!Array.isArray(rows)) return [];
  return rows
    .filter((r): r is Wire => !!r && typeof r === "object")
    .map((r) => ({
      name: String(r.name ?? ""),
      path: r.path ? String(r.path) : null,
      reason: String(r.reason ?? "").slice(0, 140),
    }))
    .filter((r) => r.name !== "")
    .slice(0, FORGOT_LIST_CAP);
}

export const FORGOT_LIST_CAP = 5;

/** IMP-07: rows the server excluded from today via Drop from plan. Shape is
    runstate's {identity, name, dropped_at}; the identity is canonical source
    identity (todoist:<id> / vault path). A row with no name or identity is
    dropped — there would be nothing to render or re-identify. */
export function projectDroppedToday(rows: unknown): import("../model/types").DroppedItem[] {
  if (!Array.isArray(rows)) return [];
  return rows
    .filter((r): r is Wire => !!r && typeof r === "object")
    .map((r) => ({
      identity: String(r.identity ?? ""),
      name: String(r.name ?? ""),
      droppedAt: r.dropped_at ? String(r.dropped_at) : null,
    }))
    .filter((r) => r.name !== "" && r.identity !== "");
}

export function projectPlanInputs(wire: Wire): PlanInputs {
  const warnings = (wire.source_warnings ?? []).map(String);
  const habits = wire.habits ?? {};
  const habitsNote =
    habits.total > 0
      ? `${habits.outstanding} of ${habits.total} habits outstanding · ~${habits.est_minutes}min`
      : null;
  return {
    validDate: String(wire.digest?.valid_date ?? ""),
    assigned: (wire.digest?.assigned ?? []).map(projectAssigned),
    unassignedCandidates: projectForgotList(wire.digest?.unassigned_candidates),
    staleAssigned: projectForgotList(wire.digest?.stale_assigned),
    droppedToday: projectDroppedToday(wire.dropped_today),
    anchored: (wire.anchored_blocks ?? []).map(projectAnchored),
    anchoredSourceFingerprint: String(wire.anchored_source_fingerprint ?? ""),
    habitsNote,
    time: {
      now: String(wire.time?.now ?? ""),
      anchor: String(wire.time?.anchor ?? ""),
      effectiveEod: String(wire.time?.effective_eod ?? ""),
      eodNote: wire.time?.eod_note ?? null,
      configEod: String(wire.time?.config_eod ?? ""),
      totalBlocks: Number(wire.time?.total_blocks ?? 0),
    },
    capacity: projectCapacity(wire.capacity ?? {}),
    daySetup: projectDaySetup(
      wire.day_setup ?? {},
      wire.day_setup_confirmed === true,
    ),
    daySemantics: projectDaySemantics(wire.day_semantics ?? {}),
    planningConfigFingerprint: String(wire.planning_config_fingerprint ?? ""),
    sourceWarnings: warnings,
    sourceCounts: {
      vault: Number(wire.source_counts?.vault ?? 0),
      todoist: Number(wire.source_counts?.todoist ?? 0),
      calendar: Number(wire.source_counts?.calendar ?? 0),
    },
    sourceHealth: sourceHealthOf(warnings),
    microAdventure: projectMicroAdventure(wire.micro_adventure),
  };
}

/** Fixed-input snapshot from a raw /plan-inputs payload (fingerprint source).
    Same normalization as the fixture's fixedInputsOf: calendar commitments +
    effective anchored blocks. */
export function projectFixedInputs(wire: Wire): FixedInputs {
  const anchored = (wire.anchored_blocks ?? []).map(projectAnchored);
  return {
    anchoredSourceFingerprint: String(wire.anchored_source_fingerprint ?? ""),
    planningConfigFingerprint: String(wire.planning_config_fingerprint ?? ""),
    calendar: anchored
      .filter(
        (a: AnchoredBlock) =>
          a.kind === "calendar" && a.capacityClass !== "quarantined",
      )
      .map((a: AnchoredBlock) => ({
        name: a.name,
        start: a.start,
        durationMin: a.durationMin,
        // T28: dismissal is a fixed-input change — freed interval must
        // invalidate a staged plan built with the row present.
        attending: a.skipToday !== true,
      })),
    anchored: anchored
      .filter((a: AnchoredBlock) => a.kind !== "calendar")
      .map((a: AnchoredBlock) => ({
        name: a.name,
        start: a.start,
        durationMin: a.durationMin,
        on: a.on,
        skipToday: a.skipToday,
      })),
  };
}

export function projectSequenceRow(row: Wire): SequenceRow {
  return {
    id: String(row.id),
    start: String(row.start),
    end: String(row.end),
    zone: row.zone ?? null,
    kind: row.backdrop === true ? "zone" : "work",
    wire: structuredClone(row),
  };
}

export function projectOverlapGrant(row: Wire): OverlapGrant {
  return {
    primaryId: String(row.primary_id),
    companionId: String(row.companion_id),
    primaryInterval: {
      start: String(row.primary_interval?.start ?? ""),
      end: String(row.primary_interval?.end ?? ""),
    },
    companionInterval: {
      start: String(row.companion_interval?.start ?? ""),
      end: String(row.companion_interval?.end ?? ""),
    },
    reason: String(row.reason ?? ""),
    planningConfigFingerprint: String(row.planning_config_fingerprint ?? ""),
  };
}

/** Server soft warnings arrive as dicts ({id, rule|kind, detail}) from
    sequence.validate_sequence — project the human `detail` string verbatim
    (locked decision 24 renders accepted defects verbatim). Plain strings
    (fixture adapter, block notes) pass through untouched. */
function warningText(w: unknown): string {
  if (typeof w === "string") return w;
  if (w && typeof w === "object" && "detail" in w) {
    return String((w as { detail: unknown }).detail);
  }
  return String(w);
}

function recordOf(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" ? value as Record<string, unknown> : null;
}

function diagnosticDetail(value: unknown, record: Record<string, unknown> | null, rule: string): string {
  const detail = record?.detail ?? record?.message ?? (typeof value === "string" ? value : null);
  if (typeof detail === "string") return detail;
  if (detail != null) return String(detail);
  try {
    return JSON.stringify(value) || rule;
  } catch {
    return rule;
  }
}

function projectValidationInterval(
  value: unknown,
): ValidationDiagnostic["intervals"][number] | null {
  const record = recordOf(value);
  if (!record) return null;
  const start = to24h(record.start);
  const end = to24h(record.end);
  if (!start || !end) return null;
  return {
    id: String(record.id ?? record.name ?? ""),
    start,
    end,
  };
}

function projectValidationDiagnostic(value: unknown, hardErrors: string[]): ValidationDiagnostic {
  const record = recordOf(value);
  const rule = String(record?.rule ?? record?.kind ?? "validation");
  const detail = diagnosticDetail(value, record, rule);
  const rawRows = record?.affected_rows ?? record?.affectedRows;
  const affectedRows = Array.isArray(rawRows)
    ? rawRows.map(String)
    : rawRows == null
      ? []
      : [String(rawRows)];
  const rawIntervals = record?.intervals;
  const intervalValues = Array.isArray(rawIntervals)
    ? rawIntervals
    : rawIntervals == null
      ? []
      : [rawIntervals];
  return {
    rule,
    // A matching hard error always wins: an additive severity cannot downgrade
    // an existing blocker to an acceptable warning.
    severity:
      hardErrors.includes(detail)
        ? "error"
        : record?.severity === "error"
          ? "error"
          : "warning",
    detail,
    affectedRows,
    intervals: intervalValues
      .map(projectValidationInterval)
      .filter((interval): interval is ValidationDiagnostic["intervals"][number] => interval !== null),
  };
}

export function projectSequenceResult(wire: Wire): {
  sequence: SequenceRow[];
  warnings: string[];
  overlapGrants: OverlapGrant[];
  pinnedRows?: SequenceRow[];
} {
  return {
    sequence: (wire.sequence ?? []).map(projectSequenceRow),
    warnings: (wire.warnings ?? []).map(warningText),
    overlapGrants: (wire.overlap_grants ?? []).map(projectOverlapGrant),
    // T27: the server returns the EFFECTIVE pin set (client + recurring
    // auto-pins); adopting it verbatim keeps later /validate-sequence and
    // /commit snapshot comparisons byte-exact.
    ...(Array.isArray(wire.pinned_rows)
      ? { pinnedRows: (wire.pinned_rows as Wire[]).map(projectSequenceRow) }
      : {}),
  };
}

export function projectValidation(wire: Wire): Validation {
  const projected: Validation = {
    ok: wire.ok === true,
    hardErrors: (wire.hard_errors ?? []).map(String),
    warnings: (wire.warnings ?? []).map(warningText),
  };
  if (Object.prototype.hasOwnProperty.call(wire, "diagnostics")) {
    const rawDiagnostics = wire.diagnostics;
    const diagnosticValues = Array.isArray(rawDiagnostics)
      ? rawDiagnostics
      : rawDiagnostics == null
        ? []
        : [rawDiagnostics];
    projected.diagnostics = diagnosticValues.map((value) =>
      projectValidationDiagnostic(value, projected.hardErrors),
    );
  }
  return projected;
}

const SHADOW_CLASSIFICATIONS: ShadowClassification[] = [
  "would-create",
  "would-update",
  "no-op",
  "conflict",
  "unavailable",
];

export function projectShadow(wire: Wire): ShadowDiff {
  const entries: ShadowEntry[] = (wire.entries ?? []).map((e: Wire) => {
    const m = e.manifest ?? {};
    return {
      step: String(m.step ?? ""),
      system: m.system,
      action: String(m.action ?? ""),
      name: String(m.name ?? ""),
      idOrPath: String(m.id_or_path ?? ""),
      time: m.time ?? null,
      durationMin: Number(m.duration_min ?? 0),
      classification: SHADOW_CLASSIFICATIONS.includes(e.classification)
        ? e.classification
        : "conflict",
      detail: e.detail ?? {},
    };
  });
  const counts = Object.fromEntries(
    SHADOW_CLASSIFICATIONS.map((c) => [c, Number(wire.counts?.[c] ?? 0)]),
  ) as ShadowDiff["counts"];
  return {
    entries,
    unavailableSurfaces: (wire.unavailable_surfaces ?? []).map(String),
    counts,
  };
}

/** T20: journal entry -> UI projection. Server-authoritative; only the
    fields the banners/undo chip need. */
export function projectRuntimeAction(wire: Wire): import("../model/types").RuntimeAction {
  return {
    id: String(wire.id ?? ""),
    verb: String(wire.verb ?? ""),
    targetName: String((wire.target as Wire)?.name ?? wire.target ?? ""),
    status: (wire.status ?? "pending") as import("../model/types").RuntimeAction["status"],
    error: wire.error == null ? null : String(wire.error),
    duplicate: wire.duplicate === true,
  };
}

export function projectCommitReport(wire: Wire): CommitReport {
  const surfaces: CommitSurface[] = Object.entries(wire.surfaces ?? {}).map(
    ([system, entry]: [string, any]) => ({
      system,
      status: entry?.status === "ok" ? "ok" : entry?.status === "skipped" ? "skipped" : "failed",
      detail: entry?.error ?? entry?.note ?? null,
    }),
  );
  const anyOk = surfaces.some((s) => s.status === "ok");
  const anyFailed = surfaces.some((s) => s.status === "failed");
  return {
    status: wire.ok === true ? "ok" : anyOk && anyFailed ? "partial" : "failed",
    surfaces,
    verifyFailures: (wire.verify_failures ?? []).map(String),
    // FEEDBACK-23: machine-canonical structured detail (24h HH:MM, raw ISO,
    // IANA timezone) travels separate from the 12h display strings — the
    // drawer formats display from these values.
    ...(Array.isArray(wire.verify_details)
      ? {
          verifyDetails: (wire.verify_details as Wire[]).map((d) => ({
            kind: d.kind === "due" ? "due" : "plain",
            name: String(d.name ?? ""),
            intent: d.intent == null ? null : String(d.intent),
            live: d.live == null ? null : String(d.live),
            liveRaw: d.live_raw == null ? null : String(d.live_raw),
            liveTimezone: d.live_timezone == null ? null : String(d.live_timezone),
            reason: String(d.reason ?? ""),
            message: String(d.message ?? ""),
          })),
        }
      : {}),
  };
}

// -- model → wire body builders ----------------------------------------------

export function daySetupToWire(d: DaySetup): Wire {
  const wire: Wire = {
    anchor: d.anchor,
    eod: d.eod,
    buffering: d.buffering,
    anchored: Object.entries(d.anchored).map(([id, o]) => ({
      id,
      on: o.on,
      skip_today: o.skipToday,
      time: o.time,
      ...(o.blocks == null ? {} : { blocks: o.blocks }),
    })),
    captures: {
      intention: d.captures.intention,
      megan_nicety: d.captures.forMeegy,
      stoic_intention: d.captures.stoic,
    },
  };
  if (Object.prototype.hasOwnProperty.call(d, "dayPreset")) wire.day_preset = d.dayPreset;
  if (Object.prototype.hasOwnProperty.call(d, "workAllotmentMinutes")) {
    wire.work_allotment_minutes = d.workAllotmentMinutes;
  }
  if (Object.prototype.hasOwnProperty.call(d, "schedulable")) {
    wire.schedulable = d.schedulable;
  }
  return wire;
}

export function rowToWire(r: SequenceRow): Wire {
  return {
    ...(r.wire ?? {}),
    id: r.id,
    start: r.start,
    end: r.end,
    zone: r.zone,
    ...(r.kind === "zone" ? { backdrop: true } : {}),
  };
}

export function grantToWire(g: OverlapGrant): Wire {
  return {
    primary_id: g.primaryId,
    companion_id: g.companionId,
    primary_interval: g.primaryInterval,
    companion_interval: g.companionInterval,
    reason: g.reason,
    planning_config_fingerprint: g.planningConfigFingerprint,
  };
}

/** Today-only shaping applied to the RAW digest rows for POST bodies:
    excluded rows drop, duration overrides replace `blocks`. Rows keep their
    wire shape verbatim otherwise (id = name, the T1 contract) — assignment
    truth is never touched (locked decision 16). */
export function shapeAssignedWire(
  rawAssigned: Wire[],
  included: Array<{ id: string; blocks: number }>,
  timeAdjustmentOptIns: Record<string, boolean> = {},
): Wire[] {
  const byId = new Map(included.map((i) => [i.id, i.blocks]));
  return rawAssigned
    .filter((row) => byId.has(String(row.name)))
    .map((row) => ({
      ...row,
      id: String(row.name),
      blocks: byId.get(String(row.name)),
      // Native Todoist times are protected unless this exact item was opted
      // in. Always emit the key so every endpoint sees the same explicit
      // false-by-default contract, regardless of stale source payloads.
      allow_time_adjustment: timeAdjustmentOptIns[String(row.name)] === true,
    }));
}
