/* TaskRow — task-actions F4a: the collapsed task row from the approved
   prototype (design/task-actions r2). One row = an independent selection
   checkbox, the name with its source/type chips and a short state, the
   duration in mono, and three labelled buttons: Complete, Skip today, and
   Expand/Collapse. Selection never depends on expansion; one row is expanded
   at a time (store ui.expandedId). The expanded region mounts RowExpanded (F4b). */

import type { ComponentChildren } from "preact";
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

export function TaskRow({
  item,
  s,
  hierarchy,
  structureTitles,
  more,
  onKeyDown,
}: {
  item: AssignedItem;
  s: AppState;
  hierarchy?: HierarchyRow;
  structureTitles?: Record<string, string>;
  /** Transitional: the per-row More menu stays until the Source menu lands. */
  more?: ComponentChildren;
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
  const completeOff = s.runtimeBusy || completeWhy !== null;
  const skipOff = s.runtimeBusy || skipWhy !== null;

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
        <button
          type="button"
          class="trow__btn trow__btn--primary"
          aria-label="Complete"
          disabled={completeOff}
          title={completeWhy ?? undefined}
          aria-describedby={completeWhy ? completeWhyId : undefined}
          onClick={() => void controller.stagingAction("done", item.id)}
        >
          Complete
        </button>
        {completeWhy && (
          <span class="sr-only" id={completeWhyId}>
            {completeWhy}
          </span>
        )}
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
        {skipWhy && (
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
        {more}
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
