/* TaskRow — task-actions F4a: the collapsed task row from the approved
   prototype (design/task-actions r2). One row = an independent selection
   checkbox, the name with its source/type chips and a short state, the
   duration in mono, the Assigned switch, and three labelled buttons: Complete,
   Skip today (Todoist rows only), and Expand/Collapse. Selection never depends on expansion; one row is expanded
   at a time (store ui.expandedId). The expanded region mounts RowExpanded (F4b). */

import { useApp } from "./context";
import { RowExpanded } from "./RowExpanded";
import {
  draftIsStaged,
  effectiveBlocks,
  isRowSelected,
  queueState,
  taskActionDisabledReason,
  type AppState,
} from "../store/store";
import type { HierarchyRow } from "../model/allocatorView";
import { blocksLabel, display12h } from "../model/time";
import { sourceContext } from "../model/sourceContext";
import { mintOverlapNotes, overlapNotes } from "../model/findings";
import { dueLabel, normalizeUrgency } from "../model/urgency";
import type { AssignedItem } from "../model/types";

/** What Complete does on a Capacities row, in the words the control carries.
    Capacities write-back is out of scope, so the row is skipped for today. */
export const CAPACITIES_COMPLETE_NOTE =
  "Done for today — skips this Capacities item for today; Capacities is not updated";

/** U11 labeling pass → Planning 2a: the row's provenance as the mockup's small
    source swatch plus its human source name ("Todoist", "Capacities"). An
    unrecognized source reads as a neutral OTHER. The colour tints live in
    app.css; the key selects the swatch hue. */
export function sourceChip(item: AssignedItem): { key: string; label: string } {
  const source = String(item.source);
  if (source === "todoist") return { key: "todoist", label: "Todoist" };
  if (source === "capacities") return { key: "capacities", label: "Capacities" };
  if (source === "calendar") return { key: "calendar", label: "Calendar" };
  return { key: "other", label: "Other" };
}

/** The row's KIND as a human label. Capacities rows resolve their structure id
    through the app's machine-local structure-titles cache; a structure the
    cache has not titled reads "Capacities item" rather than a raw UUID.
    Todoist rows carry no kind chip — the TODOIST source chip already says it. */
export function kindLabel(
  item: AssignedItem,
  titles: Record<string, string> | null | undefined,
): string | null {
  if (item.source === "todoist") return null;
  if (item.source === "capacities") {
    const id = (
      item.capacitiesStructureId ??
      item.types.find((t) => t && t !== "todoist") ??
      ""
    ).trim();
    const title = id ? (titles?.[id] ?? "").trim() : "";
    return title.length > 0 ? title : "Capacities item";
  }
  return null;
}

/** Stable id for aria-controls/aria-labelledby pairs — item ids are human
    names with spaces and punctuation, and ids must not contain them. */
export function rowId(item: { id: string }, suffix: string): string {
  return `${suffix}-${item.id.replace(/[^A-Za-z0-9_-]+/g, "-")}`;
}

/** U11: urgency tier → the mockup's priority chip (p1 highest). */
const PRIORITY_LABEL: Record<string, string> = {
  crit: "p1",
  high: "p2",
  med: "p3",
  low: "p4",
};

const NATIVE_TIME = /^(?:[01]\d|2[0-3]):[0-5]\d$/;

/** The row's short scheduling state, in the prototype's words: Staged (an
    unsaved draft), All-day (a date-only due), Source time (a native Todoist
    wall time), Pinned (an exact start chosen here), else Automatic start. */
function shortState(s: AppState, item: AssignedItem): string {
  if (draftIsStaged(s, item.id)) return "Staged";
  const blocks = s.overrides[item.id]?.blocks ?? item.blocks;
  if (blocks === 0) return "All-day";
  const pin = s.placements[item.id];
  if (pin) return `Pinned ${display12h(pin)}`;
  if (
    item.source === "todoist" &&
    typeof item.scheduledStart === "string" &&
    NATIVE_TIME.test(item.scheduledStart)
  ) {
    return `Source time ${display12h(item.scheduledStart)}`;
  }
  return "Automatic start";
}

/** KTD4/KTD5: the per-row Assigned/Unassigned switch, the only assignment
    control. A native checkbox with role="switch", so Space toggles it and the
    state is exposed as aria-checked. It is gated like every other row action:
    before Day Setup is confirmed it is disabled and the store's reason is
    announced through aria-describedby. The label owns a 44px hit area while the
    track stays 20px high, so a row does not grow. */
export function AssignSwitch({
  id,
  name,
  assigned,
  s,
}: {
  id: string;
  name: string;
  assigned: boolean;
  s: AppState;
}) {
  const { controller } = useApp();
  const why = taskActionDisabledReason(s, "complete");
  const whyId = `why-assign-${id.replace(/[^A-Za-z0-9_-]+/g, "-")}`;
  const off = s.runtimeBusy || why !== null;
  const caption = assigned ? "Assigned" : "Unassigned";
  return (
    <label
      class={`trow__assign${off ? " trow__assign--off" : ""}`}
      title={why ?? (assigned ? "Unassign for today" : "Re-assign to today")}
    >
      <input
        type="checkbox"
        role="switch"
        class="trow__switch"
        aria-checked={assigned}
        aria-label={`Assigned: ${name}`}
        aria-describedby={why ? whyId : undefined}
        checked={assigned}
        disabled={off}
        onChange={() => void controller.setAssigned(id, !assigned)}
      />
      <span class="trow__assign-text">{caption}</span>
      {why && (
        <span class="sr-only" id={whyId}>
          {why}
        </span>
      )}
    </label>
  );
}

export function TaskRow({
  item,
  s,
  hierarchy,
  structureTitles,
  onKeyDown,
}: {
  item: AssignedItem;
  s: AppState;
  hierarchy?: HierarchyRow;
  structureTitles?: Record<string, string>;
  onKeyDown?: (e: KeyboardEvent) => void;
}) {
  const { controller } = useApp();
  const state = queueState(s, item.id);
  const chip = sourceChip(item);
  const kind = kindLabel(item, structureTitles);
  const urgency = normalizeUrgency(item);
  const due = dueLabel(item.deadline, s.inputs?.validDate ?? "");
  const blocks = effectiveBlocks(s, item.id);
  const selected = isRowSelected(s, item.id);
  const expanded = s.ui.expandedId === item.id;
  const staged = draftIsStaged(s, item.id);
  const nameId = rowId(item, "name");
  const expId = rowId(item, "exp");

  // Disabled reasons: the store's one answer, shown as title + linked text.
  const completeWhy = taskActionDisabledReason(s, "complete");
  const skipWhy = taskActionDisabledReason(s, "skip");
  const completeWhyId = rowId(item, "why-complete");
  const skipWhyId = rowId(item, "why-skip");
  // A Capacities object has no completable source, so its Complete is Skip
  // today. The visible label stays Complete; the title and the description
  // element say what actually happens, so keyboard and screen-reader users are
  // told as well as the pointer.
  const capacitiesComplete = item.source === "capacities";
  const capacitiesWhyId = rowId(item, "why-capacities-complete");
  const completeOff = s.runtimeBusy || completeWhy !== null;
  const skipOff = s.runtimeBusy || skipWhy !== null;
  // KTD4: on a Capacities or calendar row Skip today is the same `dropped[]`
  // state the switch writes, so the switch replaces it there.
  const skipHidden = item.source === "capacities" || item.source === "calendar";

  const scheduledRow = s.sequence?.find((r) => r.id === item.id && r.kind === "work");
  // 04-overlap-note: explicit overlap grants and the operator "May overlap
  // Mint" grant render the same note; the partner resolves from the assigned
  // list, falling back to the raw row id.
  const grantFingerprint =
    s.planningConfigFingerprint ?? s.inputs?.planningConfigFingerprint ?? null;
  const assignedNames = new Map((s.inputs?.assigned ?? []).map((a) => [a.id, a.name]));
  const rowOverlaps = [
    ...overlapNotes(s.sequence, s.overlapGrants, grantFingerprint),
    ...mintOverlapNotes(s.sequence, s.mintOverlapOptIns),
  ].filter((n) => n.rowId === item.id);
  // Meta detail: the estimate, provenance context, scheduled time and
  // fixed/recurring note ride the meta line's title and an sr-only span.
  const metaDetail = [
    `est ${blocksLabel(blocks)}`,
    sourceContext(item),
    scheduledRow ? display12h(scheduledRow.start) : "",
    item.isRecurring && item.scheduledStart
      ? `fixed · recurring · ${display12h(item.scheduledStart)}`
      : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      class={`qrow trow ${state === "excluded" ? "qrow--excluded" : ""} ${state === "background" ? "qrow--background" : ""} ${hierarchy?.depth ? "qrow--nested" : ""} ${hierarchy?.groupKey ? "qrow--grouped" : ""}${expanded ? " trow--open" : ""}`}
      data-hierarchy-depth={hierarchy?.depth ?? 0}
      data-group-key={hierarchy?.groupKey ?? undefined}
      tabIndex={0}
      onKeyDown={(e) => onKeyDown?.(e as unknown as KeyboardEvent)}
    >
      <input
        type="checkbox"
        class="trow__check"
        checked={selected}
        aria-label={`Select ${item.name}`}
        onChange={(e) =>
          controller.selectRow(item.id, (e.target as HTMLInputElement).checked)
        }
      />
      <div class="qrow__body">
        <span class="qrow__nameline">
          {hierarchy?.depth ? (
            <span class="qrow__branch" aria-hidden="true">↳</span>
          ) : null}
          {hierarchy?.groupLabel ? (
            <span class="qrow__group" title={hierarchy.groupLabel}>
              {hierarchy.groupStart ? hierarchy.groupLabel : "same group"}
            </span>
          ) : null}
          <span class="qrow__name" id={nameId} title={item.path ?? item.name}>
            {item.name}
          </span>
        </span>
        {hierarchy?.depth && hierarchy.parentName ? (
          <span class="qrow__relationship">child of {hierarchy.parentName}</span>
        ) : null}
        <span class="qrow__source" title={metaDetail || (item.path ?? undefined)}>
          <span class={`qrow__swatch qrow__swatch--${chip.key}`} aria-hidden="true" />
          <span class={`qrow__src qrow__src--${chip.key}`}>{chip.label}</span>
          {kind && (
            <span
              class={`qrow__type qrow__type--${item.source === "capacities" ? "structure" : kind}`}
            >
              {kind}
            </span>
          )}
          <span class="qrow__meta sr-only">{metaDetail}</span>
          {due && (
            <span
              class={due.tone ? `due due--${due.tone}` : "due"}
              title={item.deadline ?? undefined}
            >
              {due.text}
            </span>
          )}
          {urgency?.tier && (
            <span class={`qrow__prio qrow__prio--${urgency.tier}`} title="Priority">
              {PRIORITY_LABEL[urgency.tier]}
            </span>
          )}
          <span class={`trow__state${staged ? " trow__state--staged" : ""}`}>
            {shortState(s, item)}
          </span>
        </span>
        {rowOverlaps.map((n) => {
          const partner = assignedNames.get(n.partnerId) ?? n.partnerId;
          const text = `Shares time with ${partner}: ${n.reason}`;
          return (
            <span
              key={n.partnerId}
              class="qrow__overlap"
              role="note"
              data-partner={n.partnerId}
              title={text}
            >
              {text}
            </span>
          );
        })}
      </div>
      <span class="trow__dur" title={blocks === 0 ? "All day — no capacity" : undefined}>
        {blocksLabel(blocks)}
      </span>
      <div class="trow__acts">
        <AssignSwitch id={item.id} name={item.name} assigned={true} s={s} />
        <button
          type="button"
          class="trow__btn trow__btn--primary"
          aria-label="Complete"
          disabled={completeOff}
          title={
            completeWhy ?? (capacitiesComplete ? CAPACITIES_COMPLETE_NOTE : undefined)
          }
          aria-describedby={
            [completeWhy ? completeWhyId : null, capacitiesComplete ? capacitiesWhyId : null]
              .filter(Boolean)
              .join(" ") || undefined
          }
          onClick={() => void controller.runRowBatch(item.id, "complete")}
        >
          Complete
        </button>
        {completeWhy && (
          <span class="sr-only" id={completeWhyId}>
            {completeWhy}
          </span>
        )}
        {capacitiesComplete && (
          <span class="sr-only" id={capacitiesWhyId}>
            {CAPACITIES_COMPLETE_NOTE}
          </span>
        )}
        {!skipHidden && (
          <button
            type="button"
            class="trow__btn"
            aria-label="Skip today"
            disabled={skipOff}
            title={skipWhy ?? undefined}
            aria-describedby={skipWhy ? skipWhyId : undefined}
            onClick={() => void controller.skipToday(item.id)}
          >
            Skip today
          </button>
        )}
        {!skipHidden && skipWhy && (
          <span class="sr-only" id={skipWhyId}>
            {skipWhy}
          </span>
        )}
        <button
          type="button"
          class="trow__btn"
          aria-label={expanded ? "Collapse" : "Expand"}
          aria-expanded={expanded}
          aria-controls={expId}
          onClick={() => controller.expandRow(expanded ? null : item.id)}
        >
          {expanded ? "Collapse" : "Expand"}
        </button>
      </div>
      {expanded && (
        <div
          class="trow__exp"
          id={expId}
          role="group"
          aria-labelledby={nameId}
          data-slot="row-controls"
        >
          <RowExpanded item={item} s={s} />
        </div>
      )}
    </div>
  );
}
