/* CapacitiesSourceEditor — the vault-local Capacities source mapping editor.

   The mapping record names which Capacities structures TDTB reads and which
   provider property plays each role on a structure. This surface adds no
   discovery: every value is declared by the operator, and a property value
   is an opaque provider id that is kept exactly as typed.

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
     by its own field. */

import { useEffect, useState } from "preact/hooks";
import { capacitiesSourceConflictOf } from "../adapters/api";
import type {
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

/** One property field. The input value is the stored value verbatim; an
    emptied field reports null and is the caller's to clear (or, for the
    non-nullable fields, to keep as an empty string, which the server
    rejects loudly rather than the client guessing a value). */
function PropertyField({
  label,
  ariaLabel,
  value,
  placeholder,
  onChange,
}: {
  label: string;
  ariaLabel: string;
  value: string | null;
  placeholder: string;
  onChange: (value: string | null) => void;
}) {
  return (
    <label class="capacities-source-field">
      <span>{label}</span>
      <input
        type="text"
        value={value ?? ""}
        placeholder={placeholder}
        aria-label={ariaLabel}
        onInput={(e) => {
          const text = e.currentTarget.value;
          onChange(text === "" ? null : text);
        }}
      />
    </label>
  );
}

function StructureRow({
  row,
  index,
  title,
  onEdit,
  onRemove,
}: {
  row: CapacitiesSourceStructure;
  index: number;
  title: string | null;
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
          onChange={(next) =>
            onEdit((current) => ({ ...current, titleProperty: next ?? "" }))
          }
        />
        <PropertyField
          label="Status property"
          ariaLabel={`Status property for ${rowRef}`}
          value={row.statusProperty}
          placeholder="unset"
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
        <PropertyField
          label="Date property"
          ariaLabel={`Date property for ${rowRef}`}
          value={row.dateProperty}
          placeholder="unset"
          onChange={(next) => onEdit((current) => ({ ...current, dateProperty: next }))}
        />
        <PropertyField
          label="Deadline property"
          ariaLabel={`Deadline property for ${rowRef}`}
          value={row.deadlineProperty}
          placeholder="unset"
          onChange={(next) =>
            onEdit((current) => ({ ...current, deadlineProperty: next }))
          }
        />
        <PropertyField
          label="Duration property"
          ariaLabel={`Duration property for ${rowRef}`}
          value={row.durationProperty}
          placeholder="unset"
          onChange={(next) =>
            onEdit((current) => ({ ...current, durationProperty: next }))
          }
        />
        <PropertyField
          label="Assignment property"
          ariaLabel={`Assignment property for ${rowRef}`}
          value={row.assignmentProperty}
          placeholder="unset"
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
        <PropertyField
          label="Completion property"
          ariaLabel={`Completion property for ${rowRef}`}
          value={row.completionProperty}
          placeholder="unset"
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
