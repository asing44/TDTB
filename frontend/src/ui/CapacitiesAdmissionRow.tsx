/* CapacitiesAdmissionRow — ONE admission control per Capacities item type.

   The panel used to ask for two opaque property GUIDs in one section and a
   separate "bring in Active items" switch in another; a declaration saved
   with that switch off silently did nothing. This row replaces all of it with
   one select whose four modes say exactly which draft keys it writes:

     off         no activeStructures entry, no declaration
     status      activeStructures entry only
     assigned    activeStructures entry + assignedStructures[structure]
     assignedOn  activeStructures entry + assignedOnStructures[structure]

   Choosing either Assigned mode shows a picker limited to properties of the
   matching kind. The picker is fed by the same provider discovery the source
   mapping editor uses; when no schema has been looked up yet the row shows
   the declared id as plain text with a "Look up properties" button rather
   than an empty picker. */

import type { CapacitiesCatalogProperty } from "../model/types";
import type { CapacitiesSettingsDraft } from "../model/types";
import "./capacities-admission.css";

export type CapacitiesAdmissionMode = "off" | "status" | "assigned" | "assignedOn";

export const ADMISSION_MODES: ReadonlyArray<{
  value: CapacitiesAdmissionMode;
  label: string;
}> = [
  { value: "off", label: "Off" },
  { value: "status", label: "Status only" },
  { value: "assigned", label: "Requires Assigned checkbox" },
  { value: "assignedOn", label: "Requires Assigned date" },
];

/** Kinds discovery reports for a boolean "assigned" checkbox. */
const CHECKBOX_KINDS = new Set(["boolean", "checkbox"]);
/** Kinds discovery reports for a read-only "Assigned to me" date. */
const DATE_KINDS = new Set(["date", "datetime"]);

export function isRequiresMode(mode: CapacitiesAdmissionMode): boolean {
  return mode === "assigned" || mode === "assignedOn";
}

/** The properties the picker may offer for a mode, in discovery order. */
export function propertiesForMode(
  properties: CapacitiesCatalogProperty[] | null,
  mode: CapacitiesAdmissionMode,
): CapacitiesCatalogProperty[] {
  if (properties === null) return [];
  const kinds = mode === "assigned" ? CHECKBOX_KINDS : mode === "assignedOn" ? DATE_KINDS : null;
  if (kinds === null) return [];
  return properties.filter((property) => kinds.has((property.type ?? "").toLowerCase()));
}

/** The mode the saved draft already describes for one structure. A
    declaration wins over the plain inclusion flag: the flag alone cannot say
    which property the operator meant. */
export function admissionModeOf(
  draft: CapacitiesSettingsDraft,
  structureId: string,
): CapacitiesAdmissionMode {
  if (draft.assignedOnStructures[structureId]) return "assignedOn";
  if (draft.assignedStructures[structureId]) return "assigned";
  return draft.activeStructures.includes(structureId) ? "status" : "off";
}

/** One admission change, written as exactly the draft keys the mode owns. Any
    Requires mode also switches the structure on, which is the whole point of
    collapsing the old switch and the declarations into one control. */
export function applyAdmissionMode(
  draft: CapacitiesSettingsDraft,
  structureId: string,
  mode: CapacitiesAdmissionMode,
  propertyId: string,
): CapacitiesSettingsDraft {
  const activeStructures =
    mode === "off"
      ? draft.activeStructures.filter((value) => value !== structureId)
      : draft.activeStructures.includes(structureId)
        ? [...draft.activeStructures]
        : [...draft.activeStructures, structureId].sort();
  const assignedStructures = { ...draft.assignedStructures };
  const assignedOnStructures = { ...draft.assignedOnStructures };
  if (mode === "assigned") {
    delete assignedOnStructures[structureId];
    if (propertyId === "") delete assignedStructures[structureId];
    else assignedStructures[structureId] = propertyId;
  } else if (mode === "assignedOn") {
    delete assignedStructures[structureId];
    if (propertyId === "") delete assignedOnStructures[structureId];
    else assignedOnStructures[structureId] = propertyId;
  } else {
    delete assignedStructures[structureId];
    delete assignedOnStructures[structureId];
  }
  return { ...draft, activeStructures, assignedStructures, assignedOnStructures };
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
  declaredPropertyId,
  properties,
  discovering,
  declaredButOff,
  onModeChange,
  onPropertyChange,
  onLookUp,
}: {
  structureId: string;
  title: string | null;
  mode: CapacitiesAdmissionMode;
  declaredPropertyId: string;
  /** The discovered properties for this structure, or null when the space's
      schema has not been looked up yet. */
  properties: CapacitiesCatalogProperty[] | null;
  discovering: boolean;
  /** A saved declaration exists while the structure is not switched on. */
  declaredButOff: boolean;
  onModeChange: (mode: CapacitiesAdmissionMode) => void;
  onPropertyChange: (propertyId: string) => void;
  onLookUp: () => void;
}) {
  const label = title ?? displayIdOf(structureId);
  const choices = propertiesForMode(properties, mode);
  const declaredMissing =
    declaredPropertyId !== "" &&
    properties !== null &&
    !choices.some((property) => property.propertyId === declaredPropertyId);
  const hasDeclaredOption = declaredPropertyId !== "" && declaredMissing;

  return (
    <div class="capacities-object-row capacities-admission" data-structure={structureId}>
      <div class="capacities-object-row__identity">
        {title !== null && <strong>{title}</strong>}
        <code>{displayIdOf(structureId)}</code>
      </div>
      <div class="capacities-admission__control">
        <label class="capacities-admission__mode">
          <span class="capacities-admission__label">Admission</span>
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

        {isRequiresMode(mode) &&
          (properties === null ? (
            <div class="capacities-admission__pending">
              <span class="capacities-admission__label">
                {declaredPropertyId === "" ? "No property picked yet" : declaredPropertyId}
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
                {mode === "assigned" ? "Assigned checkbox property" : "Assigned date property"}
              </span>
              <select
                aria-label={`Assignment property for ${label}`}
                class={declaredMissing ? "capacities-admission__pick--missing" : undefined}
                value={declaredPropertyId}
                onChange={(e) =>
                  onPropertyChange((e.currentTarget as HTMLSelectElement).value)
                }
              >
                <option value="">No property</option>
                {hasDeclaredOption && (
                  <option value={declaredPropertyId} class="capacities-admission__missing">
                    {`Missing in Capacities — pick another (${declaredPropertyId})`}
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
                  Saved property {declaredPropertyId} is missing in Capacities. Pick another, or
                  choose No property to clear it.
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

        {declaredButOff && (
          <span class="capacities-admission__warn" role="status">
            This type has a saved declaration but is not switched on. Pick the same Requires mode
            again to turn it on.
          </span>
        )}
        <span class="capacities-admission__help">
          Switching a type to a Requires mode also turns it on.
        </span>
      </div>
    </div>
  );
}
