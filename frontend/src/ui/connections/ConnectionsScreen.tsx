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
import { capacitiesAssignmentWarnings, isCanonicalCapacitiesIdentity } from "../../adapters/wire";
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
  };
}

function tagDraftOf(settings: TagExclusionSettings): TagExclusionSettingsDraft {
  return { expectedRevision: settings.revision, tags: [...settings.tags] };
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
  if (!sameJson(draft.assignedStructures, base.assignedStructures)) {
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
      return {
        ...current,
        activeStructures: current.activeStructures.filter((v) => v !== structureId),
        nativeTaskStructures: current.nativeTaskStructures.filter((v) => v !== structureId),
        assignedStructures: next,
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
  const primary = structures[0] ?? null;

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

  const assignmentWarnings = capacitiesAssignmentWarnings(s.inputs?.sourceWarnings ?? []);

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
  const previewRows: PreviewRow[] = known.map((item) => {
    const tags = item.tags ?? [];
    if (tags.some((tag) => excludedTagIds.has(tag))) {
      return { title: item.name, reason: "left out by tag" };
    }
    const structure = item.capacitiesStructureId ?? "";
    if (!(structure in (capDraft?.assignedStructures ?? {}))) {
      return { title: item.name, reason: "waiting on mapping" };
    }
    return { title: item.name, reason: "admitted" };
  });
  const admittedCount = previewRows.filter((r) => r.reason === "admitted").length;
  const leftOutCount = previewRows.filter((r) => r.reason === "left out by tag").length;
  const waitingCount = previewRows.filter((r) => r.reason === "waiting on mapping").length;

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

  /* -- render ------------------------------------------------------------- */

  const renderAdmitWhen = () => {
    if (!capDraft) return null;
    const policy = capDraft.nativeTaskAuto;
    return (
      <div class="connections-card__col">
        <h4 class="connections-card__eyebrow">Admit when</h4>
        <label class="connections-check">
          <input
            type="checkbox"
            checked={policy.activeEnabled}
            onChange={(e) => updatePolicy({ activeEnabled: (e.currentTarget as HTMLInputElement).checked })}
          />
          <span>Status is active</span>
        </label>
        <label class="connections-check">
          <input
            type="checkbox"
            checked={policy.dueEnabled}
            onChange={(e) => updatePolicy({ dueEnabled: (e.currentTarget as HTMLInputElement).checked })}
          />
          <span>Due today or overdue</span>
        </label>
        <label class="connections-check">
          <input
            type="checkbox"
            checked={policy.deadlineEnabled}
            onChange={(e) => updatePolicy({ deadlineEnabled: (e.currentTarget as HTMLInputElement).checked })}
          />
          <span>Deadline within</span>
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
              if (Number.isSafeInteger(value) && value >= 0) updatePolicy({ deadlineHorizonDays: value });
            }}
          />
          <span>days</span>
        </label>
        {horizonInvalid && (
          <span class="field-error" role="alert">Use a nonnegative whole number.</span>
        )}
        <div class="connections-active-means">
          <span class="connections-card__eyebrow">Active means</span>
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
    );
  };

  const renderMapping = (structureId: string, withDiscover: boolean) => {
    const value = capDraft?.assignedStructures[structureId] ?? "";
    return (
      <div class="connections-card__col connections-card__col--mapping">
        <div class="connections-mapping__head">
          <h4 class="connections-card__eyebrow">Property mapping</h4>
          {withDiscover && (
            <button class="btn btn--primary" onClick={() => void discover()}>
              Discover from Capacities
            </button>
          )}
        </div>
        <input
          class="connections-mapping__input"
          type="text"
          list={`connections-props-${structureId}`}
          aria-label={`Mapping for ${structureId}`}
          placeholder="property id"
          value={value}
          onInput={(e) => setMapping(structureId, (e.currentTarget as HTMLInputElement).value)}
        />
        <datalist id={`connections-props-${structureId}`}>
          {mappingChoices(structureId).map((propertyId) => (
            <option key={propertyId} value={propertyId} />
          ))}
        </datalist>
      </div>
    );
  };

  return (
    <div class="connections">
      <header class="connections__subhead">
        <button class="connections__back" onClick={back}>← Back to planning</button>
        <h1 class="connections__title">Connections</h1>
        <span class="connections__sync" role="status">{syncText}</span>
      </header>

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
        <div class="connections__body">
          <div class="connections__left">
            <h2 class="connections__lead">Which Capacities items reach your morning list</h2>
            <p class="connections__explainer">
              An item is admitted when any rule below matches. Completed and dropped items are always left out.
            </p>

            <table class="connections-table" aria-label="Capacities structures">
              <thead>
                <tr>
                  <th scope="col">STRUCTURE</th>
                  <th scope="col">NATIVE RULES</th>
                  <th scope="col">ACTIVE PULL</th>
                  <th scope="col">ASSIGNED FLAG</th>
                  <th scope="col">MAPPING</th>
                </tr>
              </thead>
              <tbody>
                {structures.length === 0 ? (
                  <tr>
                    <td colSpan={5} class="connections-table__empty">
                      The Capacities source mapping lists no structures.
                    </td>
                  </tr>
                ) : (
                  structures.map((structureId) => (
                    <tr key={structureId}>
                      <td>
                        {titleOf(structureId) && <strong>{titleOf(structureId)} </strong>}
                        <code>{structureId}</code>
                      </td>
                      <td>{capDraft.nativeTaskStructures.includes(structureId) ? "On" : "Off"}</td>
                      <td>{capDraft.activeStructures.includes(structureId) ? "On" : "Off"}</td>
                      <td>{capDraft.assignedStructures[structureId] ?? "—"}</td>
                      <td>{capDraft.assignedStructures[structureId] ? "Mapped" : "—"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>

            {structures.length === 0 ? (
              <section class="connections-card" aria-label="Admission rules">
                <header class="connections-card__head">
                  <h3 class="connections-card__title">Admission rules</h3>
                </header>
                <div class="connections-card__cols">
                  {renderAdmitWhen()}
                  {renderMapping(primary ?? "", false)}
                </div>
              </section>
            ) : (
              structures.map((structureId, index) => (
                <section class="connections-card" key={structureId} aria-label={`${titleOf(structureId) ?? structureId} ${structureId}`}>
                  <header class="connections-card__head">
                    <h3 class="connections-card__title">{titleOf(structureId) ?? structureId}</h3>
                    <code class="connections-card__id">{structureId}</code>
                    <button class="connections-card__remove" onClick={() => removeStructure(structureId)}>
                      Remove structure
                    </button>
                  </header>
                  <div class="connections-card__cols">
                    {index === 0 ? (
                      renderAdmitWhen()
                    ) : (
                      <div class="connections-card__col">
                        <h4 class="connections-card__eyebrow">Admit when</h4>
                        <label class="connections-check">
                          <input
                            type="checkbox"
                            checked={capDraft.activeStructures.includes(structureId)}
                            aria-label={`Include in active pull for ${structureId}`}
                            onChange={() => toggleActiveStructure(structureId)}
                          />
                          <span>Include in active pull</span>
                        </label>
                        <label class="connections-check">
                          <input
                            type="checkbox"
                            checked={capDraft.nativeTaskStructures.includes(structureId)}
                            aria-label={`Native rules for ${structureId}`}
                            onChange={() => toggleNativeStructure(structureId)}
                          />
                          <span>Use native rules</span>
                        </label>
                      </div>
                    )}
                    {renderMapping(structureId, index === 0)}
                  </div>
                </section>
              ))
            )}

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
                        (tag.title ?? "").toLocaleLowerCase().includes(tagQuery.trim().toLocaleLowerCase()),
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

            <details class="connections-advanced">
              <summary>Advanced</summary>
              <div class="connections-advanced__body">
                <CapacitiesSourceEditor structureTitleOf={titleOf} />
                <CapacitiesRefreshPanel active={true} />
                <CapacitiesUnknownReview active={true} />
                {assignmentWarnings.length > 0 && (
                  <section class="setup-section" aria-label="Ignored declarations">
                    <h4>Ignored declarations in the current read</h4>
                    {assignmentWarnings.map((warning) => (
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
            </details>

            {error && (
              <div class="connections__save-error" role="alert">
                <span>{error}</span>
              </div>
            )}
          </div>

          <aside class="connections__right">
            <section class="connections-preview" role="region" aria-label="Preview · Today">
              <div class="connections-preview__eyebrow">PREVIEW · TODAY</div>
              <div class="connections-preview__count">
                {admittedCount} item{admittedCount === 1 ? "" : "s"} admitted
              </div>
              <div class="connections-preview__split">
                {leftOutCount} left out by tag · {waitingCount} waiting on mapping
              </div>
              <ul class="connections-preview__list">
                {previewRows.map((row) => (
                  <li key={row.title}>
                    <span class="connections-preview__t">{row.title}</span>
                    <span class="connections-preview__why">{row.reason}</span>
                  </li>
                ))}
              </ul>
            </section>
          </aside>
        </div>
      )}
    </div>
  );
}
