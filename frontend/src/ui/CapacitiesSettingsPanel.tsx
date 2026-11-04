/* CapacitiesSettingsPanel — local TDTB assignment policy. This surface reads
   and saves the versioned local settings route only; it never discovers or
   mutates Capacities objects.

   The body is mounted by SettingsShell for the life of the shell (whether or
   not this tab is selected), so unsaved drafts survive panel switches; the
   shell discards it on close and reopening re-initializes from current state.
   The dialog chrome, focus management, and close affordance belong to the
   shell. */

import { useEffect, useState } from "preact/hooks";
import {
  CapacitiesAdmissionRow,
  admissionModeOf,
  applyAdmissionMode,
  type CapacitiesAdmissionMode,
} from "./CapacitiesAdmissionRow";
import { CapacitiesSourceEditor } from "./CapacitiesSourceEditor";
import { CapacitiesRefreshPanel } from "./CapacitiesRefreshPanel";
import { CapacitiesRulesEditor } from "./CapacitiesRulesEditor";
import { CapacitiesUnknownReview } from "./CapacitiesUnknownReview";
import { Tooltip } from "./Tooltip";
import {
  NATIVE_RULES_DETAIL,
  NATIVE_RULES_LABEL,
  NATIVE_RULES_TRIGGER_LABEL,
} from "./nativeRulesCopy";
import { useApp, useAppState } from "./context";
import type {
  AssignedItem,
  CapacitiesCatalog,
  CapacitiesNativeTaskAutoPolicy,
  CapacitiesSettings,
  CapacitiesSettingsDraft,
} from "../model/types";
import {
  capacitiesAssignedOnWarnings,
  capacitiesAssignmentWarnings,
  isCanonicalCapacitiesIdentity,
} from "../adapters/wire";
import { ApiError } from "../adapters/api";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** POST /settings/capacities/save refuses a contradictory dual declaration (one
    item type declaring an Assigned checkbox AND an Assigned On date) with its
    own 422 code before any write. The server's sentence names the structure and
    both properties, so it is shown verbatim in the save-error notice instead of
    being flattened into a generic failure. Any other status or code answers
    null, so the caller keeps the ordinary error path. */
function declarationConflictMessageOf(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const detail = error.detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return null;
  const record = detail as { code?: unknown; message?: unknown };
  if (record.code !== "capacities_settings_declaration_conflict") return null;
  if (typeof record.message === "string" && record.message.trim() !== "") {
    return record.message;
  }
  return "This save was refused: one item type cannot declare both an Assigned checkbox and an Assigned On date.";
}

/** Advisories listed inline before the rest fold into one "and N more" tail,
    so a long drift list cannot push the mapping controls off the panel. */
export const CAPACITIES_DECLARATION_DIAGNOSTIC_CAP = 5;

function draftOf(settings: CapacitiesSettings): CapacitiesSettingsDraft {
  return {
    expectedRevision: settings.revision,
    nativeTaskAuto: { ...settings.nativeTaskAuto },
    excluded: [...settings.excluded],
    // Round-tripped verbatim: this slice adds no editor, but a save must not
    // silently clear the server's active-structure inclusion set.
    activeStructures: [...settings.activeStructures],
    // Both admission keys are always sent: the save route is a full
    // replacement, so omitting either would reset it to the documented
    // default instead of leaving it unchanged.
    nativeTaskStructures: [...settings.nativeTaskStructures],
    activeStatuses: [...settings.activeStatuses],
    // The settings-declared assignment map is always sent: the save route is
    // a full replacement, so omitting it would wipe every declaration.
    assignedStructures: { ...settings.assignedStructures },
    // The read-only Assigned On map follows the same full-replacement rule.
    assignedOnStructures: { ...settings.assignedOnStructures },
  };
}

function identityOf(item: AssignedItem): string | null {
  const identity = item.capacitiesIdentity ?? item.identity ?? null;
  return isCanonicalCapacitiesIdentity(identity) ? identity : null;
}

function knownCapacitiesItems(items: AssignedItem[]): AssignedItem[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (item.source !== "capacities") return false;
    const identity = identityOf(item);
    if (!identity || seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

/** `hosted` means the Settings hub mounts the refresh, rules and unknown-review
    sections itself, so this panel must not mount a second copy. The source
    editor stays here: it needs this panel's structure titles. */
export function CapacitiesSettingsPanel({
  active,
  hosted = false,
}: {
  active: boolean;
  hosted?: boolean;
}) {
  const s = useAppState();
  const { controller, store } = useApp();
  const [settings, setSettings] = useState<CapacitiesSettings | null>(null);
  const [draft, setDraft] = useState<CapacitiesSettingsDraft | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "saving" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [horizonText, setHorizonText] = useState("");
  const [statusText, setStatusText] = useState("");
  // Admission pickers read the same provider discovery the source mapping
  // editor does. Nothing is fetched until the operator asks for it: the
  // lookup is one token-guarded read, never a mount-time provider call.
  const [catalog, setCatalog] = useState<CapacitiesCatalog | null>(null);
  const [spaceId, setSpaceId] = useState<string | null>(null);
  const [discovering, setDiscovering] = useState(false);
  // A Requires mode has no declaration until a property is picked, so the draft
  // alone cannot show it: the chosen mode is kept here until a property lands.
  const [pendingModes, setPendingModes] = useState<Record<string, CapacitiesAdmissionMode>>({});
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const close = () => store.dispatch({ type: "UI", patch: { settingsPanel: null } });

  const loadSettings = async () => {
    setPhase("loading");
    setError(null);
    try {
      const loaded = await controller.loadCapacitiesSettings();
      setSettings(loaded);
      setDraft(draftOf(loaded));
      setHorizonText(String(loaded.nativeTaskAuto.deadlineHorizonDays));
      setPhase("ready");
    } catch (e) {
      setPhase("error");
      setError(messageOf(e));
    }
  };

  useEffect(() => {
    void loadSettings();
  }, [controller]);

  if (!active) return null;

  const known = knownCapacitiesItems(s.inputs?.assigned ?? []);
  const knownIds = new Set(known.map(identityOf).filter((id): id is string => id !== null));
  const hiddenExclusions = (draft?.excluded ?? []).filter((identity) => !knownIds.has(identity));
  // The source mapping is the only non-circular inventory of
  // structures to offer. Ids the server already honours but the mapping no
  // longer lists are preserved on save, mirroring hiddenExclusions — and
  // surfaced rather than silently dropped.
  const availableStructures = settings?.availableStructures ?? [];
  // Titles are display-only advisory metadata: identity and saving always
  // key on the structure id. A title that merely repeats the id (the
  // server's fallback for a structure with no observed display name) adds
  // no information, so such a row shows the id once instead of twice.
  const structureTitleOf = (structureId: string): string | null => {
    const title = settings?.structureTitles[structureId];
    return title && title !== structureId ? title : null;
  };
  const availableStructureIds = new Set(availableStructures);
  const staleActiveStructures = (draft?.activeStructures ?? []).filter(
    (structureId) => !availableStructureIds.has(structureId),
  );
  // Same retention rule as the Active-pull set: an id the server already
  // honours but the mapping no longer lists is preserved on save and surfaced
  // instead of silently dropped.
  const staleNativeStructures = (draft?.nativeTaskStructures ?? []).filter(
    (structureId) => !availableStructureIds.has(structureId),
  );
  // A declaration whose structure the server honours but the mapping no longer
  // lists is retained on save and surfaced, exactly like the stale set above.
  const assignedStructures = draft?.assignedStructures ?? {};
  const staleAssignedStructures = Object.keys(assignedStructures)
    .filter((structureId) => !availableStructureIds.has(structureId))
    .sort();
  const assignedOnStructures = draft?.assignedOnStructures ?? {};
  const staleAssignedOnStructures = Object.keys(assignedOnStructures)
    .filter((structureId) => !availableStructureIds.has(structureId))
    .sort();
  // The backend emits one warning per declaration it could not honour. The
  // text is the server's own; the panel shows it verbatim. This is
  // deliberately NOT part of the readiness rail (the operator flagged the
  // rail as too verbose, and a configuration fact belongs beside the control
  // that declares it).
  const assignmentWarnings = capacitiesAssignmentWarnings(
    s.inputs?.sourceWarnings ?? [],
  );
  const assignedOnWarnings = capacitiesAssignedOnWarnings(
    s.inputs?.sourceWarnings ?? [],
  );
  // Read-only advisories from the server: a saved declaration names a property
  // the last published Capacities contract does not define, so it admits
  // nothing today. Non-blocking — the panel still saves, and nothing here
  // edits a mapping. The list is bounded so a long drift list stays compact.
  const declarationDiagnostics = settings?.declarationDiagnostics ?? [];
  const shownDeclarationDiagnostics = declarationDiagnostics.slice(
    0,
    CAPACITIES_DECLARATION_DIAGNOSTIC_CAP,
  );
  const hiddenDeclarationDiagnosticCount =
    declarationDiagnostics.length - shownDeclarationDiagnostics.length;
  const horizonValue = Number(horizonText);
  const horizonTextValid = /^\d+$/.test(horizonText) && Number.isSafeInteger(horizonValue);
  const policy = draft?.nativeTaskAuto;
  const horizonInvalid = policy?.deadlineEnabled === true && !horizonTextValid;

  const updatePolicy = (patch: Partial<CapacitiesNativeTaskAutoPolicy>) => {
    setDraft((current) =>
      current
        ? { ...current, nativeTaskAuto: { ...current.nativeTaskAuto, ...patch } }
        : current,
    );
  };

  const toggleExclusion = (identity: string) => {
    setDraft((current) => {
      if (!current) return current;
      const excluded = current.excluded.includes(identity)
        ? current.excluded.filter((value) => value !== identity)
        : [...current.excluded, identity].sort();
      return { ...current, excluded };
    });
  };

  const toggleActiveStructure = (structureId: string) => {
    setDraft((current) => {
      if (!current) return current;
      const activeStructures = current.activeStructures.includes(structureId)
        ? current.activeStructures.filter((value) => value !== structureId)
        : [...current.activeStructures, structureId].sort();
      return { ...current, activeStructures };
    });
  };

  const toggleNativeStructure = (structureId: string) => {
    setDraft((current) => {
      if (!current) return current;
      const nativeTaskStructures = current.nativeTaskStructures.includes(structureId)
        ? current.nativeTaskStructures.filter((value) => value !== structureId)
        : [...current.nativeTaskStructures, structureId].sort();
      return { ...current, nativeTaskStructures };
    });
  };

  // An emptied field removes the declaration; any other value is kept exactly
  // as typed (the property id is an opaque provider identifier), matching the
  // store's validation.
  const setAssignedProperty = (structureId: string, propertyId: string) => {
    setDraft((current) => {
      if (!current) return current;
      const next = { ...current.assignedStructures };
      if (propertyId === "") delete next[structureId];
      else next[structureId] = propertyId;
      return { ...current, assignedStructures: next };
    });
  };

  const setAssignedOnProperty = (structureId: string, propertyId: string) => {
    setDraft((current) => {
      if (!current) return current;
      const next = { ...current.assignedOnStructures };
      if (propertyId === "") delete next[structureId];
      else next[structureId] = propertyId;
      return { ...current, assignedOnStructures: next };
    });
  };

  // One control per type: the mode decides which draft keys exist, and any
  // Requires mode also switches the structure on. The property id travels
  // with the mode so switching between the two Requires modes keeps the
  // operator's pick when it still applies.
  const setAdmission = (structureId: string, mode: CapacitiesAdmissionMode) => {
    setPendingModes((current) => {
      const next = { ...current };
      if (mode === "assigned" || mode === "assignedOn") next[structureId] = mode;
      else delete next[structureId];
      return next;
    });
    setDraft((current) => {
      if (!current) return current;
      // Only the id already declared for THIS mode travels with the change: a
      // checkbox property is never written into the date map, or the reverse.
      const propertyId =
        mode === "assigned"
          ? (current.assignedStructures[structureId] ?? "")
          : mode === "assignedOn"
            ? (current.assignedOnStructures[structureId] ?? "")
            : "";
      return applyAdmissionMode(current, structureId, mode, propertyId);
    });
  };

  const setAdmissionProperty = (
    structureId: string,
    mode: CapacitiesAdmissionMode,
    propertyId: string,
  ) => {
    setPendingModes((current) => {
      const next = { ...current };
      if (propertyId === "") next[structureId] = mode;
      else delete next[structureId];
      return next;
    });
    setDraft((current) => {
      if (!current) return current;
      return applyAdmissionMode(current, structureId, mode, propertyId);
    });
  };

  // Discovery needs the space id, which only the source mapping record
  // carries. The read is tokenless and local; the discovery call behind it is
  // the one the mapping editor already uses. A failure is reported beside the
  // pickers and never replaces the declared ids.
  const lookUpProperties = async () => {
    if (discovering) return;
    setDiscovering(true);
    setDiscoveryError(null);
    try {
      let id = spaceId;
      if (id === null) {
        const read = await controller.loadCapacitiesSource();
        id = read.source?.spaceId ?? "";
        setSpaceId(id);
      }
      if (id === "") {
        setDiscoveryError("Save your Capacities setup first, then look up properties.");
        return;
      }
      setCatalog(await controller.discoverCapacitiesSource(id));
    } catch (e) {
      setDiscoveryError(messageOf(e));
    } finally {
      setDiscovering(false);
    }
  };

  const discoveredPropertiesOf = (structureId: string) =>
    catalog === null
      ? null
      : (catalog.structures.find((structure) => structure.structureId === structureId)
          ?.properties ?? []);

  // ``active_statuses`` is free text: the settings API exposes no status
  // inventory (the source mapping's per-structure status values never
  // cross the wire), so every saved value is an explicit entry that is kept
  // verbatim and removed only by hand.
  const addStatus = () => {
    const value = statusText.trim();
    setStatusText("");
    if (value === "") return;
    setDraft((current) => {
      if (!current || current.activeStatuses.includes(value)) return current;
      return { ...current, activeStatuses: [...current.activeStatuses, value] };
    });
  };

  const removeStatus = (status: string) => {
    setDraft((current) =>
      current
        ? { ...current, activeStatuses: current.activeStatuses.filter((value) => value !== status) }
        : current,
    );
  };

  const save = async () => {
    if (!draft || horizonInvalid) return;
    // The backend requires a valid stored horizon even when the rule is off.
    // If the disabled control contains an unfinished edit, preserve the last
    // valid policy value rather than turning an empty field into zero.
    const horizon = horizonTextValid
      ? horizonValue
      : draft.nativeTaskAuto.deadlineHorizonDays;
    if (!Number.isSafeInteger(horizon) || horizon < 0) {
      setError("Deadline horizon must be a nonnegative whole number of days.");
      return;
    }
    const next: CapacitiesSettingsDraft = {
      ...draft,
      nativeTaskAuto: { ...draft.nativeTaskAuto, deadlineHorizonDays: horizon },
    };
    setPhase("saving");
    setError(null);
    try {
      const saved = await controller.saveCapacitiesSettings(next);
      setSettings(saved);
      setDraft(draftOf(saved));
      setHorizonText(String(saved.nativeTaskAuto.deadlineHorizonDays));
      close();
    } catch (e) {
      setPhase("ready");
      setError(declarationConflictMessageOf(e) ?? messageOf(e));
    }
  };

  return (
    <div class="settings-panel settings-panel--capacities">
        <ul class="capacities-settings__concepts" aria-label="Which section to use">
          <li>Use “How each item type gets in” to pick which Capacities types can appear, by Assigned or Assigned On date, or when Active.</li>
          <li>Use “Automatic task rules” to bring in task-type items that are active, due, or have a deadline soon.</li>
          <li>Use “Fine-tune by item type” (in What shows up in my plan?) only rarely, for per-type tweaks to what you already see.</li>
        </ul>
        <p class="capacities-settings__intro">
          TDTB assignment policy is stored locally and keyed by stable Capacities identity.
          Saving here does not write object properties, discover provider data, or activate a live route.
        </p>

        {phase === "loading" && (
          <p class="capacities-settings__state" role="status">Loading local policy…</p>
        )}
        {phase === "error" && (
          <section class="capacities-settings__error" role="alert">
            <strong>Settings could not be loaded</strong>
            <span>{error}</span>
            <button class="btn" onClick={() => void loadSettings()}>Reload settings</button>
          </section>
        )}

        {draft && phase !== "loading" && (
          <>
            {declarationDiagnostics.length > 0 && (
              <section
                class="capacities-rule__notice capacities-declarations"
                aria-label="Saved mappings the last published Capacities contract does not define"
              >
                <p class="capacities-declarations__head">
                  <span class="chip chip--warn">
                    {declarationDiagnostics.length === 1
                      ? "1 mapping does nothing yet"
                      : `${declarationDiagnostics.length} mappings do nothing yet`}
                  </span>
                  <span>
                    This is a saved setting, not a read failure. Planning still works; pick a live
                    property under “How each item type gets in”, or clear the mapping.
                  </span>
                </p>
                <ul class="capacities-declarations__list">
                  {shownDeclarationDiagnostics.map((diagnostic) => (
                    <li key={`${diagnostic.structureId}:${diagnostic.reason}`}>
                      {/* The cached title is display-only and may be absent, so
                          the id is the fallback rather than an invented name. */}
                      <strong>
                        {diagnostic.structureLabel !== ""
                          ? diagnostic.structureLabel
                          : diagnostic.structureId}
                      </strong>
                      <span class="capacities-declarations__reason">{diagnostic.reason}</span>
                    </li>
                  ))}
                </ul>
                {hiddenDeclarationDiagnosticCount > 0 && (
                  <p class="capacities-declarations__more">
                    and {hiddenDeclarationDiagnosticCount} more
                  </p>
                )}
              </section>
            )}
            <section class="setup-section capacities-settings__section" aria-labelledby="capacities-sec-auto">
              <div class="setup-section__head">
                <h3 id="capacities-sec-auto">Automatic task rules</h3>
                <Tooltip
                  label={NATIVE_RULES_LABEL}
                  detail={NATIVE_RULES_DETAIL}
                  bubbleClass="tooltip--wide"
                >
                  <button type="button" class="iconbtn" aria-label={NATIVE_RULES_TRIGGER_LABEL}>
                    ?
                  </button>
                </Tooltip>
                <span class="capacities-settings__revision">
                  {settings?.persisted ? `Saved (version ${settings.revision})` : "Using defaults"}
                </span>
              </div>
              <div class="setup-section__body">
                <p class="capacities-settings__hint">
                  These rules are OR conditions for the structures admitted below. Completed and dropped objects remain excluded.
                </p>
                <label class="capacities-setting-row">
                  <input
                    type="checkbox"
                    checked={policy?.activeEnabled ?? false}
                    onChange={(e) => updatePolicy({ activeEnabled: (e.currentTarget as HTMLInputElement).checked })}
                  />
                  <span>
                    <strong>Active status</strong>
                    <small>Include a task when its source status matches a configured status value.</small>
                  </span>
                </label>
                <label class="capacities-setting-row">
                  <input
                    type="checkbox"
                    checked={policy?.dueEnabled ?? false}
                    onChange={(e) => updatePolicy({ dueEnabled: (e.currentTarget as HTMLInputElement).checked })}
                  />
                  <span>
                    <strong>Due today or overdue</strong>
                    <small>Include a task whose due date is today or earlier.</small>
                  </span>
                </label>
                <label class="capacities-setting-row">
                  <input
                    type="checkbox"
                    checked={policy?.deadlineEnabled ?? false}
                    onChange={(e) => updatePolicy({ deadlineEnabled: (e.currentTarget as HTMLInputElement).checked })}
                  />
                  <span>
                    <strong>Deadline window</strong>
                    <small>Include a task whose deadline is overdue or within the horizon below.</small>
                  </span>
                </label>
                <div class="field capacities-settings__horizon">
                  <label for="capacities-deadline-horizon">Deadline coming up</label>
                  <div class="capacities-settings__horizon-control">
                    <input
                      id="capacities-deadline-horizon"
                      type="number"
                      min="0"
                      step="1"
                      value={horizonText}
                      disabled={!policy?.deadlineEnabled}
                      onInput={(e) => {
                        const text = (e.currentTarget as HTMLInputElement).value;
                        setHorizonText(text);
                        const value = Number(text);
                        if (Number.isSafeInteger(value) && value >= 0) {
                          updatePolicy({ deadlineHorizonDays: value });
                        }
                      }}
                    />
                    <span>calendar days</span>
                  </div>
                  {horizonInvalid && <span class="field-error" role="alert">Use a nonnegative whole number.</span>}
                </div>
              </div>
            </section>

            <section class="setup-section capacities-settings__section" aria-labelledby="capacities-sec-admission">
              <div class="setup-section__head">
                <h3 id="capacities-sec-admission">What counts as active</h3>
                {staleNativeStructures.length > 0 && (
                  <span class="capacities-settings__revision">
                    {staleNativeStructures.length} saved {staleNativeStructures.length === 1 ? "structure" : "structures"} not set up to appear
                  </span>
                )}
              </div>
              <div class="setup-section__body">
                <h4>Types with automatic task rules</h4>
                <p class="capacities-settings__hint">
                  Automatic task rules apply only to the types switched on here. The list comes from your Capacities setup (the same list as the per-type admission control below), not from today's plan.
                </p>
                {availableStructures.length === 0 ? (
                  <p class="capacities-settings__empty">
                    Your Capacities setup lists no types yet; saved choices are kept when you save.
                  </p>
                ) : (
                  <div class="capacities-object-list" role="group" aria-label="Capacities structures admitted to the native Task Auto rules">
                    {availableStructures.map((structureId) => {
                      const admitted = draft.nativeTaskStructures.includes(structureId);
                      const title = structureTitleOf(structureId);
                      return (
                        <div class="capacities-object-row" key={structureId}>
                          <div class="capacities-object-row__identity">
                            {title !== null && <strong>{title}</strong>}
                            <code>{structureId}</code>
                          </div>
                          <div class="capacities-object-row__admission">
                            <label class="capacities-object-row__toggle">
                              <input
                                type="checkbox"
                                checked={admitted}
                                aria-label={`Native admission for ${structureId}`}
                                onChange={() => toggleNativeStructure(structureId)}
                              />
                              <span>{admitted ? "Automatic rules on" : "Automatic rules off"}</span>
                            </label>
                            {/* The row sits at the right edge of the panel, so
                                a centred 340px bubble would run off the window
                                and clip its own text. */}
                            <Tooltip
                              label={NATIVE_RULES_LABEL}
                              detail={NATIVE_RULES_DETAIL}
                              bubbleClass="tooltip--wide"
                              align="end"
                            >
                              <button
                                type="button"
                                class="iconbtn"
                                aria-label={NATIVE_RULES_TRIGGER_LABEL}
                              >
                                ?
                              </button>
                            </Tooltip>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
                {staleNativeStructures.length > 0 && (
                  <div class="capacities-exclusion-list">
                    <h4>Types not set up to appear (automatic rules)</h4>
                    <p class="capacities-settings__hint">
                      These {staleNativeStructures.length} saved {staleNativeStructures.length === 1 ? "id is" : "ids are"} not set up to appear in your Capacities setup. They are kept as they are when you save.
                    </p>
                    {staleNativeStructures.map((structureId) => (
                      <div class="capacities-exclusion-row" key={structureId}>
                        <code>{structureId}</code>
                        <button class="btn" onClick={() => toggleNativeStructure(structureId)}>Remove</button>
                      </div>
                    ))}
                  </div>
                )}

                <h4>Status values</h4>
                <p class="capacities-settings__hint">
                  A native-structure task is admitted by the Active status rule when its status matches one of these values (case- and whitespace-insensitive). The API exposes no status inventory, so this list is free-text; saved values are kept exactly.
                </p>
                <div class="field">
                  <label for="capacities-status-value">Add a status value</label>
                  <div class="capacities-settings__horizon-control">
                    <input
                      id="capacities-status-value"
                      type="text"
                      value={statusText}
                      placeholder="e.g. In Progress"
                      onInput={(e) => setStatusText((e.currentTarget as HTMLInputElement).value)}
                    />
                    <button class="btn" onClick={addStatus} disabled={statusText.trim() === ""}>Add status</button>
                  </div>
                </div>
                {draft.activeStatuses.length === 0 ? (
                  <p class="capacities-settings__empty">
                    No status values are configured, so the Active status rule admits nothing.
                  </p>
                ) : (
                  <div class="capacities-exclusion-list">
                    {draft.activeStatuses.map((status) => (
                      <div class="capacities-exclusion-row" key={status}>
                        <code>{status}</code>
                        <button class="btn" onClick={() => removeStatus(status)} aria-label={`Remove status ${status}`}>Remove</button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>

            <section class="setup-section capacities-settings__section" aria-labelledby="capacities-sec-admission-per-type" data-settings-section="admission-per-type">
              <div class="setup-section__head">
                <h3 id="capacities-sec-admission-per-type">How each item type gets in</h3>
                {staleActiveStructures.length + staleAssignedStructures.length + staleAssignedOnStructures.length > 0 && (
                  <span class="capacities-settings__revision">
                    {staleActiveStructures.length + staleAssignedStructures.length + staleAssignedOnStructures.length} saved {staleActiveStructures.length + staleAssignedStructures.length + staleAssignedOnStructures.length === 1 ? "structure" : "structures"} not set up to appear
                  </span>
                )}
              </div>
              <div class="setup-section__body">
                <p class="capacities-settings__hint">
                  One control per type says how its items get in. “Status only” brings in items whose status is Active. A Requires mode also brings the type in, and additionally needs a Capacities property: a date (the “Assigned to me” date, today or earlier) or an older checkbox. The list comes from your Capacities setup, not from today's plan.
                </p>
                {discoveryError !== null && (
                  <div class="capacities-admission__warn" role="alert">
                    {discoveryError}
                  </div>
                )}
                {assignmentWarnings.length > 0 && (
                  <div class="capacities-exclusion-list">
                    <h4>Settings we skipped</h4>
                    <p class="capacities-settings__hint">
                      These entries could not be understood, so we left them out this time. The wording is the server's own; correct the property id below and save.
                    </p>
                    {assignmentWarnings.map((warning) => (
                      <div class="capacities-exclusion-row" key={warning}>
                        <code>{warning}</code>
                      </div>
                    ))}
                  </div>
                )}
                {assignedOnWarnings.length > 0 && (
                  <div class="capacities-exclusion-list">
                    <h4>Notes about the “Assigned to me” date</h4>
                    <p class="capacities-settings__hint">
                      These notes came from the latest read. The wording is the server's own.
                    </p>
                    {assignedOnWarnings.map((warning) => (
                      <div class="capacities-exclusion-row" key={warning}>
                        <code>{warning}</code>
                      </div>
                    ))}
                  </div>
                )}
                {availableStructures.length === 0 ? (
                  <p class="capacities-settings__empty">
                    Your Capacities setup lists no types yet; saved settings are kept when you save.
                  </p>
                ) : (
                  <div class="capacities-object-list" role="group" aria-label="Capacities admission per item type">
                    {availableStructures.map((structureId) => {
                      const mode = pendingModes[structureId] ?? admissionModeOf(draft, structureId);
                      const declaredPropertyId =
                        assignedOnStructures[structureId] ?? assignedStructures[structureId] ?? "";
                      return (
                        <CapacitiesAdmissionRow
                          key={structureId}
                          structureId={structureId}
                          title={structureTitleOf(structureId)}
                          mode={mode}
                          declaredPropertyId={declaredPropertyId}
                          properties={discoveredPropertiesOf(structureId)}
                          discovering={discovering}
                          declaredButOff={
                            declaredPropertyId !== "" &&
                            !draft.activeStructures.includes(structureId)
                          }
                          onModeChange={(next) => setAdmission(structureId, next)}
                          onPropertyChange={(propertyId) =>
                            setAdmissionProperty(structureId, mode, propertyId)
                          }
                          onLookUp={() => void lookUpProperties()}
                        />
                      );
                    })}
                  </div>
                )}
                {staleActiveStructures.length > 0 && (
                  <div class="capacities-exclusion-list">
                    <h4>Active-item types that are not set up to appear</h4>
                    <p class="capacities-settings__hint">
                      These {staleActiveStructures.length} saved {staleActiveStructures.length === 1 ? "id is" : "ids are"} not set up to appear in your Capacities setup. They are kept as they are when you save.
                    </p>
                    {staleActiveStructures.map((structureId) => (
                      <div class="capacities-exclusion-row" key={structureId}>
                        <code>{structureId}</code>
                        <button class="btn" onClick={() => toggleActiveStructure(structureId)}>Remove</button>
                      </div>
                    ))}
                  </div>
                )}
                {staleAssignedStructures.length > 0 && (
                  <div class="capacities-exclusion-list">
                    <h4>Types with settings that are not set up to appear</h4>
                    <p class="capacities-settings__hint">
                      These {staleAssignedStructures.length} saved {staleAssignedStructures.length === 1 ? "id is" : "ids are"} not set up to appear in your Capacities setup. They are kept as they are when you save.
                    </p>
                    {staleAssignedStructures.map((structureId) => (
                      <div class="capacities-exclusion-row" key={structureId}>
                        <code>{structureId}</code>
                        <code>{assignedStructures[structureId]}</code>
                        <button class="btn" onClick={() => setAssignedProperty(structureId, "")}>Remove</button>
                      </div>
                    ))}
                  </div>
                )}
                {staleAssignedOnStructures.length > 0 && (
                  <div class="capacities-exclusion-list">
                    <h4>Types with an “Assigned to me” date that are not set up to appear</h4>
                    <p class="capacities-settings__hint">
                      These {staleAssignedOnStructures.length} saved {staleAssignedOnStructures.length === 1 ? "id is" : "ids are"} not set up to appear in your Capacities setup. They are kept as they are when you save.
                    </p>
                    {staleAssignedOnStructures.map((structureId) => (
                      <div class="capacities-exclusion-row" key={structureId}>
                        <code>{structureId}</code>
                        <code>{assignedOnStructures[structureId]}</code>
                        <button class="btn" onClick={() => setAssignedOnProperty(structureId, "")}>Remove</button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>

            <section class="setup-section capacities-settings__section" aria-labelledby="capacities-sec-objects">
              <div class="setup-section__head">
                <h3 id="capacities-sec-objects">Known Capacities items</h3>
              </div>
              <div class="setup-section__body">
                <p class="capacities-settings__hint">
                  This list only covers items from the latest refresh. It is not a full browser of Capacities.
                  An item that Capacities itself marks as assigned still wins over an exclusion set in TDTB.
                </p>
                {known.length === 0 ? (
                  <p class="capacities-settings__empty">
                    No Capacities objects are present in the current plan inputs.
                  </p>
                ) : (
                  <div class="capacities-object-list">
                    {known.map((item) => {
                      const identity = identityOf(item)!;
                      const excluded = draft.excluded.includes(identity);
                      return (
                        <div class="capacities-object-row" key={identity}>
                          <div class="capacities-object-row__identity">
                            <strong>{item.name}</strong>
                            <small>{item.types.length > 0 ? item.types.join(" · ") : "Structure not named"}</small>
                            <code>{identity}</code>
                          </div>
                          <label class="capacities-object-row__toggle">
                            <input
                              type="checkbox"
                              checked={excluded}
                              onChange={() => toggleExclusion(identity)}
                            />
                            <span>{excluded ? "Excluded in TDTB" : "Available to TDTB"}</span>
                          </label>
                        </div>
                      );
                    })}
                  </div>
                )}
                {hiddenExclusions.length > 0 && (
                  <div class="capacities-exclusion-list">
                    <h4>Saved exclusions outside this snapshot</h4>
                    {hiddenExclusions.map((identity) => (
                      <div class="capacities-exclusion-row" key={identity}>
                        <code>{identity}</code>
                        <button class="btn" onClick={() => toggleExclusion(identity)}>Remove</button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>

            <p class="capacities-settings__footer-note">
              Revision conflicts keep the drawer open so you can reload before replacing newer settings.
            </p>
            {error && (
              <div class="capacities-settings__save-error" role="alert">
                <span>{error}</span>
                <button class="btn" onClick={() => void loadSettings()}>Reload settings</button>
              </div>
            )}
            <div class="editor__actions setup__actions">
              <button class="btn" onClick={close}>Cancel</button>
              <button class="btn btn--primary" onClick={() => void save()} disabled={phase === "saving" || horizonInvalid}>
                {phase === "saving" ? "Saving…" : "Save Capacities settings"}
              </button>
            </div>
          </>
        )}
        {/* The mapping editor owns its own read/save route and save
            lifecycle, but it mounts only once the settings read has
            settled: the shell's destination-focus helper lands on the
            panel's FIRST h3, and this section must not introduce one (while
            the settings sections are absent) before the panel's own heading
            exists. It stays available when the settings read failed. */}
        {phase !== "loading" && (
          <CapacitiesSourceEditor structureTitleOf={structureTitleOf} />
        )}
        {/* Connections surfaces beyond the local policy body. Gated on the
            settings read settling, exactly like the mapping editor: the
            shell's destination-focus helper lands on the panel's FIRST h3,
            so these sections must not introduce one before the policy
            headings exist. Each owns its own read/save route and lifecycle,
            and none runs a provider call on mount (only tokenless local
            status/rule/selection reads). */}
        {phase !== "loading" && !hosted && (
          <>
            <CapacitiesRefreshPanel active={active} />
            <CapacitiesRulesEditor active={active} />
            <CapacitiesUnknownReview active={active} />
          </>
        )}
    </div>
  );
}
