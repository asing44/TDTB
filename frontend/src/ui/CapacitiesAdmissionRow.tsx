/* CapacitiesAdmissionRow — ONE admission rule per Capacities item type.

   Schema version 2 gives each type exactly one rule for getting into the plan,
   so the row is one five-option mode selector plus the two controls a mode may
   need:

     off         no declaration at all (the type admits nothing)
     active      status only — inherits or states its own open names
     assigned    an `assigned` checkbox property (boolean) must be true
     assignedOn  an `assignedOn` date property must be today or earlier
     native      the built-in rules: status-open, due, or deadline in window

   The property picker appears only for the two property modes and offers only
   properties of the matching kind (boolean for Assigned, date for Assigned On),
   fed by the same provider discovery the source mapping editor uses. The status
   control appears only for the two status modes and has three states that map
   one-to-one onto the wire's tri-state:

     Inherit the shared list  -> statuses: null
     Don't use status         -> statuses: []
     Custom names             -> statuses: ["In Progress", ...]

   `null` and `[]` mean opposite things to the evaluator (inherit vs. no status
   admission at all), so the row never collapses one into the other. */

import { useState } from "preact/hooks";
import type {
  CapacitiesCatalogProperty,
  CapacitiesSettingsDraft,
  CapacitiesTypeDeclaration,
  CapacitiesTypeMode,
} from "../model/types";
import "./capacities-admission.css";

export type CapacitiesAdmissionMode = CapacitiesTypeMode;

export const ADMISSION_MODES: ReadonlyArray<{
  value: CapacitiesAdmissionMode;
  label: string;
}> = [
  { value: "off", label: "Off" },
  { value: "active", label: "Active by status" },
  { value: "assigned", label: "Assigned checkbox" },
  { value: "assignedOn", label: "Assigned On date" },
  { value: "native", label: "Native task rules" },
];

/** Kinds discovery reports for a boolean "assigned" checkbox. */
const CHECKBOX_KINDS = new Set(["boolean", "checkbox"]);
/** Kinds discovery reports for a read-only "Assigned On" date. */
const DATE_KINDS = new Set(["date", "datetime"]);

/** Modes whose declaration is meaningless without a picked property. */
export function isPropertyMode(mode: CapacitiesAdmissionMode): boolean {
  return mode === "assigned" || mode === "assignedOn";
}

/** Modes whose decision consults the status condition at all. */
export function usesStatus(mode: CapacitiesAdmissionMode): boolean {
  return mode === "active" || mode === "native";
}

/** The properties the picker may offer for a mode, in discovery order. A
    non-property mode offers nothing: its decision reads no property. */
export function propertiesForMode(
  properties: CapacitiesCatalogProperty[] | null,
  mode: CapacitiesAdmissionMode,
): CapacitiesCatalogProperty[] {
  if (properties === null) return [];
  const kinds = mode === "assigned" ? CHECKBOX_KINDS : mode === "assignedOn" ? DATE_KINDS : null;
  if (kinds === null) return [];
  return properties.filter((property) => kinds.has((property.type ?? "").toLowerCase()));
}

/** The declaration the draft holds for one structure. An absent entry is the
    documented `off` default — never a missing editor state. */
export function declarationOf(
  draft: CapacitiesSettingsDraft,
  structureId: string,
): CapacitiesTypeDeclaration {
  return draft.types[structureId] ?? { mode: "off", property: null, statuses: null };
}

/** The mode the saved draft already describes for one structure. */
export function admissionModeOf(
  draft: CapacitiesSettingsDraft,
  structureId: string,
): CapacitiesAdmissionMode {
  return declarationOf(draft, structureId).mode;
}

/** One mode change. The property travels with the mode so switching between the
    two property modes keeps the operator's pick when it still applies, and a
    property is never sent for a mode that must not declare one. The statuses
    tri-state is untouched: switching a type's rule is not a statement about
    its status names. */
export function applyAdmissionMode(
  draft: CapacitiesSettingsDraft,
  structureId: string,
  mode: CapacitiesAdmissionMode,
  propertyId: string,
): CapacitiesSettingsDraft {
  const current = declarationOf(draft, structureId);
  const property = isPropertyMode(mode) && propertyId !== "" ? propertyId : null;
  return {
    ...draft,
    types: { ...draft.types, [structureId]: { mode, property, statuses: current.statuses } },
  };
}

/** The three states the status control offers. */
export type CapacitiesStatusChoice = "inherit" | "none" | "custom";

export function statusChoiceOf(declaration: CapacitiesTypeDeclaration): CapacitiesStatusChoice {
  if (declaration.statuses === null) return "inherit";
  if (declaration.statuses.length === 0) return "none";
  return "custom";
}

/** One status-control change, written as exactly the tri-state the wire needs:
    `null` (inherit the shared list), `[]` (status admission off for this type,
    so only its date/checkbox/native conditions decide), or the custom names. */
export function applyStatusChoice(
  draft: CapacitiesSettingsDraft,
  structureId: string,
  choice: CapacitiesStatusChoice,
  names: string[] = [],
): CapacitiesSettingsDraft {
  const current = declarationOf(draft, structureId);
  const statuses = choice === "inherit" ? null : choice === "none" ? [] : [...names];
  return {
    ...draft,
    types: { ...draft.types, [structureId]: { ...current, statuses } },
  };
}

/** Edit the custom name list of a declaration that already states its own
    names. Kept as a separate helper so adding a name never flips the state of a
    declaration that inherits or disables status admission. */
export function applyCustomStatusNames(
  draft: CapacitiesSettingsDraft,
  structureId: string,
  names: string[],
): CapacitiesSettingsDraft {
  const current = declarationOf(draft, structureId);
  return {
    ...draft,
    types: { ...draft.types, [structureId]: { ...current, statuses: [...names] } },
  };
}

/** Shortens an id only when there is no title to show: a raw 36-char provider
    UUID is unreadable in a row, while a short mapping id reads fine as is. */
export function displayIdOf(structureId: string): string {
  return structureId.length > 20 ? `${structureId.slice(0, 8)}…` : structureId;
}

export function CapacitiesAdmissionRow({
  structureId,
  title,
  mode,
  propertyId,
  properties,
  discovering,
  statusChoice,
  customStatuses,
  openStatuses,
  onModeChange,
  onPropertyChange,
  onStatusChoiceChange,
  onCustomStatusesChange,
  onLookUp,
}: {
  structureId: string;
  title: string | null;
  mode: CapacitiesAdmissionMode;
  /** The declaration's raw property id, or "" when none is picked yet. */
  propertyId: string;
  /** The discovered properties for this structure, or null when the space's
      schema has not been looked up yet. */
  properties: CapacitiesCatalogProperty[] | null;
  discovering: boolean;
  statusChoice: CapacitiesStatusChoice;
  customStatuses: string[];
  /** The shared open names an "inherit" declaration resolves to. */
  openStatuses: string[];
  onModeChange: (mode: CapacitiesAdmissionMode) => void;
  onPropertyChange: (propertyId: string) => void;
  onStatusChoiceChange: (choice: CapacitiesStatusChoice) => void;
  onCustomStatusesChange: (names: string[]) => void;
  onLookUp: () => void;
}) {
  const label = title ?? displayIdOf(structureId);
  const choices = propertiesForMode(properties, mode);
  const declaredMissing =
    propertyId !== "" && properties !== null && !choices.some((p) => p.propertyId === propertyId);
  const hasDeclaredOption = propertyId !== "" && declaredMissing;
  const needsProperty = isPropertyMode(mode) && propertyId === "";

  return (
    <div class="capacities-object-row capacities-admission" data-structure={structureId} data-mode={mode}>
      <div class="capacities-object-row__identity">
        {title !== null && <strong>{title}</strong>}
        <code>{displayIdOf(structureId)}</code>
      </div>
      <div class="capacities-admission__control">
        <label class="capacities-admission__mode">
          <span class="capacities-admission__label">Rule</span>
          <select
            aria-label={`Admission for ${label}`}
            value={mode}
            onChange={(e) =>
              onModeChange((e.currentTarget as HTMLSelectElement).value as CapacitiesAdmissionMode)
            }
          >
            {ADMISSION_MODES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        {/* One line so the two status modes are not read as the same rule. */}
        <span class="capacities-admission__help">
          Active uses the status only; Native also uses the due date and the deadline.
        </span>

        {isPropertyMode(mode) &&
          (properties === null ? (
            <div class="capacities-admission__pending">
              <span class="capacities-admission__label">
                {propertyId === "" ? "No property picked yet" : propertyId}
              </span>
              <button
                type="button"
                class="btn"
                aria-label={`Look up properties for ${label}`}
                disabled={discovering}
                onClick={onLookUp}
              >
                {discovering ? "Looking up…" : "Look up properties"}
              </button>
            </div>
          ) : (
            <label class="capacities-admission__mode">
              <span class="capacities-admission__label">
                {mode === "assigned" ? "Assigned checkbox property" : "Assigned On date property"}
              </span>
              <select
                aria-label={`Assignment property for ${label}`}
                class={declaredMissing ? "capacities-admission__pick--missing" : undefined}
                value={propertyId}
                onChange={(e) => onPropertyChange((e.currentTarget as HTMLSelectElement).value)}
              >
                <option value="">No property</option>
                {hasDeclaredOption && (
                  <option value={propertyId} class="capacities-admission__missing">
                    {`Missing in Capacities — pick another (${propertyId})`}
                  </option>
                )}
                {choices.map((property) => (
                  <option key={property.propertyId} value={property.propertyId}>
                    {`${property.title || property.propertyId} (${property.propertyId})`}
                  </option>
                ))}
              </select>
              {declaredMissing && (
                <span class="capacities-admission__warn" role="status">
                  Saved property {propertyId} is missing in Capacities. Pick another, or choose No
                  property to clear it.
                </span>
              )}
              {!declaredMissing && choices.length === 0 && (
                <span class="capacities-admission__warn" role="status">
                  Capacities reports no {mode === "assigned" ? "checkbox" : "date"} property for
                  this type.
                </span>
              )}
            </label>
          ))}

        {isPropertyMode(mode) && needsProperty && (
          <span class="capacities-admission__warn" role="status">
            Pick a property for this type, or choose another rule.
          </span>
        )}

        {usesStatus(mode) && (
          <CapacitiesStatusControl
            label={label}
            choice={statusChoice}
            customStatuses={customStatuses}
            openStatuses={openStatuses}
            onChoiceChange={onStatusChoiceChange}
            onNamesChange={onCustomStatusesChange}
          />
        )}
      </div>
    </div>
  );
}

/** The tri-state status control. It is rendered only for the two status modes,
    because the other modes never consult status and an editor there would
    promise something the evaluator ignores. */
function CapacitiesStatusControl({
  label,
  choice,
  customStatuses,
  openStatuses,
  onChoiceChange,
  onNamesChange,
}: {
  label: string;
  choice: CapacitiesStatusChoice;
  customStatuses: string[];
  openStatuses: string[];
  onChoiceChange: (choice: CapacitiesStatusChoice) => void;
  onNamesChange: (names: string[]) => void;
}) {
  const [draftName, setDraftName] = useState("");
  const addName = () => {
    const value = draftName.trim();
    setDraftName("");
    if (value === "" || customStatuses.includes(value)) return;
    onNamesChange([...customStatuses, value].sort());
  };
  return (
    <div class="capacities-admission__status">
      <label class="capacities-admission__mode">
        <span class="capacities-admission__label">Status names</span>
        <select
          aria-label={`Status names for ${label}`}
          value={choice}
          onChange={(e) =>
            onChoiceChange((e.currentTarget as HTMLSelectElement).value as CapacitiesStatusChoice)
          }
        >
          <option value="inherit">Inherit the shared list</option>
          <option value="none">Don't use status (date or checkbox only)</option>
          <option value="custom">Custom names</option>
        </select>
      </label>
      {choice === "inherit" && (
        <span class="capacities-admission__help">
          {openStatuses.length === 0
            ? "The shared open list is empty, so status admits nothing."
            : `Uses your shared open list: ${openStatuses.join(", ")}.`}
        </span>
      )}
      {choice === "none" && (
        <span class="capacities-admission__help">
          Status is ignored for this type; only its date or checkbox rule decides.
        </span>
      )}
      {choice === "custom" && (
        <div class="capacities-admission__names">
          {customStatuses.length === 0 ? (
            <span class="capacities-admission__warn" role="status">
              No names yet, so status admits nothing for this type.
            </span>
          ) : (
            <ul class="capacities-admission__names-list">
              {customStatuses.map((name) => (
                <li key={name}>
                  <code>{name}</code>
                  <button
                    type="button"
                    class="btn"
                    aria-label={`Remove status ${name} from ${label}`}
                    onClick={() => onNamesChange(customStatuses.filter((value) => value !== name))}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div class="capacities-admission__names-add">
            <input
              type="text"
              aria-label={`Add a status name for ${label}`}
              placeholder="e.g. In Progress"
              value={draftName}
              onInput={(e) => setDraftName((e.currentTarget as HTMLInputElement).value)}
            />
            <button type="button" class="btn" disabled={draftName.trim() === ""} onClick={addName}>
              Add name
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
