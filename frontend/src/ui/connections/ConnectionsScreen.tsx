/* ConnectionsScreen — U13, the composed Connections page (reference C).

   One page: a sub-header (Back to planning, the title, a sync pill), two
   columns, and a footer unsaved-changes bar (owned by Footer). The left
   column carries the admission explainer, the structures table, a card per
   structure (admit-when rules, "Active means" chips, property mapping), the
   tag-exclusion card, and a collapsible Advanced region for every surface the
   reference has no slot for (source mapping, refresh, unknown review,
   declared assignments, known objects). The right column is the sticky
   admission preview.

   The view is recomposed; the engine is untouched. This screen calls exactly
   the existing controller routes (load/save capacities settings, load/save
   tag-exclusion settings, discover) and reuses the existing draft-revision,
   full-replacement and validation rules. The drawers keep their own bodies,
   so the legacy entry points are unaffected. */

import { useEffect, useMemo, useState } from "preact/hooks";
import { useApp, useAppState } from "../context";
import { CapacitiesSourceEditor } from "../CapacitiesSourceEditor";
import { CapacitiesRefreshPanel } from "../CapacitiesRefreshPanel";
import { CapacitiesUnknownReview } from "../CapacitiesUnknownReview";
import { Tooltip } from "../Tooltip";
import {
  NATIVE_RULES_DETAIL,
  NATIVE_RULES_LABEL,
  NATIVE_RULES_TRIGGER_LABEL,
} from "../nativeRulesCopy";
import {
  capacitiesAssignedOnWarnings,
  capacitiesAssignmentWarnings,
  isCanonicalCapacitiesIdentity,
  missingGenerationWarnings,
  sourceHealthBySource,
} from "../../adapters/wire";
import {
  clearConnectionsCache,
  onConnectionsDiscard,
  onConnectionsSave,
  publishConnectionsChanges,
  readConnectionsCache,
  writeConnectionsCache,
  type ConnectionsChange,
} from "./commands";
import type {
  AssignedItem,
  CapacitiesCatalog,
  CapacitiesSettings,
  CapacitiesSettingsDraft,
  TagExclusionIdentity,
  TagExclusionSettings,
  TagExclusionSettingsDraft,
} from "../../model/types";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function draftOf(settings: CapacitiesSettings): CapacitiesSettingsDraft {
  return {
    expectedRevision: settings.revision,
    nativeTaskAuto: { ...settings.nativeTaskAuto },
    excluded: [...settings.excluded],
    activeStructures: [...settings.activeStructures],
    nativeTaskStructures: [...settings.nativeTaskStructures],
    activeStatuses: [...settings.activeStatuses],
    assignedStructures: { ...settings.assignedStructures },
    assignedOnStructures: { ...settings.assignedOnStructures },
  };
}

function tagDraftOf(settings: TagExclusionSettings): TagExclusionSettingsDraft {
  // The save route is a full replacement of both dimensions: the draft always
  // carries the Todoist list so a tag edit never wipes hidden tasks.
  return {
    expectedRevision: settings.revision,
    tags: [...settings.tags],
    todoist: [...settings.todoist],
  };
}

/** Short, human label for a hidden task's added_at timestamp (e.g. "Mar 4, 2026"). */
function shortAddedDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

function tagKey(identity: TagExclusionIdentity): string {
  return `${identity.source}:${identity.spaceId}:${identity.tagId}`;
}

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** The dirty slices of the Capacities policy, in the operator's words. */
function capacitiesChanges(
  draft: CapacitiesSettingsDraft,
  base: CapacitiesSettingsDraft,
): ConnectionsChange[] {
  const out: ConnectionsChange[] = [];
  if (!sameJson(draft.nativeTaskAuto, base.nativeTaskAuto)) {
    out.push({ key: "rules", label: "Native rules" });
  }
  if (
    !sameJson(draft.assignedStructures, base.assignedStructures) ||
    !sameJson(draft.assignedOnStructures, base.assignedOnStructures)
  ) {
    out.push({ key: "mapping", label: "Task mapping" });
  }
  if (!sameJson([...draft.excluded].sort(), [...base.excluded].sort())) {
    out.push({ key: "excluded", label: "Known objects" });
  }
  if (!sameJson([...draft.activeStructures].sort(), [...base.activeStructures].sort())) {
    out.push({ key: "activePull", label: "Active pull" });
  }
  if (!sameJson([...draft.nativeTaskStructures].sort(), [...base.nativeTaskStructures].sort())) {
    out.push({ key: "nativeStructures", label: "Native structures" });
  }
  if (!sameJson(draft.activeStatuses, base.activeStatuses)) {
    out.push({ key: "statuses", label: "Status values" });
  }
  return out;
}

interface PreviewRow {
  title: string;
  reason: string;
}

export function ConnectionsScreen() {
  const s = useAppState();
  const { controller, store } = useApp();

  const [capSettings, setCapSettings] = useState<CapacitiesSettings | null>(null);
  const [capDraft, setCapDraft] = useState<CapacitiesSettingsDraft | null>(null);
  const [tagSettings, setTagSettings] = useState<TagExclusionSettings | null>(null);
  const [tagDraft, setTagDraft] = useState<TagExclusionSettingsDraft | null>(null);
  const [horizonText, setHorizonText] = useState("");
  const [statusText, setStatusText] = useState("");
  const [tagQuery, setTagQuery] = useState("");
  const [phase, setPhase] = useState<"loading" | "ready" | "saving" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [discovered, setDiscovered] = useState<CapacitiesCatalog | null>(null);
  // Which nav item owns the detail pane. Null falls back to the first
  // configured structure (or Advanced when the mapping lists none).
  const [selectedNav, setSelectedNav] = useState<string | null>(null);

  const load = async () => {
    setPhase("loading");
    setError(null);
    try {
      const [cap, tag] = await Promise.all([
        controller.loadCapacitiesSettings(),
        controller.loadTagExclusionSettings(),
      ]);
      setCapSettings(cap);
      setCapDraft(draftOf(cap));
      setHorizonText(String(cap.nativeTaskAuto.deadlineHorizonDays));
      setTagSettings(tag);
      setTagDraft(tagDraftOf(tag));
      setPhase("ready");
    } catch (e) {
      setPhase("error");
      setError(messageOf(e));
    }
  };

  useEffect(() => {
    const cached = readConnectionsCache();
    if (cached) {
      setCapSettings(cached.capSettings);
      setCapDraft(cached.capDraft);
      setHorizonText(cached.horizonText);
      setStatusText(cached.statusText);
      setTagSettings(cached.tagSettings);
      setTagDraft(cached.tagDraft);
      setPhase("ready");
      return;
    }
    void load();
  }, [controller]);

  const capBase = useMemo(() => (capSettings ? draftOf(capSettings) : null), [capSettings]);
  const tagBase = useMemo(() => (tagSettings ? tagDraftOf(tagSettings) : null), [tagSettings]);

  const changes = useMemo<ConnectionsChange[]>(() => {
    if (!capDraft || !capBase) return [];
    const list = capacitiesChanges(capDraft, capBase);
    if (tagDraft && tagBase && !sameJson(tagDraft.tags, tagBase.tags)) {
      list.push({ key: "tags", label: "tag list" });
    }
    if (tagDraft && tagBase && !sameJson(tagDraft.todoist, tagBase.todoist)) {
      list.push({ key: "hidden", label: "hidden tasks" });
    }
    return list;
  }, [capDraft, capBase, tagDraft, tagBase]);

  const horizonValue = Number(horizonText);
  const horizonTextValid = /^\d+$/.test(horizonText) && Number.isSafeInteger(horizonValue);
  const horizonInvalid =
    capDraft?.nativeTaskAuto.deadlineEnabled === true && !horizonTextValid;

  // Publish the dirty summary and cache the draft while it is dirty, so the
  // footer can name the changes and Back to planning keeps them.
  useEffect(() => {
    publishConnectionsChanges(changes, {
      invalid: horizonInvalid,
      saving: phase === "saving",
    });
    if (
      changes.length > 0 &&
      capSettings &&
      capDraft &&
      tagSettings &&
      tagDraft
    ) {
      writeConnectionsCache({
        capSettings,
        capDraft,
        tagSettings,
        tagDraft,
        horizonText,
        statusText,
      });
    } else if (changes.length === 0) {
      clearConnectionsCache();
    }
  }, [changes, horizonInvalid, phase, capSettings, capDraft, tagSettings, tagDraft, horizonText, statusText]);

  const save = async () => {
    if (!capDraft || !tagDraft || horizonInvalid || phase === "saving") return;
    const horizon = horizonTextValid ? horizonValue : capDraft.nativeTaskAuto.deadlineHorizonDays;
    if (!Number.isSafeInteger(horizon) || horizon < 0) {
      setError("Deadline horizon must be a nonnegative whole number of days.");
      return;
    }
    setPhase("saving");
    setError(null);
    try {
      const savedCap = await controller.saveCapacitiesSettings({
        ...capDraft,
        nativeTaskAuto: { ...capDraft.nativeTaskAuto, deadlineHorizonDays: horizon },
      });
      const { settings: savedTag } = await controller.saveTagExclusionSettings(tagDraft);
      setCapSettings(savedCap);
      setCapDraft(draftOf(savedCap));
      setHorizonText(String(savedCap.nativeTaskAuto.deadlineHorizonDays));
      setTagSettings(savedTag);
      setTagDraft(tagDraftOf(savedTag));
      clearConnectionsCache();
      setPhase("ready");
    } catch (e) {
      setPhase("ready");
      setError(messageOf(e));
    }
  };

  const discard = () => {
    if (capSettings) {
      setCapDraft(draftOf(capSettings));
      setHorizonText(String(capSettings.nativeTaskAuto.deadlineHorizonDays));
    }
    if (tagSettings) setTagDraft(tagDraftOf(tagSettings));
    setStatusText("");
    setError(null);
    clearConnectionsCache();
  };

  useEffect(() => {
    const offSave = onConnectionsSave(() => void save());
    const offDiscard = onConnectionsDiscard(() => discard());
    return () => {
      offSave();
      offDiscard();
    };
  });

  const back = () => store.dispatch({ type: "UI", patch: { screen: "plan" } });

  /* -- draft mutators (same shapes and full-replacement rules) ------------ */

  const updatePolicy = (patch: Partial<CapacitiesSettingsDraft["nativeTaskAuto"]>) =>
    setCapDraft((current) =>
      current ? { ...current, nativeTaskAuto: { ...current.nativeTaskAuto, ...patch } } : current,
    );

  const setMapping = (structureId: string, propertyId: string) =>
    setCapDraft((current) => {
      if (!current) return current;
      const next = { ...current.assignedStructures };
      if (propertyId === "") delete next[structureId];
      else next[structureId] = propertyId;
      return { ...current, assignedStructures: next };
    });

  const setAssignedOn = (structureId: string, propertyId: string) =>
    setCapDraft((current) => {
      if (!current) return current;
      const next = { ...current.assignedOnStructures };
      if (propertyId === "") delete next[structureId];
      else next[structureId] = propertyId;
      return { ...current, assignedOnStructures: next };
    });

  const toggleActiveStructure = (structureId: string) =>
    setCapDraft((current) => {
      if (!current) return current;
      const activeStructures = current.activeStructures.includes(structureId)
        ? current.activeStructures.filter((v) => v !== structureId)
        : [...current.activeStructures, structureId].sort();
      return { ...current, activeStructures };
    });

  const toggleNativeStructure = (structureId: string) =>
    setCapDraft((current) => {
      if (!current) return current;
      const nativeTaskStructures = current.nativeTaskStructures.includes(structureId)
        ? current.nativeTaskStructures.filter((v) => v !== structureId)
        : [...current.nativeTaskStructures, structureId].sort();
      return { ...current, nativeTaskStructures };
    });

  const removeStructure = (structureId: string) =>
    setCapDraft((current) => {
      if (!current) return current;
      const next = { ...current.assignedStructures };
      delete next[structureId];
      const nextOn = { ...current.assignedOnStructures };
      delete nextOn[structureId];
      return {
        ...current,
        activeStructures: current.activeStructures.filter((v) => v !== structureId),
        nativeTaskStructures: current.nativeTaskStructures.filter((v) => v !== structureId),
        assignedStructures: next,
        assignedOnStructures: nextOn,
      };
    });

  const addStatus = () => {
    const value = statusText.trim();
    setStatusText("");
    if (value === "") return;
    setCapDraft((current) =>
      !current || current.activeStatuses.includes(value)
        ? current
        : { ...current, activeStatuses: [...current.activeStatuses, value] },
    );
  };

  const removeStatus = (status: string) =>
    setCapDraft((current) =>
      current
        ? { ...current, activeStatuses: current.activeStatuses.filter((v) => v !== status) }
        : current,
    );

  const toggleTag = (identity: TagExclusionIdentity) =>
    setTagDraft((current) => {
      if (!current) return current;
      const key = tagKey(identity);
      const has = current.tags.some((t) => tagKey(t) === key);
      const tags = has
        ? current.tags.filter((t) => tagKey(t) !== key)
        : [...current.tags, { ...identity }].sort((a, b) => (tagKey(a) < tagKey(b) ? -1 : 1));
      return { ...current, tags };
    });

  /** Show again: drop the hidden task from the DRAFT only. The footer Save
      commits the full replacement; Discard restores from the loaded base. */
  const showTodoistAgain = (id: string) =>
    setTagDraft((current) =>
      current
        ? { ...current, todoist: current.todoist.filter((entry) => entry.id !== id) }
        : current,
    );

  const discover = async () => {
    try {
      const loaded = await controller.loadCapacitiesSource();
      const spaceId = loaded.source?.spaceId;
      if (!spaceId) return;
      setDiscovered(await controller.discoverCapacitiesSource(spaceId));
    } catch {
      /* Discovery is advisory; a failure leaves the draft untouched. */
    }
  };

  /* -- derived data ------------------------------------------------------- */

  const structures = capSettings?.availableStructures ?? [];
  const titleOf = (structureId: string): string | null => {
    const title = capSettings?.structureTitles[structureId];
    return title && title !== structureId ? title : null;
  };

  const known = useMemo(() => {
    const seen = new Set<string>();
    return (s.inputs?.assigned ?? []).filter((item: AssignedItem) => {
      if (item.source !== "capacities") return false;
      const identity = item.capacitiesIdentity ?? item.identity ?? null;
      if (!isCanonicalCapacitiesIdentity(identity) || seen.has(identity as string)) return false;
      seen.add(identity as string);
      return true;
    });
  }, [s.inputs]);

  /* R8/KTD7: each source dot reads its OWN attributed health, so a
     Capacities-only warning never colours the Todoist or Calendar dot. The
     global ``sourceHealth`` still drives the pill and the drawer, where it
     means "commit blocked". */
  const sourceHealth = sourceHealthBySource(s.inputs?.sourceWarnings ?? []);
  const assignmentWarnings = capacitiesAssignmentWarnings(s.inputs?.sourceWarnings ?? []);
  const assignedOnWarnings = capacitiesAssignedOnWarnings(s.inputs?.sourceWarnings ?? []);
  /* Generation-freshness notices are advisory for the global read, so they
     never degrade sourceHealth or the Todoist/Calendar dots. They are still a
     real gap — those mapped types are absent from the served plan — so the
     Capacities row carries the count as its own per-source note. */
  const missingGenerationCount = missingGenerationWarnings(
    s.inputs?.sourceWarnings ?? [],
  ).length;
  const missingGenerationNote =
    missingGenerationCount > 0
      ? `${missingGenerationCount} mapped type(s) not yet in the published generation; Refresh`
      : null;

  const selectedTagKeys = new Set((tagDraft?.tags ?? []).map(tagKey));
  const catalogTags = tagSettings?.catalog.tags ?? [];
  const staleTags = (tagDraft?.tags ?? []).filter((identity) => {
    const catalog = tagSettings?.catalog;
    return !(
      identity.spaceId === catalog?.spaceId &&
      catalogTags.some((tag) => tag.id === identity.tagId)
    );
  });
  const tagLabelOf = (identity: TagExclusionIdentity): string => {
    const tag = catalogTags.find((t) => t.id === identity.tagId);
    return tag?.title ? `#${tag.title}` : identity.tagId.slice(0, 8);
  };

  const excludedTagIds = new Set((tagDraft?.tags ?? []).map((t) => t.tagId));
  /* The preview is per structure. An item's structure comes from the canonical
     `capacities:<space>:<structure>:<object>` identity; the backend also emits
     a dedicated `capacities_structure_id`, but the wire projection does not map
     it today, so the identity is the populated path and the explicit field wins
     when a fixture or legacy payload carries it. An item whose structure is not
     in the configured list appears under no nav item, which is the honest
     state rather than showing it under every structure. */
  const structureOf = (item: AssignedItem): string => {
    const explicit = (item.capacitiesStructureId ?? "").trim();
    if (explicit !== "") return explicit;
    const identity = item.capacitiesIdentity ?? item.identity ?? "";
    const parts = identity.split(":");
    return parts[0] === "capacities" && parts.length >= 4 ? parts[2] : "";
  };
  /* A structure counts as mapped when either declaration is set: the legacy
     boolean `assigned_structures` marker, or the read-only `Assigned On`
     date marker (which wins for that structure). */
  const hasMapping = (structureId: string): boolean =>
    (capDraft?.assignedStructures[structureId] ?? "") !== "" ||
    (capDraft?.assignedOnStructures[structureId] ?? "") !== "";
  const previewRowsFor = (structureId: string): PreviewRow[] =>
    known
      .filter((item) => structureOf(item) === structureId)
      .map((item) => {
        const tags = item.tags ?? [];
        if (tags.some((tag) => excludedTagIds.has(tag))) {
          return { title: item.name, reason: "left out by tag" };
        }
        if (!hasMapping(structureId)) {
          return { title: item.name, reason: "waiting on mapping" };
        }
        return { title: item.name, reason: "admitted" };
      });

  const syncText = (() => {
    const state = s.inputs?.capacitiesIntake?.state;
    if (state === "ok") return "Capacities synced";
    if (state === "degraded") return "Capacities degraded";
    if (state === "unavailable") return "Capacities unavailable";
    return "Capacities not synced";
  })();

  const mappingChoices = (structureId: string): string[] =>
    discovered?.structures.find((st) => st.structureId === structureId)?.properties.map(
      (p) => p.propertyId,
    ) ?? [];
  /** The Assigned On marker is READ-ONLY date data: only date/datetime
      properties are offered as choices, and only as suggestions. */
  const assignedOnChoices = (structureId: string): string[] =>
    (discovered?.structures.find((st) => st.structureId === structureId)?.properties ?? [])
      .filter((property) => property.type === "date" || property.type === "datetime")
      .map((property) => property.propertyId);

  /* -- navigation helpers ------------------------------------------------ */

  const SHORT_ID_AFTER = 24;
  const shortId = (structureId: string): string =>
    structureId.length > SHORT_ID_AFTER ? `${structureId.slice(0, 8)}…` : structureId;
  const navLabel = (structureId: string): string => {
    const title = titleOf(structureId);
    const short = shortId(structureId);
    return title ? (short === structureId ? title : `${title} ${short}`) : short;
  };
  /** A structure's health dot: amber while it still needs a mapping, green
      once it is mapped and pulling, grey when the operator turned it off. */
  const structureDot = (structureId: string): string => {
    if (!hasMapping(structureId)) return "#e0a020";
    return capDraft?.activeStructures.includes(structureId) ? "#1f9d6b" : "#cfccc4";
  };
  /** Sources keep their own health. Todoist and Calendar read only the
      warnings attributed to them (``sourceHealthBySource``), so a
      Capacities-only warning never colours them (R8, AE4). Capacities is its
      own source: amber while the intake is not ok, any structure still waits
      on a mapping, or a Capacities warning blocks commit. */
  const sourceDot = (source: string): string => {
    if (source === "capacities") {
      const state = s.inputs?.capacitiesIntake?.state;
      const anyUnmapped = structures.some((id) => !hasMapping(id));
      return state !== "ok" || anyUnmapped || sourceHealth.capacities !== "ok"
        ? "#e0a020"
        : "#1f9d6b";
    }
    const health = source === "calendar" ? sourceHealth.calendar : sourceHealth.todoist;
    return health === "ok" ? "#1f9d6b" : "#e0a020";
  };

  const defaultNav =
    structures.length > 0 ? `structure:${structures[0]}` : "rule:advanced";
  const activeNav = selectedNav ?? defaultNav;

  /* -- detail renderers --------------------------------------------------- */

  const renderSwitch = (name: string, checked: boolean, onChange: () => void) => (
    <button
      type="button"
      role="switch"
      class="connections-switch"
      aria-checked={checked}
      aria-label={name}
      onClick={onChange}
    />
  );

  const renderGlobalRules = () => {
    if (!capDraft) return null;
    const policy = capDraft.nativeTaskAuto;
    return (
      <>
        <div class="connections-admit__row">
          <span class="connections-admit__name">Status is active</span>
          {renderSwitch("Status is active", policy.activeEnabled, () =>
            updatePolicy({ activeEnabled: !policy.activeEnabled }),
          )}
        </div>
        <div class="connections-admit__row">
          <span class="connections-admit__name">Due today or overdue</span>
          {renderSwitch("Due today or overdue", policy.dueEnabled, () =>
            updatePolicy({ dueEnabled: !policy.dueEnabled }),
          )}
        </div>
        <div class="connections-admit__row">
          <span class="connections-admit__name">
            Deadline within
            <input
              class="connections-check__days"
              type="number"
              min="0"
              step="1"
              aria-label="Deadline horizon"
              value={horizonText}
              disabled={!policy.deadlineEnabled}
              onInput={(e) => {
                const text = (e.currentTarget as HTMLInputElement).value;
                setHorizonText(text);
                const value = Number(text);
                if (Number.isSafeInteger(value) && value >= 0)
                  updatePolicy({ deadlineHorizonDays: value });
              }}
            />
            days
          </span>
          {renderSwitch(
            `Deadline within ${horizonText} days`,
            policy.deadlineEnabled,
            () => updatePolicy({ deadlineEnabled: !policy.deadlineEnabled }),
          )}
        </div>
        {horizonInvalid && (
          <p class="field-error connections-admit__error" role="alert">
            Use a nonnegative whole number.
          </p>
        )}
        <div class="connections-admit__row connections-admit__row--chips">
          <span class="connections-admit__name">Active means</span>
          <div class="connections-active-means">
            {capDraft.activeStatuses.map((status) => (
              <span class="connections-chip" key={status}>
                {status}
                <button
                  class="connections-chip__x"
                  aria-label={`Remove status ${status}`}
                  onClick={() => removeStatus(status)}
                >
                  ×
                </button>
              </span>
            ))}
            <input
              class="connections-chip-input"
              type="text"
              aria-label="Add a status value"
              placeholder="+ add"
              value={statusText}
              onInput={(e) => setStatusText((e.currentTarget as HTMLInputElement).value)}
              onKeyDown={(e) => {
                if ((e as unknown as KeyboardEvent).key === "Enter") addStatus();
              }}
            />
          </div>
        </div>
      </>
    );
  };

  const renderStructureRules = (structureId: string) => (
    <>
      <div class="connections-admit__row">
        <span class="connections-admit__name">Include in active pull</span>
        {renderSwitch(
          `Include in active pull for ${structureId}`,
          capDraft?.activeStructures.includes(structureId) ?? false,
          () => toggleActiveStructure(structureId),
        )}
      </div>
      <div class="connections-admit__row">
        <span class="connections-admit__name">Use native rules</span>
        {renderSwitch(
          `Native rules for ${structureId}`,
          capDraft?.nativeTaskStructures.includes(structureId) ?? false,
          () => toggleNativeStructure(structureId),
        )}
      </div>
    </>
  );

  const renderMapping = (structureId: string) => {
    const legacy = capDraft?.assignedStructures[structureId] ?? "";
    const assignedOn = capDraft?.assignedOnStructures[structureId] ?? "";
    const onMapped = assignedOn !== "";
    return (
      <div class="connections-mapping">
        <span class="connections-admit__label">Property mapping</span>
        <div class="connections-mapping__row">
          <span class="connections-mapping__name">Assigned On property</span>
          <input
            class="connections-mapping__input"
            type="text"
            list={`connections-assigned-on-props-${structureId}`}
            aria-label={`Assigned On property for ${structureId}`}
            placeholder="Property id"
            value={assignedOn}
            onInput={(e) => setAssignedOn(structureId, (e.currentTarget as HTMLInputElement).value)}
          />
          <button
            class="btn connections-mapping__clear"
            aria-label={`Clear Assigned On property for ${structureId}`}
            disabled={assignedOn === ""}
            onClick={() => setAssignedOn(structureId, "")}
          >
            Clear
          </button>
          <datalist id={`connections-assigned-on-props-${structureId}`}>
            {assignedOnChoices(structureId).map((propertyId) => (
              <option key={propertyId} value={propertyId} />
            ))}
          </datalist>
        </div>
        <p class="connections-mapping__help">
          Date-time property set in Capacities. An item counts as assigned when this is today or
          earlier; a future date is not assigned yet.
        </p>
        <div class="connections-mapping__row">
          <span class="connections-mapping__name">Assigned flag (legacy)</span>
          <input
            class="connections-mapping__input"
            type="text"
            list={`connections-props-${structureId}`}
            aria-label={`Mapping for ${structureId}`}
            placeholder="Property id"
            value={legacy}
            disabled={onMapped}
            onInput={(e) => setMapping(structureId, (e.currentTarget as HTMLInputElement).value)}
          />
          <button
            class="btn btn--primary connections-mapping__discover"
            onClick={() => void discover()}
          >
            Discover from Capacities
          </button>
          <datalist id={`connections-props-${structureId}`}>
            {mappingChoices(structureId).map((propertyId) => (
              <option key={propertyId} value={propertyId} />
            ))}
          </datalist>
        </div>
        <p class="connections-mapping__help">Used only when no Assigned On property is mapped.</p>
        {onMapped && <p class="connections-mapping__inactive">Ignored while Assigned On is mapped</p>}
      </div>
    );
  };

  const renderStructureDetail = (structureId: string) => {
    const previewRows = previewRowsFor(structureId);
    const admittedCount = previewRows.filter((r) => r.reason === "admitted").length;
    const waitingCount = previewRows.filter((r) => r.reason === "waiting on mapping").length;
    const excludedCount = previewRows.filter((r) => r.reason === "left out by tag").length;
    const firstWaiting = previewRows.find((r) => r.reason === "waiting on mapping");
    const typeLabel = firstWaiting?.title ?? previewRows[0]?.title ?? "";
    const mappedLegacy = capDraft?.assignedStructures[structureId] ?? "";
    const mappedOn = capDraft?.assignedOnStructures[structureId] ?? "";
    const mappedHint =
      mappedLegacy !== "" ? `Mapped: ${mappedLegacy}` : mappedOn !== "" ? `Mapped: ${mappedOn}` : "Not mapped";
    return (
      <>
        <div class="connections-detail__head">
          <div class="connections-detail__id">
            <span class="connections-detail__kicker">Capacities structure</span>
            <h2 class="connections-detail__title">{titleOf(structureId) ?? structureId}</h2>
            <p class="connections-detail__helper">
              Items are admitted when any rule matches. Completed and dropped items are always
              left out.
            </p>
            <code class="connections-detail__mapping">{mappedHint}</code>
          </div>
          <button
            class="connections-detail__remove"
            aria-label={`Remove structure ${titleOf(structureId) ?? structureId}`}
            onClick={() => removeStructure(structureId)}
          >
            Remove
          </button>
        </div>

        <div class="connections-previewstrip" role="region" aria-label="Preview · today">
          <span class="connections-previewstrip__count">{admittedCount} admitted today</span>
          <span class="connections-previewstrip__meta">
            {waitingCount} waiting on mapping
            {excludedCount > 0 ? ` · ${excludedCount} left out by tag` : ""}
            {typeLabel !== "" ? ` · ${typeLabel}` : ""}
          </span>
        </div>

        <details class="connections-previewitems">
          <summary class="connections-previewitems__summary">
            Preview items ({previewRows.length})
          </summary>
          {previewRows.length === 0 ? (
            <p class="connections-previewitems__empty">
              Nothing in the current read is waiting on this structure.
            </p>
          ) : (
            <ul class="connections-previewitems__list">
              {previewRows.map((row) => (
                <li class="connections-previewitems__row" key={`${row.title}:${row.reason}`}>
                  <span class="connections-previewitems__title">{row.title}</span>
                  <span class="connections-previewitems__reason">{row.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </details>

        <div class="connections-admit">
          <span class="connections-admit__label connections-admit__label--info">
            Admit when
            <Tooltip
              label={NATIVE_RULES_LABEL}
              detail={NATIVE_RULES_DETAIL}
              bubbleClass="tooltip--wide"
            >
              <button type="button" class="iconbtn" aria-label={NATIVE_RULES_TRIGGER_LABEL}>
                ?
              </button>
            </Tooltip>
          </span>
          {structures[0] === structureId ? renderGlobalRules() : renderStructureRules(structureId)}
        </div>

        {renderMapping(structureId)}
      </>
    );
  };

  const renderSourceDetail = (source: string) => {
    if (source === "capacities") {
      return (
        <>
          <div class="connections-detail__head">
            <div class="connections-detail__id">
              <span class="connections-detail__kicker">Source</span>
              <h2 class="connections-detail__title">Capacities</h2>
              <p class="connections-detail__helper">
                <span class="connections__sync" role="status" aria-live="polite">
                  {syncText}
                </span>
              </p>
            </div>
          </div>
          <CapacitiesRefreshPanel active={true} />
          <CapacitiesUnknownReview active={true} />
        </>
      );
    }
    const isTodoist = source === "todoist";
    const label = isTodoist ? "Todoist" : "Calendar";
    const ownHealth = isTodoist ? sourceHealth.todoist : sourceHealth.calendar;
    const count = isTodoist
      ? s.inputs?.sourceCounts.todoist ?? 0
      : s.inputs?.sourceCounts.calendar ?? 0;
    return (
      <div class="connections-detail__head">
        <div class="connections-detail__id">
          <span class="connections-detail__kicker">Source</span>
          <h2 class="connections-detail__title">{label}</h2>
          <p class="connections-detail__helper">
            {count} {isTodoist ? "tasks" : "events"} in the current read.
            {ownHealth === "ok"
              ? " Healthy."
              : " Degraded — the last read reported a problem."}
          </p>
        </div>
      </div>
    );
  };

  const renderTagsDetail = () => {
    if (!tagDraft) return null;
    return (
      <section class="connections-card connections-card--tags" aria-label="Exclude items tagged">
        <header class="connections-card__head">
          <h3 class="connections-card__title">Exclude items tagged</h3>
          <span class="connections-card__note">Applies to every structure</span>
        </header>
        {staleTags.length > 0 && (
          <div class="connections-stale" role="status">
            <span>A saved tag no longer exists in Capacities.</span>
            <code>{staleTags[0].tagId.slice(0, 8)}…</code>
            <button
              class="btn"
              aria-label={`Remove saved tag ${staleTags[0].tagId}`}
              onClick={() => toggleTag(staleTags[0])}
            >
              Remove
            </button>
          </div>
        )}
        <div class="connections-tags">
          {(tagDraft.tags ?? []).map((identity) => (
            <span class="connections-chip" key={tagKey(identity)}>
              {tagLabelOf(identity)}
              <button
                class="connections-chip__x"
                aria-label={`Remove tag ${tagLabelOf(identity)}`}
                onClick={() => toggleTag(identity)}
              >
                ×
              </button>
            </span>
          ))}
          <input
            class="connections-chip-input"
            type="search"
            aria-label="Search tags to exclude"
            placeholder="Search tags to exclude…"
            value={tagQuery}
            onInput={(e) => setTagQuery((e.currentTarget as HTMLInputElement).value)}
          />
        </div>
        {tagQuery.trim() !== "" && (
          <ul class="connections-tags__suggestions">
            {catalogTags
              .filter(
                (tag) =>
                  !selectedTagKeys.has(`capacities:${tagSettings?.catalog.spaceId}:${tag.id}`) &&
                  (tag.title ?? "")
                    .toLocaleLowerCase()
                    .includes(tagQuery.trim().toLocaleLowerCase()),
              )
              .map((tag) => (
                <li key={tag.id}>
                  <button
                    class="btn"
                    onClick={() =>
                      toggleTag({
                        source: "capacities",
                        spaceId: tagSettings?.catalog.spaceId ?? "",
                        tagId: tag.id,
                      })
                    }
                  >
                    #{tag.title ?? tag.id}
                  </button>
                </li>
              ))}
          </ul>
        )}
      </section>
    );
  };

  const renderHiddenTasksDetail = () => {
    if (!tagDraft) return null;
    return (
      <section class="connections-card connections-card--hidden" aria-label="Hidden tasks">
        <header class="connections-card__head">
          <h3 class="connections-card__title">Hidden tasks</h3>
          <span class="connections-card__note">Hidden from planning every day</span>
        </header>
        {tagDraft.todoist.length === 0 ? (
          <p class="connections-hidden__empty">
            Nothing hidden. Use ⋯ &gt; Always exclude on a task to hide it every day.
          </p>
        ) : (
          <ul class="connections-hidden__list">
            {tagDraft.todoist.map((entry) => (
              <li class="connections-hidden__row" key={entry.id}>
                <div class="connections-hidden__identity">
                  <strong class="connections-hidden__name">{entry.name}</strong>
                  <span class="connections-hidden__date">{shortAddedDate(entry.addedAt)}</span>
                </div>
                <button
                  class="btn connections-hidden__show"
                  aria-label={`Show again ${entry.name}`}
                  onClick={() => showTodoistAgain(entry.id)}
                >
                  Show again
                </button>
              </li>
            ))}
          </ul>
        )}
        <p class="connections-hidden__note">
          Capacities objects are managed under Known objects in Advanced.
        </p>
      </section>
    );
  };

  const renderAdvancedDetail = () => (
    <div class="connections-advanced__body">
      <CapacitiesSourceEditor structureTitleOf={titleOf} />
      {assignmentWarnings.length > 0 && (
        <section class="setup-section" aria-label="Ignored declarations">
          <h4>Ignored declarations in the current read</h4>
          {assignmentWarnings.map((warning) => (
            <code key={warning}>{warning}</code>
          ))}
        </section>
      )}
      {assignedOnWarnings.length > 0 && (
        <section class="setup-section" aria-label="Assigned On diagnostics">
          <h4>Assigned On diagnostics in the current read</h4>
          {assignedOnWarnings.map((warning) => (
            <code key={warning}>{warning}</code>
          ))}
        </section>
      )}
      <section class="setup-section" aria-label="Known Capacities objects">
        <h4>Known Capacities objects</h4>
        {known.length === 0 ? (
          <p class="capacities-settings__empty">
            No Capacities objects are present in the current plan inputs.
          </p>
        ) : (
          <div class="capacities-object-list">
            {known.map((item) => (
              <div class="capacities-object-row" key={item.capacitiesIdentity ?? item.id}>
                <div class="capacities-object-row__identity">
                  <strong>{item.name}</strong>
                  <code>{item.capacitiesIdentity ?? item.identity}</code>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );

  const hiddenCount = tagDraft?.todoist.length ?? 0;
  const navItem = (id: string, label: string, dot?: string) => (
    <button
      key={id}
      type="button"
      class={`connections-navitem${activeNav === id ? " connections-navitem--active" : ""}`}
      aria-current={activeNav === id ? "true" : undefined}
      onClick={() => setSelectedNav(id)}
    >
      <span class="connections-navitem__label">{label}</span>
      {dot && (
        <span class="connections-navitem__dot" style={{ background: dot }} aria-hidden="true" />
      )}
    </button>
  );

  const detailOf = () => {
    if (activeNav.startsWith("structure:")) {
      return renderStructureDetail(activeNav.slice("structure:".length));
    }
    if (activeNav.startsWith("source:")) {
      return renderSourceDetail(activeNav.slice("source:".length));
    }
    if (activeNav === "rule:tags") return renderTagsDetail();
    if (activeNav === "rule:hidden") return renderHiddenTasksDetail();
    return renderAdvancedDetail();
  };

  return (
    <div class="connections">
      <h1 class="sr-only">Connections</h1>

      {phase === "loading" && (
        <p class="connections__state" role="status">Loading Connections…</p>
      )}
      {phase === "error" && (
        <section class="connections__error" role="alert">
          <strong>Connections could not be loaded</strong>
          <span>{error}</span>
          <button class="btn" onClick={() => void load()}>Reload</button>
        </section>
      )}

      {phase !== "loading" && capDraft && tagDraft && (
        <div class="connections__frame">
          <nav class="connections__nav" aria-label="Connections sections">
            <button class="connections__back" onClick={back}>← Back to planning</button>
            <div class="connections__group">
              <span class="connections__group-label">Sources</span>
              {navItem("source:todoist", "Todoist", sourceDot("todoist"))}
              {navItem("source:calendar", "Calendar", sourceDot("calendar"))}
              {navItem("source:capacities", "Capacities", sourceDot("capacities"))}
              {missingGenerationNote && (
                <span class="connections-navitem__note">{missingGenerationNote}</span>
              )}
            </div>
            <div class="connections__group">
              <span class="connections__group-label">Capacities structures</span>
              {structures.length === 0 ? (
                <span class="connections__group-empty">
                  No structures in the source mapping.
                </span>
              ) : (
                structures.map((structureId) =>
                  navItem(
                    `structure:${structureId}`,
                    navLabel(structureId),
                    structureDot(structureId),
                  ),
                )
              )}
            </div>
            <div class="connections__group">
              <span class="connections__group-label">Rules</span>
              {navItem("rule:tags", "Exclude tags")}
              {navItem(
                "rule:hidden",
                hiddenCount > 0 ? `Hidden tasks (${hiddenCount})` : "Hidden tasks",
              )}
              {navItem("rule:advanced", "Advanced")}
            </div>
          </nav>

          <section class="connections__detail">
            {detailOf()}
            {error && (
              <div class="connections__save-error" role="alert">
                <span>{error}</span>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
