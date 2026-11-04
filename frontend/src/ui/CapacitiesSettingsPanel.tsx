/* CapacitiesSettingsPanel — local TDTB admission policy. This surface reads and
   saves the versioned local settings route only; it never discovers or mutates
   Capacities objects.

   Schema version 2 states ONE rule per item type, so the panel is:
     - one shared status vocabulary (open + completed),
     - the native deadline horizon,
     - one row per type with a five-option rule selector.
   The previous three overlapping surfaces (automatic-task toggles, per-type
   Active/Assigned/Assigned On switches, and a bare status list) are gone; every
   one of them was a partial statement of the same rule.

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
  applyCustomStatusNames,
  applyStatusChoice,
  declarationOf,
  statusChoiceOf,
  usesStatus,
  isPropertyMode,
  type CapacitiesAdmissionMode,
  type CapacitiesStatusChoice,
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
  CapacitiesSettings,
  CapacitiesSettingsDraft,
  CapacitiesTypeDeclaration,
} from "../model/types";
import {
  capacitiesAssignedOnWarnings,
  capacitiesAssignmentWarnings,
  capacitiesSettingsToWire,
  isCanonicalCapacitiesIdentity,
} from "../adapters/wire";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Advisories listed inline before the rest fold into one "and N more" tail,
    so a long drift list cannot push the rule controls off the panel. */
export const CAPACITIES_DECLARATION_DIAGNOSTIC_CAP = 5;

/** The draft is a full-replacement document: every key the save route replaces
    wholesale is copied here, including each declaration's `statuses`
    tri-state, which must reach the wire unchanged. */
function draftOf(settings: CapacitiesSettings): CapacitiesSettingsDraft {
  const types: Record<string, CapacitiesTypeDeclaration> = {};
  for (const [structureId, declaration] of Object.entries(settings.types)) {
    types[structureId] = {
      mode: declaration.mode,
      property: declaration.property,
      statuses: declaration.statuses === null ? null : [...declaration.statuses],
    };
  }
  return {
    expectedRevision: settings.revision,
    statuses: {
      open: [...settings.statuses.open],
      completed: [...settings.statuses.completed],
    },
    types,
    native: { deadlineHorizonDays: settings.native.deadlineHorizonDays },
    excluded: [...settings.excluded],
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
  const [openText, setOpenText] = useState("");
  const [completedText, setCompletedText] = useState("");
  // Admission pickers read the same provider discovery the source mapping
  // editor does. Nothing is fetched until the operator asks for it: the
  // lookup is one token-guarded read, never a mount-time provider call.
  const [catalog, setCatalog] = useState<CapacitiesCatalog | null>(null);
  const [spaceId, setSpaceId] = useState<string | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const close = () => store.dispatch({ type: "UI", patch: { settingsPanel: null } });

  const loadSettings = async () => {
    setPhase("loading");
    setError(null);
    try {
      const loaded = await controller.loadCapacitiesSettings();
      setSettings(loaded);
      setDraft(draftOf(loaded));
      setHorizonText(String(loaded.native.deadlineHorizonDays));
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
  // The source mapping is the only non-circular inventory of structures to
  // offer. Ids the server already holds a declaration for but the mapping no
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
  const staleStructureIds = Object.keys(draft?.types ?? {})
    .filter((structureId) => !availableStructureIds.has(structureId))
    .sort();
  // The backend emits one warning per declaration it could not honour. The
  // text is the server's own; the panel shows it verbatim. This is
  // deliberately NOT part of the readiness rail (the operator flagged the
  // rail as too verbose, and a configuration fact belongs beside the control
  // that declares it).
  const assignmentWarnings = capacitiesAssignmentWarnings(s.inputs?.sourceWarnings ?? []);
  const assignedOnWarnings = capacitiesAssignedOnWarnings(s.inputs?.sourceWarnings ?? []);
  // Read-only advisories from the server: a saved declaration names a property
  // the last published Capacities contract does not define, so it admits
  // nothing today. Non-blocking — the panel still saves, and nothing here
  // edits a declaration. The list is bounded so a long drift list stays compact.
  const declarationDiagnostics = settings?.declarationDiagnostics ?? [];
  const shownDeclarationDiagnostics = declarationDiagnostics.slice(
    0,
    CAPACITIES_DECLARATION_DIAGNOSTIC_CAP,
  );
  const hiddenDeclarationDiagnosticCount =
    declarationDiagnostics.length - shownDeclarationDiagnostics.length;
  const horizonValue = Number(horizonText);
  const horizonTextValid = /^\d+$/.test(horizonText) && Number.isSafeInteger(horizonValue);
  // A property rule with no property cannot be saved: the route requires a raw
  // property id for exactly those two modes. The panel says which types are
  // unfinished instead of letting the save fail.
  const unfinishedPropertyIds = Object.entries(draft?.types ?? {})
    .filter(([, declaration]) => isPropertyMode(declaration.mode) && declaration.property === null)
    .map(([structureId]) => structureId)
    .sort();
  const saveBlocked = !horizonTextValid || unfinishedPropertyIds.length > 0;

  const setStatusNameList = (
    values: (current: CapacitiesSettingsDraft) => CapacitiesSettingsDraft,
  ) => setDraft((current) => (current ? values(current) : current));

  const addVocabularyName = (dimension: "open" | "completed") => {
    const text = dimension === "open" ? openText : completedText;
    const value = text.trim();
    if (dimension === "open") setOpenText("");
    else setCompletedText("");
    if (value === "") return;
    setStatusNameList((current) => {
      const names = current.statuses[dimension];
      if (names.includes(value)) return current;
      return {
        ...current,
        statuses: { ...current.statuses, [dimension]: [...names, value].sort() },
      };
    });
  };

  const removeVocabularyName = (dimension: "open" | "completed", name: string) => {
    setStatusNameList((current) => ({
      ...current,
      statuses: {
        ...current.statuses,
        [dimension]: current.statuses[dimension].filter((value) => value !== name),
      },
    }));
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

  const setMode = (structureId: string, mode: CapacitiesAdmissionMode) => {
    setDraft((current) => {
      if (!current) return current;
      const declared = declarationOf(current, structureId);
      // Switching between the two property rules keeps the operator's pick
      // when it still applies; anything else drops the raw id, because a mode
      // that declares no property must not send one.
      const propertyId = isPropertyMode(mode) && isPropertyMode(declared.mode)
        ? (declared.property ?? "")
        : "";
      return applyAdmissionMode(current, structureId, mode, propertyId);
    });
  };

  const setProperty = (structureId: string, propertyId: string) => {
    setDraft((current) =>
      current
        ? applyAdmissionMode(current, structureId, admissionModeOf(current, structureId), propertyId)
        : current,
    );
  };

  const setStatusChoice = (
    structureId: string,
    choice: CapacitiesStatusChoice,
  ) => {
    setDraft((current) => {
      if (!current) return current;
      const declared = declarationOf(current, structureId);
      // Picking "custom" for a declaration that inherits starts from the shared
      // list, so the operator edits names instead of an empty field.
      const names =
        choice === "custom" && declared.statuses === null
          ? [...(current.statuses.open ?? [])]
          : (declared.statuses ?? []);
      return applyStatusChoice(current, structureId, choice, names);
    });
  };

  const setCustomStatusNames = (structureId: string, names: string[]) => {
    setDraft((current) =>
      current ? applyCustomStatusNames(current, structureId, names) : current,
    );
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

  const save = async () => {
    if (!draft || saveBlocked) return;
    // The backend requires a valid stored horizon even when no type uses the
    // native rules today.
    const horizon = horizonTextValid ? horizonValue : draft.native.deadlineHorizonDays;
    if (!Number.isSafeInteger(horizon) || horizon < 0) {
      setError("Deadline horizon must be a nonnegative whole number of days.");
      return;
    }
    const next: CapacitiesSettingsDraft = {
      ...draft,
      native: { deadlineHorizonDays: horizon },
    };
    // A malformed draft must fail here, in the operator's own words, rather
    // than at the route: the wire builder owns the exact schema-v2 contract.
    try {
      capacitiesSettingsToWire(next);
    } catch (e) {
      setError(messageOf(e));
      return;
    }
    setPhase("saving");
    setError(null);
    try {
      const saved = await controller.saveCapacitiesSettings(next);
      setSettings(saved);
      setDraft(draftOf(saved));
      setHorizonText(String(saved.native.deadlineHorizonDays));
      close();
    } catch (e) {
      setPhase("ready");
      setError(messageOf(e));
    }
  };

  const migrated = settings?.migratedFrom === 1;

  return (
    <div class="settings-panel settings-panel--capacities">
        <p class="capacities-settings__concepts">
          Each type has one rule for getting into your plan.
        </p>
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
            {migrated && (
              <section class="capacities-rule__notice capacities-settings__migration" role="status">
                Your saved settings use the older format; saving rewrites them and keeps a backup.
              </section>
            )}
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
                    property in the rule below, or switch that type off.
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

            <section class="setup-section capacities-settings__section" aria-labelledby="capacities-sec-per-type" data-settings-section="admission-per-type">
              <div class="setup-section__head">
                <h3 id="capacities-sec-per-type">How each item type gets in</h3>
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
                  Each type has exactly one rule. “Active by status” brings in items whose status is
                  one of the open names below. “Assigned checkbox” and “Assigned On date” need a
                  Capacities property of their own kind. “Native task rules” use status, the due
                  date, and the deadline window together.
                </p>

                <div class="capacities-settings__vocabulary">
                  {(["open", "completed"] as const).map((dimension) => (
                    <div class="field" key={dimension}>
                      <h4>
                        {dimension === "open" ? "Open statuses" : "Completed statuses"}
                      </h4>
                      <p class="capacities-settings__hint">
                        {dimension === "open"
                          ? "A type that inherits this list treats these names as open. Names are compared case- and whitespace-insensitively."
                          : "These names always stay out of the plan, whatever a type's rule is."}
                      </p>
                      <div class="capacities-settings__horizon-control">
                        <input
                          type="text"
                          aria-label={`Add an ${dimension} status name`}
                          placeholder={dimension === "open" ? "e.g. In Progress" : "e.g. Dropped"}
                          value={dimension === "open" ? openText : completedText}
                          onInput={(e) => {
                            const text = (e.currentTarget as HTMLInputElement).value;
                            if (dimension === "open") setOpenText(text);
                            else setCompletedText(text);
                          }}
                        />
                        <button
                          class="btn"
                          onClick={() => addVocabularyName(dimension)}
                          disabled={(dimension === "open" ? openText : completedText).trim() === ""}
                        >
                          Add name
                        </button>
                      </div>
                      {draft.statuses[dimension].length === 0 ? (
                        <p class="capacities-settings__empty">
                          {dimension === "open"
                            ? "No open names are configured, so inheriting types admit nothing by status."
                            : "No completed names are configured."}
                        </p>
                      ) : (
                        <div class="capacities-exclusion-list">
                          {draft.statuses[dimension].map((name) => (
                            <div class="capacities-exclusion-row" key={name}>
                              <code>{name}</code>
                              <button
                                class="btn"
                                aria-label={`Remove ${dimension} status ${name}`}
                                onClick={() => removeVocabularyName(dimension, name)}
                              >
                                Remove
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                <div class="field capacities-settings__horizon">
                  <label for="capacities-deadline-horizon">Deadline coming up</label>
                  <div class="capacities-settings__horizon-control">
                    <input
                      id="capacities-deadline-horizon"
                      type="number"
                      min="0"
                      step="1"
                      value={horizonText}
                      onInput={(e) => {
                        const text = (e.currentTarget as HTMLInputElement).value;
                        setHorizonText(text);
                        const value = Number(text);
                        if (Number.isSafeInteger(value) && value >= 0) {
                          setDraft((current) =>
                            current
                              ? { ...current, native: { deadlineHorizonDays: value } }
                              : current,
                          );
                        }
                      }}
                    />
                    <span>calendar days</span>
                  </div>
                  <span class="capacities-settings__hint">
                    Applies to “Native task rules”: a task whose deadline is overdue or within this
                    many days comes in.
                  </span>
                  {!horizonTextValid && (
                    <span class="field-error" role="alert">Use a nonnegative whole number.</span>
                  )}
                </div>

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
                {unfinishedPropertyIds.length > 0 && (
                  <div class="capacities-settings__save-error" role="alert">
                    <span>
                      Pick a property for {unfinishedPropertyIds.join(", ")} — a checkbox or date
                      rule cannot be saved without one.
                    </span>
                  </div>
                )}
                {availableStructures.length === 0 ? (
                  <p class="capacities-settings__empty">
                    Your Capacities setup lists no types yet; saved settings are kept when you save.
                  </p>
                ) : (
                  <div class="capacities-object-list" role="group" aria-label="Capacities admission per item type">
                    {availableStructures.map((structureId) => {
                      const declared = declarationOf(draft, structureId);
                      const mode = admissionModeOf(draft, structureId);
                      return (
                        <CapacitiesAdmissionRow
                          key={structureId}
                          structureId={structureId}
                          title={structureTitleOf(structureId)}
                          mode={mode}
                          propertyId={declared.property ?? ""}
                          properties={discoveredPropertiesOf(structureId)}
                          discovering={discovering}
                          statusChoice={statusChoiceOf(declared)}
                          customStatuses={declared.statuses ?? []}
                          openStatuses={draft.statuses.open}
                          onModeChange={(next) => setMode(structureId, next)}
                          onPropertyChange={(propertyId) => setProperty(structureId, propertyId)}
                          onStatusChoiceChange={(choice) => setStatusChoice(structureId, choice)}
                          onCustomStatusesChange={(names) =>
                            setCustomStatusNames(structureId, names)
                          }
                          onLookUp={() => void lookUpProperties()}
                        />
                      );
                    })}
                  </div>
                )}
                {staleStructureIds.length > 0 && (
                  <div class="capacities-exclusion-list">
                    <h4>Types with a saved rule that are not set up to appear</h4>
                    <p class="capacities-settings__hint">
                      These {staleStructureIds.length} saved {staleStructureIds.length === 1 ? "id is" : "ids are"} not set up to appear in your Capacities setup. They are kept as they are when you save.
                    </p>
                    {staleStructureIds.map((structureId) => {
                      const declared = declarationOf(draft, structureId);
                      return (
                        <div class="capacities-exclusion-row" key={structureId}>
                          <code>{structureId}</code>
                          <code>{usesStatus(declared.mode) ? `${declared.mode} · status` : declared.mode}</code>
                          <button
                            class="btn"
                            aria-label={`Switch off the saved rule for ${structureId}`}
                            onClick={() => setMode(structureId, "off")}
                          >
                            Switch off
                          </button>
                        </div>
                      );
                    })}
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
              <button class="btn btn--primary" onClick={() => void save()} disabled={phase === "saving" || saveBlocked}>
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
