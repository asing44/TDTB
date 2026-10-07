/* CapacitiesSourceEditor — the vault-local Capacities source mapping editor.

   The mapping record names which Capacities structures TDTB reads and which
   provider property plays each role on a structure. Property values are
   opaque provider ids kept exactly as typed; discovery renders the space's
   real ids so the operator does not have to guess them.

   Contract this surface keeps:
   - An absent record ({source: null, persisted: false}) is an empty state,
     not an error; the operator can start a first mapping from it.
   - The save route is a FULL REPLACEMENT, so the draft always carries every
     field of every structure plus the loaded expectedRevision.
   - openStatusValues and assignmentValues are order-preserving lists: they
     are edited one entry per line and are never sorted or normalised.
   - Removing a structure is an explicit per-row action. A re-render, a
     reload, or a failed save never drops a draft row.
   - A real 409 conflict (capacitiesSourceConflictOf) keeps the draft and
     reports both revisions; "reload and review" re-reads the record on
     request, and the save is never retried or re-based automatically.
   - Display titles are advisory metadata from the settings read; identity
     and saving always key on the structure id, and the id is rendered only
     by its own field.
   - Discovery is a read-only provider query: it renders what Capacities
     really has and never writes. It never touches the draft, and the only
     bridge from the catalog into the draft is the explicit per-structure
     "Add to mapping" action, which appends the existing empty row shape
     with the structure id set. No role is ever inferred from a property's
     name, title, or type. A structure whose reported title equals its own
     structure id is a real no-name case, not an error.
   - A failed discovery keeps the draft byte-identical, performs no save,
     and classifies itself from the ApiError status: 503 credentials (an
     operator must restore them), 429 rate limit (wait and retry), anything
     else generic.
   - The catalog is also offered as field choices, and only as suggestions:
     a property field suggests its row's own structure's property ids
     through a datalist (id as the value, title and type as the label), and
     each value list suggests its property's label options as one-click
     append chips (id as the value, title as the label). Nothing is ever
     preselected, populated, or inferred from a name, title, or type; a
     value the catalog does not list can still be typed and saved; and with
     no catalog every field is exactly the same free-text field it was.
     Property values stay opaque: nothing is trimmed, normalised, sorted,
     or case-folded. */

import { useEffect, useId, useState } from "preact/hooks";
import { ApiError, capacitiesSourceConflictOf } from "../adapters/api";
import type {
  CapacitiesCatalog,
  CapacitiesCatalogStructure,
  CapacitiesSource,
  CapacitiesSourceDraft,
  CapacitiesSourceRead,
  CapacitiesSourceStructure,
} from "../model/types";
import { useApp } from "./context";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** A brand-new row: every role unconfigured except the two fields that are
    non-nullable in the record. Nothing is inferred from a title or copied
    from a similarly named field — the operator declares every role. */
function emptyStructure(): CapacitiesSourceStructure {
  return {
    structureId: "",
    titleProperty: "",
    statusProperty: null,
    openStatusValues: [],
    dateProperty: null,
    deadlineProperty: null,
    durationProperty: null,
    assignmentProperty: null,
    assignmentValues: [],
    completionProperty: null,
    completionValue: null,
  };
}

/** Full-replacement draft for a loaded record, or the empty draft that
    starts a first mapping. Every field of every structure is copied,
    including both order-preserving lists (copied, never sorted), so an edit
    can never mutate the record the component still holds. */
function draftOf(source: CapacitiesSource | null): CapacitiesSourceDraft {
  return {
    expectedRevision: source?.revision ?? 0,
    spaceId: source?.spaceId ?? "",
    structures: (source?.structures ?? []).map((row) => ({
      ...row,
      openStatusValues: [...row.openStatusValues],
      assignmentValues: [...row.assignmentValues],
    })),
  };
}

/** The value lists are edited one entry per line: "" is the empty list and
    the stored order is kept exactly — nothing is trimmed, sorted, or
    case-folded. (Provider validation rejects leading/trailing whitespace
    and every whitespace character other than an internal space, so a
    well-formed list never contains a newline.) */
function listTextOf(values: string[]): string {
  return values.join("\n");
}

function listOf(text: string): string[] {
  return text === "" ? [] : text.split("\n");
}

interface Conflict {
  expectedRevision: number;
  currentRevision: number;
}

/** What an explicit reload re-read: the revision the next save is checked
    against, and the server's own structure ids so the operator can review
    them beside the retained draft. */
interface Review {
  revision: number;
  structureIds: string[] | null;
}

function reviewOf(loaded: CapacitiesSourceRead): Review {
  return {
    revision: loaded.source?.revision ?? 0,
    structureIds:
      loaded.source === null
        ? null
        : loaded.source.structures.map((row) => row.structureId),
  };
}

function reviewText(review: Review): string {
  if (review.structureIds === null) {
    return "Re-read the record: no mapping is stored on the server now. Your draft is unchanged; saving now creates one.";
  }
  const count = review.structureIds.length;
  const ids = review.structureIds.join(", ");
  return `Re-read the record: the server now holds revision ${review.revision} with ${count} ${
    count === 1 ? "structure" : "structures"
  } (${ids}). Your draft is unchanged; saving now replaces the stored record.`;
}

/** How a failed discovery is actionable, narrowed from the ApiError status
    (house style: `e instanceof ApiError && e.status === N`). 503 needs an
    operator because no retry can restore a missing credential; 429 clears
    on its own; anything else is a generic failure. */
type DiscoveryFailureKind = "credentials" | "rate_limited" | "failed";

interface DiscoveryFailure {
  kind: DiscoveryFailureKind;
  message: string;
}

const DISCOVERY_FAILURE_LEAD: Record<DiscoveryFailureKind, string> = {
  credentials:
    "Capacities credentials are unavailable; retrying will not help until an operator restores them.",
  rate_limited: "Capacities rate-limited the discovery; wait a moment, then try again.",
  failed: "Discovery failed.",
};

function discoveryFailureOf(error: unknown): DiscoveryFailure {
  if (error instanceof ApiError && error.status === 503) {
    return { kind: "credentials", message: messageOf(error) };
  }
  if (error instanceof ApiError && error.status === 429) {
    return { kind: "rate_limited", message: messageOf(error) };
  }
  return { kind: "failed", message: messageOf(error) };
}

/** One suggested field value: `value` is exactly what a mapping stores and
    `label` is display metadata. Choices only ever suggest. */
interface FieldChoice {
  value: string;
  label: string;
}

/** One property field. The input value is the stored value verbatim; an
    emptied field reports null and is the caller's to clear (or, for the
    non-nullable fields, to keep as an empty string, which the server
    rejects loudly rather than the client guessing a value). An optional
    `choices` list renders as a datalist: it suggests, never constrains, so
    any value — listed or not — can still be typed and saved. */
function PropertyField({
  label,
  ariaLabel,
  value,
  placeholder,
  choices,
  onChange,
}: {
  label: string;
  ariaLabel: string;
  value: string | null;
  placeholder: string;
  choices?: FieldChoice[];
  onChange: (value: string | null) => void;
}) {
  const listId = useId();
  const offered = choices !== undefined && choices.length > 0 ? choices : null;
  return (
    <label class="capacities-source-field">
      <span>{label}</span>
      <input
        type="text"
        value={value ?? ""}
        placeholder={placeholder}
        aria-label={ariaLabel}
        list={offered === null ? undefined : listId}
        onInput={(e) => {
          const text = e.currentTarget.value;
          onChange(text === "" ? null : text);
        }}
      />
      {offered !== null && (
        <datalist id={listId}>
          {offered.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </datalist>
      )}
    </label>
  );
}

/** The row's own structure from the discovery catalog, matched on the raw
    structure id. No catalog, a blank id, or no match means no choices. */
function discoveredStructureOf(
  catalog: CapacitiesCatalog | null,
  structureId: string,
): CapacitiesCatalogStructure | null {
  if (catalog === null || structureId === "") return null;
  return (
    catalog.structures.find(
      (structure) => structure.structureId === structureId,
    ) ?? null
  );
}

/** Exact-match append for a value list: an id already present stays put, a
    new one lands at the end. Order is preserved and nothing is normalised. */
function withChoice(values: string[], value: string): string[] {
  return values.includes(value) ? values : [...values, value];
}

/** A value list's label options as one-click append chips. Each chip carries
    the option's id as its value and its title as its label; a click appends
    the exact id as a new line only when it is not already present. Nothing
    is preselected and nothing is inferred from a property name or type. */
function ValueChoices({
  ariaLabel,
  choices,
  onAppend,
}: {
  ariaLabel: string;
  choices: FieldChoice[];
  onAppend: (value: string) => void;
}) {
  if (choices.length === 0) return null;
  return (
    <div
      class="capacities-source-choices capacities-source-field--wide"
      role="group"
      aria-label={ariaLabel}
    >
      <small>Click a discovered value to append it.</small>
      {choices.map((choice) => (
        <button
          class="chip chip--btn"
          key={choice.value}
          value={choice.value}
          onClick={() => onAppend(choice.value)}
        >
          {choice.label}
        </button>
      ))}
    </div>
  );
}

function StructureRow({
  row,
  index,
  title,
  discovered,
  onEdit,
  onRemove,
}: {
  row: CapacitiesSourceStructure;
  index: number;
  title: string | null;
  discovered: CapacitiesCatalogStructure | null;
  onEdit: (
    update: (row: CapacitiesSourceStructure) => CapacitiesSourceStructure,
  ) => void;
  onRemove: () => void;
}) {
  // The row is addressed by its structure id when it has one and by its
  // position otherwise. The id itself is rendered only by its own field —
  // an observed title leads, and a title that merely repeats the id is
  // dropped by the caller, so the id never renders twice.
  const rowRef = row.structureId === "" ? `structure ${index + 1}` : row.structureId;
  // Every role field suggests that row's own structure's properties, and
  // each value list suggests the label options of the property it names.
  // Both are offers only: nothing here reads a catalog value into the row.
  const propertyChoices: FieldChoice[] = (discovered?.properties ?? []).map(
    (property) => ({
      value: property.propertyId,
      label: `${property.title} · ${property.type}`,
    }),
  );
  const labelChoicesOf = (propertyId: string | null): FieldChoice[] => {
    if (propertyId === null) return [];
    const property = discovered?.properties.find(
      (candidate) => candidate.propertyId === propertyId,
    );
    return (property?.labelOptions ?? []).map((option) => ({
      value: option.id,
      label: option.title,
    }));
  };
  return (
    <article class="capacities-source-row">
      <div class="capacities-source-row__head">
        <div class="capacities-source-row__identity">
          {title !== null && <strong>{title}</strong>}
          <small>Structure {index + 1}</small>
        </div>
        <button
          class="btn"
          onClick={onRemove}
          aria-label={`Remove structure ${rowRef}`}
        >
          Remove
        </button>
      </div>
      <div class="capacities-source-row__fields">
        <PropertyField
          label="Structure ID"
          ariaLabel={`Structure ID for ${rowRef}`}
          value={row.structureId}
          placeholder="structure id"
          onChange={(next) =>
            onEdit((current) => ({ ...current, structureId: next ?? "" }))
          }
        />
        <PropertyField
          label="Title property"
          ariaLabel={`Title property for ${rowRef}`}
          value={row.titleProperty}
          placeholder="property id"
          choices={propertyChoices}
          onChange={(next) =>
            onEdit((current) => ({ ...current, titleProperty: next ?? "" }))
          }
        />
        <PropertyField
          label="Status property"
          ariaLabel={`Status property for ${rowRef}`}
          value={row.statusProperty}
          placeholder="unset"
          choices={propertyChoices}
          onChange={(next) =>
            onEdit((current) => ({ ...current, statusProperty: next }))
          }
        />
        <label class="capacities-source-field capacities-source-field--wide">
          <span>Open status values</span>
          <textarea
            rows={3}
            value={listTextOf(row.openStatusValues)}
            placeholder="one value per line"
            aria-label={`Open status values for ${rowRef}`}
            onInput={(e) => {
              const text = e.currentTarget.value;
              onEdit((current) => ({ ...current, openStatusValues: listOf(text) }));
            }}
          />
          <small>One value per line; stored in exactly this order.</small>
        </label>
        <ValueChoices
          ariaLabel={`Discovered open status values for ${rowRef}`}
          choices={labelChoicesOf(row.statusProperty)}
          onAppend={(value) =>
            onEdit((current) => ({
              ...current,
              openStatusValues: withChoice(current.openStatusValues, value),
            }))
          }
        />
        <PropertyField
          label="Date property"
          ariaLabel={`Date property for ${rowRef}`}
          value={row.dateProperty}
          placeholder="unset"
          choices={propertyChoices}
          onChange={(next) => onEdit((current) => ({ ...current, dateProperty: next }))}
        />
        <PropertyField
          label="Deadline property"
          ariaLabel={`Deadline property for ${rowRef}`}
          value={row.deadlineProperty}
          placeholder="unset"
          choices={propertyChoices}
          onChange={(next) =>
            onEdit((current) => ({ ...current, deadlineProperty: next }))
          }
        />
        <PropertyField
          label="Duration property"
          ariaLabel={`Duration property for ${rowRef}`}
          value={row.durationProperty}
          placeholder="unset"
          choices={propertyChoices}
          onChange={(next) =>
            onEdit((current) => ({ ...current, durationProperty: next }))
          }
        />
        <PropertyField
          label="Assignment property"
          ariaLabel={`Assignment property for ${rowRef}`}
          value={row.assignmentProperty}
          placeholder="unset"
          choices={propertyChoices}
          onChange={(next) =>
            onEdit((current) => ({ ...current, assignmentProperty: next }))
          }
        />
        <label class="capacities-source-field capacities-source-field--wide">
          <span>Assignment values</span>
          <textarea
            rows={3}
            value={listTextOf(row.assignmentValues)}
            placeholder="one value per line"
            aria-label={`Assignment values for ${rowRef}`}
            onInput={(e) => {
              const text = e.currentTarget.value;
              onEdit((current) => ({ ...current, assignmentValues: listOf(text) }));
            }}
          />
          <small>One value per line; stored in exactly this order.</small>
        </label>
        <ValueChoices
          ariaLabel={`Discovered assignment values for ${rowRef}`}
          choices={labelChoicesOf(row.assignmentProperty)}
          onAppend={(value) =>
            onEdit((current) => ({
              ...current,
              assignmentValues: withChoice(current.assignmentValues, value),
            }))
          }
        />
        <PropertyField
          label="Completion property"
          ariaLabel={`Completion property for ${rowRef}`}
          value={row.completionProperty}
          placeholder="unset"
          choices={propertyChoices}
          onChange={(next) =>
            onEdit((current) => ({ ...current, completionProperty: next }))
          }
        />
        <PropertyField
          label="Completion value"
          ariaLabel={`Completion value for ${rowRef}`}
          value={row.completionValue}
          placeholder="unset"
          onChange={(next) =>
            onEdit((current) => ({ ...current, completionValue: next }))
          }
        />
      </div>
    </article>
  );
}

export function CapacitiesSourceEditor({
  structureTitleOf,
}: {
  structureTitleOf: (structureId: string) => string | null;
}) {
  const { controller } = useApp();
  const [draft, setDraft] = useState<CapacitiesSourceDraft | null>(null);
  const [record, setRecord] = useState<CapacitiesSource | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [saving, setSaving] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [catalog, setCatalog] = useState<CapacitiesCatalog | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [discoveryFailure, setDiscoveryFailure] = useState<DiscoveryFailure | null>(
    null,
  );

  const loadSource = async () => {
    setLoading(true);
    setLoadError(null);
    setConflict(null);
    setReview(null);
    try {
      const loaded = await controller.loadCapacitiesSource();
      setRecord(loaded.source);
      setDraft(draftOf(loaded.source));
    } catch (e) {
      setLoadError(messageOf(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadSource();
  }, [controller]);

  const patchDraft = (patch: Partial<CapacitiesSourceDraft>) => {
    setDraft((current) => (current === null ? null : { ...current, ...patch }));
  };

  const editStructure = (
    index: number,
    update: (row: CapacitiesSourceStructure) => CapacitiesSourceStructure,
  ) => {
    setDraft((current) => {
      if (current === null) return null;
      const structures = current.structures.map((row, i) =>
        i === index ? update(row) : row,
      );
      return { ...current, structures };
    });
  };

  const addStructure = () => {
    setDraft((current) =>
      current === null
        ? null
        : { ...current, structures: [...current.structures, emptyStructure()] },
    );
  };

  // Discovery is a read-only provider query: it may replace the rendered
  // catalog or report a failure, but it NEVER saves and NEVER touches the
  // draft. The catalog it renders is advisory reference material, not a
  // mapping.
  const discover = async () => {
    if (draft === null || discovering) return;
    setDiscovering(true);
    setDiscoveryFailure(null);
    try {
      setCatalog(await controller.discoverCapacitiesSource(draft.spaceId));
    } catch (e) {
      setDiscoveryFailure(discoveryFailureOf(e));
    } finally {
      setDiscovering(false);
    }
  };

  // The ONLY path that copies a discovered structure into the draft, and it
  // copies nothing but the identity: every role stays exactly as
  // emptyStructure() left it. A property named "Title" or a type of "label"
  // never implies a role.
  const addDiscoveredStructure = (structureId: string) => {
    setDraft((current) =>
      current === null
        ? null
        : {
            ...current,
            structures: [
              ...current.structures,
              { ...emptyStructure(), structureId },
            ],
          },
    );
  };

  // The ONLY path that drops a draft row: a deliberate click on that row's
  // own Remove button. No reload, save, or re-render removes a structure.
  const removeStructure = (index: number) => {
    setDraft((current) => {
      if (current === null) return null;
      return {
        ...current,
        structures: current.structures.filter((_, i) => i !== index),
      };
    });
  };

  const save = async () => {
    if (draft === null || saving) return;
    setSaving(true);
    setActionError(null);
    setConflict(null);
    setReview(null);
    try {
      const saved = await controller.saveCapacitiesSource(draft);
      setRecord(saved.source);
      setDraft(draftOf(saved.source));
    } catch (e) {
      const stale = capacitiesSourceConflictOf(e);
      if (stale !== null) {
        // A real conflict: keep the draft, report both revisions, and wait
        // for the operator. Never retry, never re-base, never discard.
        setConflict(stale);
      } else {
        setActionError(messageOf(e));
      }
    } finally {
      setSaving(false);
    }
  };

  const reloadForReview = async () => {
    if (reloading) return;
    setReloading(true);
    setActionError(null);
    try {
      const loaded = await controller.loadCapacitiesSource();
      setRecord(loaded.source);
      setReview(reviewOf(loaded));
      setConflict(null);
      // The next save is checked against the freshly read revision. This is
      // the only place the base revision moves, and only because the
      // operator asked for the reload; the draft's own contents are never
      // rewritten, reordered, or dropped.
      patchDraft({ expectedRevision: loaded.source?.revision ?? 0 });
    } catch (e) {
      setActionError(messageOf(e));
    } finally {
      setReloading(false);
    }
  };

  return (
    <section
      class="setup-section capacities-settings__section"
      aria-labelledby="capacities-sec-source"
    >
      <div class="setup-section__head">
        <h3 id="capacities-sec-source">Capacities source mapping</h3>
        <span class="capacities-settings__revision">
          {record !== null ? `Saved revision ${record.revision}` : "No mapping saved yet"}
        </span>
      </div>
      <div class="setup-section__body">
        <p class="capacities-settings__hint">
          Names which Capacities structures TDTB reads and which provider property
          plays each role. Saving replaces the whole record: every field of every
          structure is sent. Property values are raw provider ids and are stored
          exactly as typed.
        </p>

        {loading && (
          <p class="capacities-settings__state" role="status">
            Loading source mapping…
          </p>
        )}

        {!loading && draft === null && (
          <section class="capacities-settings__error" role="alert">
            <strong>Source mapping could not be loaded</strong>
            <span>{loadError}</span>
            <button class="btn" onClick={() => void loadSource()}>
              Reload mapping
            </button>
          </section>
        )}

        {draft !== null && (
          <>
            {record === null && (
              <p class="capacities-settings__empty">
                No Capacities source mapping has been saved for this vault yet.
                Name the Capacities space and add at least one structure to
                start one.
              </p>
            )}

            <div class="field capacities-source-space">
              <label for="capacities-source-space">Capacities space id</label>
              <input
                id="capacities-source-space"
                type="text"
                value={draft.spaceId}
                placeholder="space id"
                onInput={(e) => patchDraft({ spaceId: e.currentTarget.value })}
              />
            </div>

            <div class="capacities-source-discover">
              <button
                class="btn"
                onClick={() => void discover()}
                disabled={discovering || draft.spaceId === ""}
              >
                {discovering ? "Discovering…" : "Discover from Capacities"}
              </button>
              <small>
                Reads the space's real structures and properties so their ids can
                be copied instead of guessed. Discovery never saves and never edits
                the draft.
              </small>
            </div>

            {discoveryFailure !== null && (
              <div class="capacities-source__discovery-error" role="alert">
                <strong>
                  {DISCOVERY_FAILURE_LEAD[discoveryFailure.kind]}
                </strong>
                <span>{discoveryFailure.message}</span>
              </div>
            )}

            {catalog !== null && (
              <section
                class="capacities-source-catalog"
                aria-label="Discovered Capacities structures"
              >
                <div class="capacities-source-catalog__head">
                  <h4>Discovered structures</h4>
                  <span>{`Space ${catalog.spaceId}`}</span>
                </div>
                {catalog.warnings.map((warning) => (
                  <p
                    class="capacities-source-catalog__warning"
                    role="status"
                    key={warning}
                  >
                    {warning}
                  </p>
                ))}
                {catalog.structures.length === 0 ? (
                  <p class="capacities-source-catalog__empty">
                    Capacities reported no structures in this space.
                  </p>
                ) : (
                  catalog.structures.map((structure) => (
                    <article
                      class="capacities-source-catalog__structure"
                      key={structure.structureId}
                    >
                      <div class="capacities-source-catalog__structure-head">
                        <div class="capacities-source-catalog__identity">
                          <strong>{structure.title}</strong>
                          <code>{structure.structureId}</code>
                        </div>
                        <button
                          class="btn"
                          onClick={() =>
                            addDiscoveredStructure(structure.structureId)
                          }
                          aria-label={`Add ${structure.structureId} to mapping`}
                        >
                          Add to mapping
                        </button>
                      </div>
                      {structure.properties.length === 0 ? (
                        <p class="capacities-source-catalog__empty">
                          No properties were discovered for this structure.
                        </p>
                      ) : (
                        <ul class="capacities-source-catalog__properties">
                          {structure.properties.map((property) => (
                            <li key={property.propertyId}>
                              <div class="capacities-source-catalog__property">
                                <code>{property.propertyId}</code>
                                <span>{property.title}</span>
                                <small>
                                  {property.writable
                                    ? `${property.type} · writable`
                                    : `${property.type} · read-only`}
                                </small>
                              </div>
                              {property.labelOptions.length > 0 && (
                                <ul class="capacities-source-catalog__labels">
                                  {property.labelOptions.map((option) => (
                                    <li key={option.id}>
                                      <code>{option.id}</code>
                                      <span>{option.title}</span>
                                    </li>
                                  ))}
                                </ul>
                              )}
                            </li>
                          ))}
                        </ul>
                      )}
                    </article>
                  ))
                )}
              </section>
            )}

            {draft.structures.length === 0 ? (
              <p class="capacities-settings__empty">
                No structures are mapped yet. Add a structure row, then declare
                its properties.
              </p>
            ) : (
              <div class="capacities-source-list">
                {draft.structures.map((row, index) => (
                  <StructureRow
                    key={index}
                    row={row}
                    index={index}
                    title={structureTitleOf(row.structureId)}
                    discovered={discoveredStructureOf(catalog, row.structureId)}
                    onEdit={(update) => editStructure(index, update)}
                    onRemove={() => removeStructure(index)}
                  />
                ))}
              </div>
            )}

            <div class="capacities-source-add">
              <button class="btn" onClick={addStructure}>
                Add structure
              </button>
            </div>

            {conflict !== null && (
              <div class="capacities-source__conflict" role="alert">
                <span>
                  {`This mapping changed while you were editing: you loaded revision ${conflict.expectedRevision}, and the server is now at revision ${conflict.currentRevision}. Your draft is kept and was not re-sent.`}
                </span>
                <button
                  class="btn"
                  onClick={() => void reloadForReview()}
                  disabled={reloading}
                >
                  {reloading ? "Reloading…" : "Reload and review"}
                </button>
              </div>
            )}

            {review !== null && (
              <p class="capacities-source__review" role="status">
                {reviewText(review)}
              </p>
            )}

            {actionError !== null && (
              <div class="capacities-settings__save-error" role="alert">
                <span>{actionError}</span>
              </div>
            )}

            <div class="editor__actions setup__actions">
              <button
                class="btn btn--primary"
                onClick={() => void save()}
                disabled={
                  saving || draft.spaceId === "" || draft.structures.length === 0
                }
              >
                {saving ? "Saving…" : "Save mapping"}
              </button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
