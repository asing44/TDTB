/* Queue — the allocator table, T12e redesign (3a spec) → IMP-07. Urgency
   BANDS (Critical / High / Everything else) replace planning-state as the
   primary grouping so critical rows surface in one scan (brief problem 3);
   Scheduled and Excluded today stay as trailing sections, and Dropped today
   (IMP-07 Drop from plan) renders as its own collapsible section. Each row
   is one aligned grid (stripe | name | due | time | actions) so a row's
   controls sit in fixed columns instead of drifting to the far edge (brief
   problem 2).

   IMP-07 final verb model (frozen action table): direct Done and
   Drop from plan on the row; Unassign and Delete live behind a per-row More
   disclosure menu. FEEDBACK-10 (A13): the row-shaping actions (Place at a
   specific time, Unschedule) also moved into More. S2 (cockpit UI feedback
   items 1 + 5): the cluster is one labelled affordance — planning
   adjustments (Exclude/Include today, Exact duration) sit in their own named
   group with visible scope notes, and item handling (direct Done and Leave
   as-is, then More) sits in theirs. The ⊘/✎ icon-only controls are gone; the
   frozen verb model (which actions are direct, and their IDs) is unchanged.
   Defer is not a product verb and is not rendered. The surface is named
   "Today's work" — "Queue" is never user-facing language.

   Band headers are disclosure buttons (contract item 6): keyboard-accessible,
   stateful (session) collapse/expand with row counts; collapsed bands keep
   their header and count visible while hiding rows. FEEDBACK-10 (A11): the
   collapsed state is announced in words (state chip + "N rows hidden"), not
   only a chevron glyph.

   The duration slider renders the day's budget line at the point where
   cumulative spend crosses it (per-row track, model/bands.ts) — the number
   that moves when a slider drags is on the slider itself (brief problem 1).

   All-day (0-block) items stay visible but never occupy capacity (locked
   decision 7). Source is a demoted dot + text under the name, never a
   grouping axis (locked decision 6). All controls keep the 44px floor. */

import { useEffect, useRef, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import {
  effectiveBlocks,
  queueState,
  type AppState,
} from "../store/store";
import type { QueueState } from "../model/types";
import {
  budgetTotal,
  includedDisplayOrder,
  type HierarchyRow,
} from "../model/allocatorView";
import { taskTableSections } from "../store/allocatorView";
import { blocksLabel, display12h, formatBlockAmount } from "../model/time";
import {
  HALF_BLOCK,
  MAX_BLOCKS,
  MIN_BLOCKS,
  clampDisplayBlocks,
  clampHalfBlocks,
  stepDownBlocks,
  stepUpBlocks,
} from "../model/allocator";
import { stripeColor, trackFor, trackGradient } from "../model/bands";
import { DIRECT_VERBS, MORE_VERBS } from "../model/staging";
import { sourceContext } from "../model/sourceContext";
import { dueLabel, normalizeUrgency } from "../model/urgency";
import type { AssignedItem } from "../model/types";
import { CalendarImpact } from "./CalendarImpact";
import { CapacityCard } from "./CapacityCard";
import { Tooltip } from "./Tooltip";

/** U11 labeling pass: the row's provenance as a chip a scan can read without
    parsing a sentence — TODOIST / CAPACITIES (plus the CALENDAR
    vocabulary the source counts already use). An unrecognized source reads
    as a neutral OTHER chip. Colour tints live in app.css. */
export function sourceChip(item: AssignedItem): { key: string; label: string } {
  const source = String(item.source);
  if (source === "todoist") return { key: "todoist", label: "TODOIST" };
  if (source === "capacities") return { key: "capacities", label: "CAPACITIES" };
  if (source === "calendar") return { key: "calendar", label: "CALENDAR" };
  return { key: "other", label: "OTHER" };
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

/** Duration provenance, spoken. It is the title/aria-label on the visible
    chip; the chip's own wording comes from durLabelText. */
function srcTagLabel(tag: "memory" | "remembered" | "source"): string {
  if (tag === "memory") return "Duration: memory (today's session override)";
  if (tag === "remembered") return "Duration: remembered (durable memory)";
  return "Duration: source (source-resolved)";
}

/** Visible chip wording — plain, not the internal tag. Source-resolved rows
    are the default and render no chip at all. */
function durLabelText(tag: "memory" | "remembered" | "source"): string | null {
  if (tag === "memory") return "today only";
  if (tag === "remembered") return "saved";
  return null;
}

function hasAdjustableNativeTime(item: AssignedItem): boolean {
  return (
    item.source === "todoist" &&
    item.id.length > 0 &&
    Number.isFinite(item.blocks) &&
    item.blocks > 0 &&
    typeof item.scheduledStart === "string" &&
    /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(item.scheduledStart)
  );
}

/** Stable id for aria-controls/aria-labelledby pairs — item ids are human
    names with spaces and punctuation, and ids must not contain them. */
function rowId(item: AssignedItem, suffix: string): string {
  return `${suffix}-${item.id.replace(/[^A-Za-z0-9_-]+/g, "-")}`;
}

/** Drop timestamps arrive as ISO ("2026-08-13T08:00:00Z"); render as a
    compact 12-hour time like every other wall-clock readout (user
    preference; FEEDBACK-09/11 24h presentation superseded). */
function droppedTimeLabel(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const hhmm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return display12h(hhmm);
}

/** Hover/focus descriptions for the row buttons, so each one explains itself. */
const VERB_HINT: Record<string, string> = {
  done: "Completes it in the source (Todoist or Capacities). Shortcut C.",
  drop_from_plan:
    "Takes it out of today's plan and leaves the source untouched. Today only.",
};

/** IMP-07: direct verbs (Done, Drop from plan) render on the row — one click,
    journaled through the staging path. Unassign and Delete live in MoreMenu. */
function StagingVerbs({ item, busy }: { item: AssignedItem; busy: boolean }) {
  const { controller } = useApp();
  const [confirming, setConfirming] = useState<string | null>(null);

  return (
    <>
      {DIRECT_VERBS.map((spec) => {
        const armed = confirming === spec.verb;
        return (
          <Tooltip
            key={spec.verb}
            label={spec.label}
            detail={VERB_HINT[spec.verb]}
            anchorClass={`qrow__tip qrow__tip--${spec.verb}`}
          >
          <button
            class={`alloc-verb alloc-verb--${spec.verb}${armed ? " alloc-verb--armed" : ""}`}
            disabled={busy}
            aria-label={
              armed
                ? `Confirm ${spec.aria.toLowerCase()}: ${item.name}`
                : `${spec.aria}: ${item.name}`
            }
            onClick={() => {
              if (spec.destructive && !armed) {
                setConfirming(spec.verb);
                return;
              }
              setConfirming(null);
              void controller.stagingAction(spec.verb, item.id);
            }}
            onBlur={() => armed && setConfirming(null)}
          >
            {armed ? "Sure?" : <><span>{spec.label}</span><span class="alloc-verb__detail">{spec.detail}</span></>}
          </button>
          </Tooltip>
        );
      })}
    </>
  );
}

/** IMP-07: per-row More disclosure exposing the frozen staging verbs
    (Remove from planning, Delete) plus — FEEDBACK-10 (A13) — the row-shaping
    actions that used to crowd the row (Adjust time, Unschedule). Menu-button
    pattern: aria-haspopup, aria-expanded, aria-controls; Arrow keys move
    between items; Escape closes and returns focus to the trigger; outside
    click closes. Delete arms to "Sure?" before it fires (permanent). */
function MoreMenu({
  item,
  busy,
  state,
  allowTimeAdjustment,
}: {
  item: AssignedItem;
  busy: boolean;
  state: QueueState;
  allowTimeAdjustment: boolean;
}) {
  const { controller, store } = useApp();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuId = rowId(item, "more");

  const close = (refocus: boolean) => {
    setOpen(false);
    setConfirming(null);
    if (refocus) triggerRef.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent | TouchEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        close(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close(true);
      }
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("touchstart", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("touchstart", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const onMenuKeyDown = (e: KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = Array.from(
      wrapRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [],
    );
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (i < 0) return;
    const next =
      items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length];
    next.focus();
  };

  return (
    <div class="row-more" ref={wrapRef}>
      <Tooltip
        label="More"
        detail="Remove from planning, Delete, adjust time, unschedule."
        align="end"
      >
      <button
        ref={triggerRef}
        class="alloc-verb alloc-verb--more row-more__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={`More actions for ${item.name}`}
        disabled={busy}
        onClick={() => {
          setOpen((o) => !o);
          setConfirming(null);
        }}
      >
        More
      </button>
      </Tooltip>
      {open && (
        <div
          class="row-more__menu"
          id={menuId}
          role="menu"
          aria-label={`Actions for ${item.name}`}
          onKeyDown={onMenuKeyDown}
        >
          {allowTimeAdjustment && (state === "needs-placement" || state === "scheduled") && (
            <button
              role="menuitem"
              class="row-more__item"
              aria-label={`Adjust time for ${item.name}`}
              onClick={() => {
                close(false);
                store.dispatch({
                  type: "UI",
                  patch: { editorItem: item.id, editorIntent: "place" },
                });
              }}
            >
              <span>Adjust time</span>
              <span class="row-more__item-detail">Today’s plan only</span>
            </button>
          )}
          {state === "scheduled" && !item.isRecurring && (
            <button
              role="menuitem"
              class="row-more__item"
              aria-label={`Unschedule ${item.name}`}
              onClick={() => {
                close(false);
                controller.releasePlacement(item.id);
              }}
            >
              Unschedule
            </button>
          )}
          {MORE_VERBS.map((spec) => {
            const armed = confirming === spec.verb;
            return (
              <button
                key={spec.verb}
                role="menuitem"
                class={`alloc-verb alloc-verb--${spec.verb} row-more__item${armed ? " alloc-verb--armed" : ""}`}
                data-source-mutation={spec.sourceMutation ? "true" : undefined}
                aria-label={
                  armed
                    ? `Confirm ${spec.aria.toLowerCase()}: ${item.name}`
                    : `${spec.aria}: ${item.name}`
                }
                onClick={() => {
                  if (spec.destructive && !armed) {
                    setConfirming(spec.verb);
                    return;
                  }
                  close(false);
                  void controller.stagingAction(spec.verb, item.id);
                }}
              >
                {armed ? "Sure?" : <><span>{spec.label}</span><span class="row-more__item-detail">{spec.detail}</span></>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Native Todoist times are protected until the user authorizes this specific
    row to move. Keep the permission beside the time evidence, rather than in
    the row action cluster, so it reads as planning input and not as a source
    mutation. */
function TimeAdjustmentOptIn({
  item,
  s,
}: {
  item: AssignedItem;
  s: AppState;
}) {
  const { controller } = useApp();
  if (!hasAdjustableNativeTime(item)) return null;

  const checked = s.timeAdjustmentOptIns[item.id] === true;
  const controlId = rowId(item, "time-adjustment");
  const statusId = `${controlId}-status`;
  const label = `Allow scheduler to move existing time for ${item.name}`;

  return (
    <div
      class={`qrow__time-adjustment${checked ? " qrow__time-adjustment--allowed" : ""}`}
      title={label}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <label class="qrow__time-adjustment-control" htmlFor={controlId}>
        <input
          id={controlId}
          type="checkbox"
          checked={checked}
          aria-label={label}
          aria-describedby={statusId}
          onChange={(e) =>
            controller.setTimeAdjustmentOptIn(
              item.id,
              (e.target as HTMLInputElement).checked,
            )
          }
        />
        {/* Compact one-line note; the full sentence rides title + aria-label
            so a dense row never wraps the permission into a one-char column. */}
        <span class="qrow__time-adjustment-note" aria-hidden="true">
          {checked ? "🔓 Allow move" : "🔒 Protected · Allow move"}
        </span>
      </label>
      <span id={statusId} class="qrow__time-adjustment-status sr-only" role="status">
        {checked
          ? "Allowed — scheduler may move the Todoist start"
          : "Protected — keeps the Todoist start fixed"}
      </span>
    </div>
  );
}

/** U11: urgency tier → the mockup's priority chip (p1 highest). */
const PRIORITY_LABEL: Record<string, string> = {
  crit: "p1",
  high: "p2",
  med: "p3",
  low: "p4",
};

/** U11 hint row: the shortcuts that actually work on the focused row. */
function ShortcutHint() {
  return (
    <div class="queue__hint" role="note" aria-label="Task table keyboard shortcuts">
      <span class="queue__hint-keys">
        Row shortcuts · <kbd>C</kbd> complete · <kbd>E</kbd> exclude ·{" "}
        <kbd>D</kbd> exact duration · <kbd>←→</kbd> ±15min · <kbd>↑↓</kbd> move
      </span>
      <span class="queue__hint-note">
        Exclude, Exact, and Leave as-is apply to today only and can be undone.
        Complete updates the source.
      </span>
    </div>
  );
}

/** Keys-card navigation (3a spec), re-keyed for U11: ↑/↓ move between rows,
    ←/→ shape the focused row's duration by ±15min, E toggles today-only
    exclusion, D opens the exact-duration editor, C marks complete. Skips when
    the event originates in a control that owns the key (the range input
    already handles arrows; buttons own Enter). */
function rowKeydown(
  e: KeyboardEvent,
  item: AssignedItem,
  s: AppState,
  controller: {
    setOverride: (id: string, inc: boolean, b: number | null) => void;
    stagingAction: (v: string, id: string) => Promise<unknown>;
  },
  store: { dispatch: (a: { type: "UI"; patch: { editorItem: string; editorIntent: "duration" } }) => void },
) {
  const target = e.target as HTMLElement;
  const inControl = target.tagName === "INPUT" || target.tagName === "BUTTON";
  const row = (target.closest(".qrow") ?? target) as HTMLElement;
  const included = s.overrides[item.id]?.included ?? true;
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;

  if ((key === "ArrowDown" || key === "ArrowUp") && !inControl) {
    const rows = Array.from(
      row.closest(".queue")?.querySelectorAll<HTMLElement>(".qrow") ?? [],
    );
    const i = rows.indexOf(row);
    const next = rows[key === "ArrowDown" ? i + 1 : i - 1];
    if (next) next.focus();
    e.preventDefault();
  } else if ((key === "ArrowLeft" || key === "ArrowRight") && !inControl) {
    if (!included) return;
    const current = effectiveBlocks(s, item.id);
    const next = key === "ArrowRight" ? stepUpBlocks(current) : stepDownBlocks(current);
    controller.setOverride(item.id, included, next);
    e.preventDefault();
  } else if (key === "e" && !inControl) {
    controller.setOverride(item.id, !included, s.overrides[item.id]?.blocks ?? null);
    e.preventDefault();
  } else if (key === "d" && !inControl) {
    store.dispatch({ type: "UI", patch: { editorItem: item.id, editorIntent: "duration" } });
    e.preventDefault();
  } else if (key === "c" && !inControl) {
    void controller.stagingAction("done", item.id);
    e.preventDefault();
  }
}

function Row({
  item,
  s,
  cumBefore,
  hierarchy,
  structureTitles,
}: {
  item: AssignedItem;
  s: AppState;
  cumBefore: number;
  hierarchy?: HierarchyRow;
  structureTitles?: Record<string, string>;
}) {
  const { controller, store } = useApp();
  const state = queueState(s, item.id);
  const chip = sourceChip(item);
  const kind = kindLabel(item, structureTitles);
  const urgency = normalizeUrgency(item);
  const due = dueLabel(item.deadline, s.inputs?.validDate ?? "");
  // Display clamp only: a stored override above the slider's range (e.g. 12
  // blocks saved before MAX_BLOCKS dropped to 10) must not show a readout the
  // thumb cannot reach. The override in state is left untouched.
  const blocks = clampDisplayBlocks(effectiveBlocks(s, item.id));
  const stepDown = stepDownBlocks(blocks);
  const stepUp = stepUpBlocks(blocks);
  const minusMinutes = Math.round((blocks - stepDown) * 30);
  const included = s.overrides[item.id]?.included ?? true;
  const overridden = s.overrides[item.id]?.blocks != null;
  // Duration-memory MVP: three distinct provenance states — "memory" (a
  // same-day session override holds the value), "remembered" (a durable
  // server-side memory entry), "source" (deterministic source resolution).
  const srcTag: "memory" | "remembered" | "source" = overridden
    ? "memory"
    : item.durationSource === "remembered"
      ? "remembered"
      : "source";
  const durLabel = durLabelText(srcTag);
  const scheduledRow = s.sequence?.find((r) => r.id === item.id && r.kind === "work");
  const track = trackFor(cumBefore, blocks, budgetTotal(s), MAX_BLOCKS);

  return (
    <div
      class={`qrow ${state === "excluded" ? "qrow--excluded" : ""} ${state === "background" ? "qrow--background" : ""} ${hierarchy?.depth ? "qrow--nested" : ""} ${hierarchy?.groupKey ? "qrow--grouped" : ""}`}
      data-hierarchy-depth={hierarchy?.depth ?? 0}
      data-group-key={hierarchy?.groupKey ?? undefined}
      tabIndex={0}
      onKeyDown={(e) => rowKeydown(e as unknown as KeyboardEvent, item, s, controller, store)}
    >
      <span class="qrow__stripe" style={{ background: stripeColor(item) }} />
      <div class="qrow__body">
        <div class="qrow__nameline">
          {/* The native checkbox stays the control (name, toggle, keyboard);
              the pill is its visual face. The label swallows clicks so the
              pill never opens the row. */}
          <label
            class={`qrow__assign ${included ? "qrow__assign--on" : "qrow__assign--off"}`}
            onClick={(e) => e.stopPropagation()}
          >
            <input
              type="checkbox"
              class="qrow__check"
              checked={included}
              aria-label={`Include ${item.name} in today's plan`}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
              onChange={(e) =>
                controller.setOverride(
                  item.id,
                  (e.target as HTMLInputElement).checked,
                  s.overrides[item.id]?.blocks ?? null,
                )
              }
            />
            <span class="qrow__assign-text">{included ? "Assigned" : "Unassigned"}</span>
          </label>
          {hierarchy?.depth ? (
            <span class="qrow__branch" aria-hidden="true">↳</span>
          ) : null}
          {hierarchy?.groupLabel ? (
            <span class="qrow__group" title={hierarchy.groupLabel}>
              {hierarchy.groupStart ? hierarchy.groupLabel : "same group"}
            </span>
          ) : null}
          <span class="qrow__name" title={item.path ?? item.name}>
            {item.name}
          </span>
        </div>
        {hierarchy?.depth && hierarchy.parentName ? (
          <span class="qrow__relationship">child of {hierarchy.parentName}</span>
        ) : null}
        {/* 2026-10-06 cockpit feedback item 2: no would-drop flag rides this
            line any more. Over-allotment is reported as the day's actual
            selected/budget/overage (queue header, rail, dock); a row leaves
            the plan only when the operator excludes, completes, or deletes
            it. */}
        <span class="qrow__source" title={item.path ?? undefined}>
          {/* U11 labeling: provenance leads as a coloured chip (TODOIST /
              CAPACITIES), then the row's human KIND, then plain meta.
              The raw uppercase type badge is gone — a Capacities structure id
              resolves through the structure-titles cache to its title, and an
              untitled one reads "Capacities item", never a UUID. */}
          <span class={`qrow__src qrow__src--${chip.key}`}>{chip.label}</span>
          {kind && (
            <span
              class={`qrow__type qrow__type--${item.source === "capacities" ? "structure" : kind}`}
            >
              {kind}
            </span>
          )}
          <span class="qrow__meta">
            {[
              srcTag === "source" ? `est ${blocksLabel(blocks)}` : "",
              sourceContext(item),
              scheduledRow ? display12h(scheduledRow.start) : "",
              item.isRecurring && item.scheduledStart
                ? `fixed · recurring · ${display12h(item.scheduledStart)}`
                : "",
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
          {/* A zero-duration row stays exactly where it is and says what it
              became. Previously it jumped to a trailing All-day section the
              moment the slider hit bottom. */}
          {state === "background" && (
            <span class="qrow__allday" title="All day — included, unscheduled, uses no capacity">
              all day
            </span>
          )}
          {/* POLISH-3: fixed rows carry the permission on the meta line, right
              after `fixed · recurring · 10 PM`, instead of a third line under
              the slider — so a Fixed row matches every other row's ~60px. */}
          <TimeAdjustmentOptIn item={item} s={s} />
        </span>
      </div>
      <div class="qrow__due">
        {urgency?.tier && (
          <span
            class={`qrow__prio qrow__prio--${urgency.tier}`}
            title="Priority"
          >
            {PRIORITY_LABEL[urgency.tier]}
          </span>
        )}
        {due && (
          <span
            class={due.tone ? `due due--${due.tone}` : "due"}
            title={item.deadline ?? undefined}
          >
            {due.text}
          </span>
        )}
      </div>
      <div class="qrow__time">
        {included ? (
          <>
            {/* 2026-10-12 (Adam): the duration readout leads the time column
                ABOVE the slider, on one line. The value holds the left edge;
                a non-default provenance chip sits immediately after it. */}
            <div class="qrow__time-readout">
              <span
                class={`alloc-track__value ${track.pastBudget ? "alloc-track__value--over" : ""}`}
                aria-live="polite"
              >
                {blocksLabel(blocks)}
                {overridden ? "*" : ""}
              </span>
              {/* FEEDBACK-10 (A08) + duration-memory MVP: provenance as a
                  visible chip, in plain words, right after the value. Source
                  rows are the default and render no chip; the * on the value
                  and the slider's aria-valuetext still carry that state. */}
              {durLabel != null && (
                <span
                  class={`qrow__dur-label qrow__dur-label--${srcTag}`}
                  data-src={srcTag}
                  title={srcTagLabel(srcTag)}
                  aria-label={srcTagLabel(srcTag)}
                >
                  {durLabel}
                </span>
              )}
            </div>
            <div class="qrow__time-line">
                {/* The slider's own notch is a 30-minute block; these are the
                    half-block trim the slider can't express without becoming
                    twice as twitchy to drag. They snap to the HALF-block grid —
                    routing them through clampBlocks made − a no-op at every
                    integer and turned + into a 30-minute jump. */}
                  <Tooltip
                    label={stepDown <= MIN_BLOCKS ? "All day (no capacity)" : `−${minusMinutes} min`}
                  >
                    <button
                      class="iconbtn alloc-step"
                      aria-label={
                        stepDown <= MIN_BLOCKS
                          ? `Make ${item.name} all day`
                          : `${minusMinutes} minutes less for ${item.name}`
                      }
                      disabled={blocks <= MIN_BLOCKS}
                      onClick={() =>
                        controller.setOverride(item.id, included, stepDown)
                      }
                    >
                      −
                    </button>
                  </Tooltip>
                  <span class="alloc-track" style={{ "--trackbg": trackGradient(track) }}>
                    <input
                      type="range"
                      class="alloc-track__input"
                      /* Half-block notches, all the way down to All day: 30min →
                         15min → All day. The row no longer relocates when it hits
                         zero (see bandedRows), so the bottom of the track is a
                         place you can safely drag to. Same grid as the ± steppers,
                         so the two controls can't disagree. */
                      min={MIN_BLOCKS}
                      max={MAX_BLOCKS}
                      step={HALF_BLOCK}
                      value={blocks}
                      aria-label={`${item.name} duration in 15-minute steps`}
                      /* FEEDBACK-10 (A08): the spoken value names WHERE the duration
                         came from — a session memory override, durable memory, or
                         the source. */
                      aria-valuetext={`${blocksLabel(blocks)} (${srcTag})`}
                      onInput={(e) =>
                        controller.setOverride(
                          item.id,
                          included,
                          clampHalfBlocks(Number((e.target as HTMLInputElement).value)),
                          { defer: true },
                        )
                      }
                    />
                    {track.markPct != null && (
                      <span class="alloc-track__mark" style={{ left: `${track.markPct}%` }} />
                    )}
                  </span>
                  <Tooltip label="+15 min">
                    <button
                      class="iconbtn alloc-step"
                      aria-label={`15 minutes more for ${item.name}`}
                      disabled={blocks >= MAX_BLOCKS}
                      onClick={() =>
                        controller.setOverride(item.id, included, stepUp)
                      }
                    >
                      +
                    </button>
                  </Tooltip>
            </div>
          </>
        ) : (
          <span class="qrow__excluded-note">excluded today</span>
        )}
      </div>
      <div class="qrow__actions">
        {/* S2 (feedback 1+5): the unlabelled ⊘/✎ pair left the operator
            guessing. Planning adjustments are labelled controls in a named
            group: both are today-only local selection/shaping and neither
            writes source, so they read as planning — not as verbs. The
            scope note spells the reversibility out. */}
        <div
          class="qrow__action-group qrow__action-group--plan"
          role="group"
          aria-label={`Planning adjustments for ${item.name}`}
        >
          <Tooltip
            label={included ? "Exclude today" : "Include today"}
            detail={
              included
                ? "Skips it for today only; reversible, source unchanged. Shortcut E."
                : "Puts it back in today's plan; source unchanged. Shortcut E."
            }
            anchorClass="qrow__tip qrow__tip--plan"
          >
            <button
              class="planbtn"
              aria-label={`${included ? "Exclude" : "Include"} ${item.name} today`}
              onClick={() => controller.setOverride(item.id, !included, s.overrides[item.id]?.blocks ?? null)}
            >
              <span class="planbtn__label">
                {included ? "Exclude today" : "Include today"}
              </span>
              <span class="planbtn__detail">
                today only · reversible · source unchanged
              </span>
            </button>
          </Tooltip>
          {/* T7: fine shaping on EVERY included row — the slider's 30-minute
              notches can't express 15/5-minute shaping. T12e: this editor is
              duration-only now; placement is its own More-menu action. */}
          {included && (
            <Tooltip
              label="Exact duration"
              detail="Sets the minutes precisely for today; source unchanged. Shortcut D."
              anchorClass="qrow__tip qrow__tip--plan"
            >
              <button
                class="planbtn"
                aria-label={`Exact duration for ${item.name}`}
                onClick={() =>
                  store.dispatch({
                    type: "UI",
                    patch: { editorItem: item.id, editorIntent: "duration" },
                  })
                }
              >
                <span class="planbtn__label">Exact duration</span>
                <span class="planbtn__detail">
                  today only · source unchanged
                </span>
              </button>
            </Tooltip>
          )}
        </div>
        <span class="qrow__divider" />
        {/* Item handling keeps the frozen IMP-07 directness: Mark complete
            and Leave as-is fire in one click; source removal/Delete and the
            placement actions stay behind More (FEEDBACK-10 A13). */}
        <div
          class="qrow__action-group qrow__action-group--item"
          role="group"
          aria-label={`Item handling for ${item.name}`}
        >
          <StagingVerbs item={item} busy={s.runtimeBusy} />
          <MoreMenu
            item={item}
            busy={s.runtimeBusy}
            state={state}
            allowTimeAdjustment={
              !item.isRecurring &&
              (!hasAdjustableNativeTime(item) || s.timeAdjustmentOptIns[item.id] === true)
            }
          />
        </div>
      </div>
    </div>
  );
}

/** U11 lane header: the grouped table's title + sub + total + collapse
    toggle. Keyboard-accessible disclosure with state announced in words
    (KTD10/KTD12); the Out today lane keeps the legacy `band--drop` hook. */
function LaneHeader({
  label,
  sub,
  count,
  open,
  onToggle,
  controlsId,
  drop,
}: {
  label: string;
  sub: string;
  count: number;
  open: boolean;
  onToggle: () => void;
  controlsId: string;
  drop?: boolean;
}) {
  const rows = `${count} row${count === 1 ? "" : "s"}`;
  return (
    <button
      type="button"
      class={`band band--lane${drop ? " band--drop" : ""}${open ? "" : " band--collapsed"}`}
      aria-expanded={open}
      aria-controls={controlsId}
      aria-label={`${label}, ${open ? "expanded" : "collapsed"}, ${rows}${open ? "" : " hidden"}, activate to ${open ? "collapse" : "expand"}`}
      onClick={onToggle}
    >
      <span class="band__tick" />
      <span class="band__label">{label}</span>
      <span class="band__note">{sub}</span>
      <span class="band__rule" />
      <span class="band__count">{rows}{open ? "" : " hidden"}</span>
      <span class={`band__state${open ? "" : " band__state--closed"}`}>
        {open ? "expanded" : "collapsed"}
      </span>
      <span class="band__chevron" aria-hidden="true">{open ? "▾" : "▸"}</span>
    </button>
  );
}

export function Queue() {
  const s = useAppState();
  const { controller } = useApp();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  // Capacities rows carry a raw structure id; the machine-local
  // structure-titles cache turns it into a human name. Loaded once, and only
  // when a Capacities row is actually on the table (most days: never), so the
  // title is advisory and its absence degrades to "Capacities item".
  const [structureTitles, setStructureTitles] = useState<Record<string, string>>({});
  const hasCapacitiesRows =
    !!s.inputs?.assigned.some((i) => i.source === "capacities");
  useEffect(() => {
    if (!hasCapacitiesRows) return;
    let cancelled = false;
    void controller
      .loadCapacitiesSettings()
      .then((settings) => {
        if (!cancelled) setStructureTitles(settings.structureTitles ?? {});
      })
      .catch(() => {
        /* Titles are advisory: an unreadable cache falls back per-row. */
      });
    return () => {
      cancelled = true;
    };
  }, [controller, hasCapacitiesRows]);
  if (!s.inputs) return null;

  const toggle = (key: string) =>
    setCollapsed((c) => ({ ...c, [key]: !(c[key] ?? false) }));
  const isOpen = (key: string) => !(collapsed[key] ?? false);

  const items = s.inputs.assigned;
  if (items.length === 0) {
    return (
      <section class="queue" aria-label="Assigned this morning">
        {/* U9/U11: the capacity card heads the frame even when nothing is
            assigned — a quiet day still needs its capacity story named. */}
        <header class="queue__head">
          <div>
            <h1>Assigned this morning</h1>
            <p>Edits here affect today only. Mark complete writes back to the source.</p>
          </div>
          <span class="queue__assigned-chip">assigned only</span>
        </header>
        <CapacityCard />
        {/* S5: calendar evidence stays with the work surface even on a quiet
            day — the empty-assigned branch keeps the inline band. */}
        <CalendarImpact inline />
        <div class="queue__quiet" role="status">
          <p>
            No assigned items today. Assignment happens upstream — Todoist
            Today and Capacities. A quiet day is a valid day.
          </p>
        </div>
      </section>
    );
  }

  // KTD12: section membership is a view projection over the band/state model.
  const sections = taskTableSections(s);

  // One cumulative-spend walk shared with the rail (allocatorView), so the
  // budget line lands on exactly one row's track.
  const ordered = includedDisplayOrder(s);
  const cumBefore = new Map<string, number>();
  let cum = 0;
  for (const r of ordered) {
    cumBefore.set(r.id, cum);
    cum += r.blocks;
  }

  const hierarchyById = new Map(
    sections.flatMap((sec) => sec.rows).map((entry) => [entry.item.id, entry]),
  );
  const row = (i: AssignedItem) => (
    <Row
      key={i.id}
      item={i}
      s={s}
      cumBefore={cumBefore.get(i.id) ?? cum}
      hierarchy={hierarchyById.get(i.id)}
      structureTitles={structureTitles}
    />
  );

  return (
    <section class="queue" aria-label="Assigned this morning">
      <header class="queue__head">
        <div>
          <h1>Assigned this morning</h1>
          <p>Edits here affect today only. Mark complete writes back to the source.</p>
        </div>
        <span class="queue__assigned-chip">assigned only</span>
      </header>
      <CapacityCard />
      <CalendarImpact inline />
      <ShortcutHint />
      {sections.map((sec) => {
        const open = isOpen(sec.key);
        const regionId = `tasktable-${sec.key}`;
        const count = sec.rows.length + sec.dropped.length;
        const blocks = sec.rows
          .filter((e) => s.overrides[e.item.id]?.included ?? true)
          .reduce((sum, e) => sum + effectiveBlocks(s, e.item.id), 0);
        return (
          <div
            key={sec.key}
            class={`queue__band tasktable tasktable--${sec.key}`}
            data-section={sec.key}
          >
            <LaneHeader
              label={sec.label}
              sub={sec.sub}
              count={count}
              open={open}
              onToggle={() => toggle(sec.key)}
              controlsId={regionId}
              drop={sec.key === "out"}
            />
            {open && (
              <div id={regionId} class="queue__band-rows tasktable__rows">
                <div class="tasktable__grouplabel">
                  <span class="tasktable__grouplabel-name">{sec.sub}</span>
                  <span class="tasktable__grouplabel-total">
                    {formatBlockAmount(blocks)} selected
                  </span>
                </div>
                {sec.rows.map((e) => row(e.item))}
                {sec.dropped.map((d) => (
                  <div key={d.identity} class="qrow qrow--dropped">
                    <span class="qrow__stripe" />
                    <div class="qrow__body">
                      <div class="qrow__nameline">
                        <span class="qrow__name">{d.name}</span>
                      </div>
                      <span class="qrow__source">
                        dropped{d.droppedAt ? ` · ${droppedTimeLabel(d.droppedAt)}` : ""}
                      </span>
                    </div>
                    <div class="qrow__due" />
                    <div class="qrow__time">
                      <span class="qrow__excluded-note">out today</span>
                    </div>
                    <div class="qrow__actions" />
                  </div>
                ))}
                {count === 0 && <div class="queue__empty">nothing here</div>}
              </div>
            )}
          </div>
        );
      })}
    </section>
  );
}
