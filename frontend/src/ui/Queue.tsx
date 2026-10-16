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
import { trackFor, trackGradient } from "../model/bands";
import { DIRECT_VERBS, MORE_VERBS, type StagingVerbSpec } from "../model/staging";
import { sourceContext } from "../model/sourceContext";
import { isCanonicalCapacitiesIdentity } from "../adapters/wire";
import { dueLabel, normalizeUrgency } from "../model/urgency";
import type { AssignedItem, TodayOverride } from "../model/types";
import { AlreadyPlaced } from "./AlreadyPlaced";
import { CapacityCard } from "./CapacityCard";
import { Tooltip } from "./Tooltip";
import { RowIcon } from "./RowIcon";

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

/** More-menu armed-confirm sentinel for "Always exclude". It has no staging
    verb id: the commit runs through the controller's persistent-exclusion
    methods, not runtimeAction. Distinct from every StagingVerb string. */
const ALWAYS_EXCLUDE_CONFIRM = "always_exclude";

/** Undo-path copy for the always-exclude description line, per source
    surface (Todoist hidden list vs Capacities known objects). */
const ALWAYS_EXCLUDE_DETAIL = {
  todoist:
    "Hides this task from planning every day. Undo in Connections > Hidden tasks.",
  capacities:
    "Hides this task from planning every day. Undo in Connections > Capacities > Known objects.",
} as const;

/** The persistent-exclusion target a row can offer, or null. Todoist rows
    exclude by task id + title; Capacities rows exclude by the canonical
    object identity the settings route accepts. Calendar rows and rows without
    a stable identity offer nothing. */
type AlwaysExcludeTarget =
  | { kind: "todoist"; todoistId: string }
  | { kind: "capacities"; identity: string };

function alwaysExcludeTarget(item: AssignedItem): AlwaysExcludeTarget | null {
  if (item.source === "todoist") {
    return item.todoistId && item.todoistId.length > 0
      ? { kind: "todoist", todoistId: item.todoistId }
      : null;
  }
  if (item.source === "capacities") {
    const identity = item.capacitiesIdentity ?? item.identity ?? null;
    return isCanonicalCapacitiesIdentity(identity)
      ? { kind: "capacities", identity }
      : null;
  }
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

/** A native Todoist wall time (HH:MM). The shadow pins these rows to their
    own clock, so the all-day path cannot carry them (they stay NOOP). */
function hasNativeTodoistTime(item: AssignedItem): boolean {
  return (
    item.source === "todoist" &&
    typeof item.scheduledStart === "string" &&
    /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(item.scheduledStart)
  );
}

/** Issue #17: the More menu's all-day entry, in both directions. "Keep
    all-day" routes the row through the existing blocks == 0 path
    (`setOverride(id, included, 0)`), which the shadow renders as a date-only
    `schedule-all-day` due with no timed block. Only the rows that path can
    carry are enabled: a native-timed or recurring Todoist row keeps its own
    clock/pattern (NOOP), a Capacities row carries no Todoist due, and a
    Calendar row is calendar-only. Once this action set the all-day state, the
    same slot offers the reverse — clearing the session override restores the
    source's timed estimate. */
type AllDayEntry = {
  verb: "keep" | "restore";
  label: string;
  detail: string;
  disabled: boolean;
  reason: string | null;
};

function allDayEntry(
  item: AssignedItem,
  override: TodayOverride | undefined,
): AllDayEntry {
  const included = override?.included ?? true;
  const blocks = override?.blocks ?? item.blocks;
  const restorable =
    blocks === 0 && override?.blocks === 0 && included && item.blocks > 0;
  if (restorable) {
    return {
      verb: "restore",
      label: "Use a time block",
      detail: `Restores ${item.durationLabel}`,
      disabled: false,
      reason: null,
    };
  }
  let reason: string | null = null;
  if (!included) reason = "Excluded today — include the row first";
  else if (blocks === 0) reason = "Already all-day";
  else if (item.source === "capacities")
    reason = "Capacities rows carry no Todoist due";
  else if (item.source === "calendar")
    reason = "Calendar rows are calendar-only";
  else if (item.isRecurring) reason = "Recurring tasks keep their own pattern";
  else if (hasNativeTodoistTime(item)) reason = "Has a fixed Todoist time";
  return {
    verb: "keep",
    label: "Keep all-day",
    detail: reason ?? "All day — commits a date-only due",
    disabled: reason != null,
    reason,
  };
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
    "Hands it back for today and records that on the server, so a reload or another device keeps it. Undo from Out today. Source unchanged.",
};

/** IMP-07: one direct verb as a row button. Planning 2a composes the row's
    action cluster from individual verbs (Complete, Exclude today, Exact
    duration, Leave as-is, More) instead of two labelled groups, so each verb
    owns its button. Directness, the frozen verb mapping, and every aria name
    are unchanged. */
function VerbButton({
  spec,
  item,
  busy,
  armed,
  onArm,
}: {
  spec: StagingVerbSpec;
  item: AssignedItem;
  busy: boolean;
  armed: boolean;
  onArm: (verb: string | null) => void;
}) {
  const { controller } = useApp();
  return (
    <Tooltip
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
            onArm(spec.verb);
            return;
          }
          onArm(null);
          void controller.stagingAction(spec.verb, item.id);
        }}
        onBlur={() => armed && onArm(null)}
      >
        {armed ? "Sure?" : <RowIcon name={spec.verb === "done" ? "check" : "undo"} />}
      </button>
    </Tooltip>
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
  optIn,
  override,
}: {
  item: AssignedItem;
  busy: boolean;
  state: QueueState;
  allowTimeAdjustment: boolean;
  optIn: boolean;
  override: TodayOverride | undefined;
}) {
  const { controller, store } = useApp();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [excludeBusy, setExcludeBusy] = useState(false);
  const [excludeStatus, setExcludeStatus] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuId = rowId(item, "more");
  const excludeArmed = confirming === ALWAYS_EXCLUDE_CONFIRM;
  const excludeTarget = alwaysExcludeTarget(item);
  // Issue #17: the row's all-day entry (enabled/disabled state + reason).
  const allDay = allDayEntry(item, override);
  const allDayIncluded = override?.included ?? true;

  const close = (refocus: boolean) => {
    setOpen(false);
    setConfirming(null);
    setExcludeStatus(null);
    if (refocus) triggerRef.current?.focus();
  };

  /** Commit the armed always-exclude through the controller's persistent
      exclusion methods. The controller saves the full policy (409 retry
      included) and re-earns planning inputs itself, so the row leaves the
      plan on success. A save that throws leaves the row exactly as it was and
      the menu reports the error text; a failed post-save refresh is reported
      distinctly, because the exclusion DID persist. */
  const commitAlwaysExclude = async (target: AlwaysExcludeTarget) => {
    setConfirming(null);
    setExcludeBusy(true);
    setExcludeStatus(null);
    try {
      const result =
        target.kind === "todoist"
          ? await controller.alwaysExcludeTodoist({
              todoistId: target.todoistId,
              name: item.name,
            })
          : await controller.alwaysExcludeCapacitiesObject(target.identity);
      setExcludeStatus(
        result.refreshError
          ? `Always excluded, but the planning refresh failed: ${result.refreshError}`
          : `Always excluded: ${item.name}.`,
      );
    } catch (e) {
      setExcludeStatus(
        `Could not always exclude: ${e instanceof Error ? e.message : String(e)}`,
      );
    } finally {
      setExcludeBusy(false);
    }
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
      wrapRef.current?.querySelectorAll<HTMLElement>(
        '[role="menuitem"], [role="menuitemcheckbox"]',
      ) ?? [],
    );
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (i < 0) return;
    const next =
      items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length];
    next.focus();
  };

  /** One menu verb, one boxed row. The destructive verb renders inside the
      same component so its armed confirm never changes the box geometry. */
  const renderMenuVerb = (spec: StagingVerbSpec) => {
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
  };
  /* Delete is the menu's last stop, behind the hairline divider; more
     destructive verbs added later would join it there. */
  const deleteSpec = MORE_VERBS.find((spec) => spec.destructive);

  return (
    <div class="row-more" ref={wrapRef}>
      <Tooltip
        label="More"
        detail="Remove from planning, Delete, adjust time, allow move, unschedule."
        align="end"
        suppress={open}
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
          setExcludeStatus(null);
        }}
      >
        <RowIcon name="ellipsis" />
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
          {/* Planning 2a cleanup (2026-10-14): the "Allow move" permission
              moved off the row's meta line into this menu as a checkbox item.
              Same handler, same wire flag, same full aria-label; a
              Fixed/native-time row no longer carries an inline checkbox. */}
          {/* Fixed order (2026-10-14): row shaping (Adjust time, Allow move,
              Unschedule), then the source write (Remove from planning), then
              the destructive pair — Always exclude, and Delete last behind
              the hairline divider. Every item is the same boxed row. */}
          {allowTimeAdjustment && (state === "needs-placement" || state === "scheduled") && (
            <button
              role="menuitem"
              class="alloc-verb row-more__item"
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
          {hasAdjustableNativeTime(item) && (
            <button
              role="menuitemcheckbox"
              aria-checked={optIn}
              class="alloc-verb row-more__item"
              aria-label={`Allow scheduler to move existing time for ${item.name}`}
              onClick={() => {
                /* Toggle in place: the menu stays open, so the checkmark is
                   visible while it flips. */
                controller.setTimeAdjustmentOptIn(item.id, !optIn);
              }}
            >
              <span class="row-more__item-title">
                <span class="row-more__item-check" aria-hidden="true">
                  {optIn ? "✓" : ""}
                </span>
                Allow move
              </span>
              <span class="row-more__item-detail">
                {optIn
                  ? "Allowed — scheduler may move the Todoist start"
                  : "Protected — keeps the Todoist start fixed"}
              </span>
            </button>
          )}
          {state === "scheduled" && !item.isRecurring && (
            <button
              role="menuitem"
              class="alloc-verb row-more__item"
              aria-label={`Unschedule ${item.name}`}
              onClick={() => {
                close(false);
                controller.releasePlacement(item.id);
              }}
            >
              <span>Unschedule</span>
              <span class="row-more__item-detail">Today’s plan only</span>
            </button>
          )}
          {/* Issue #17: per-row all-day action. One slot, both directions:
              "Keep all-day" routes the row through the existing blocks == 0
              path (the shadow's date-only schedule-all-day due, no timed
              block); once that path set the state, "Use a time block" clears
              the override and restores the source estimate. Rows the all-day
              path cannot carry stay disabled with the reason in the title and
              the detail line. */}
          <button
            role="menuitem"
            class="alloc-verb row-more__item"
            aria-label={`${allDay.label} for ${item.name}`}
            disabled={allDay.disabled}
            title={allDay.reason ?? undefined}
            onClick={() => {
              close(false);
              controller.setOverride(
                item.id,
                allDayIncluded,
                allDay.verb === "restore" ? null : 0,
              );
            }}
          >
            <span>{allDay.label}</span>
            <span class="row-more__item-detail">{allDay.detail}</span>
          </button>
          {MORE_VERBS.filter((spec) => !spec.destructive).map((spec) => renderMenuVerb(spec))}
          {/* Planning 2a: persistent exclusion rides the More menu for rows
              the source policy can key on (todoist id + title; canonical
              Capacities identity). Destructive, so it arms first: shape the
              same armed-confirm as Delete, with the confirm question and the
              undo path in words. Escape, outside click, or moving focus away
              disarms. The commit disables the item while the save is in
              flight and reports the outcome (or the error text) in the
              menu's live status line. */}
          {excludeTarget && (
            <button
              role="menuitem"
              class={`alloc-verb row-more__item${excludeArmed ? " alloc-verb--armed" : ""}`}
              aria-label={
                excludeArmed
                  ? `Confirm always exclude: ${item.name}`
                  : `Always exclude: ${item.name}`
              }
              disabled={excludeBusy}
              onClick={() => {
                if (!excludeArmed) {
                  setExcludeStatus(null);
                  setConfirming(ALWAYS_EXCLUDE_CONFIRM);
                  return;
                }
                void commitAlwaysExclude(excludeTarget);
              }}
              onBlur={() => excludeArmed && setConfirming(null)}
            >
              <span>
                {excludeArmed
                  ? `Confirm: always exclude "${item.name}"?`
                  : "Always exclude"}
              </span>
              <span class="row-more__item-detail">
                {excludeTarget.kind === "capacities"
                  ? ALWAYS_EXCLUDE_DETAIL.capacities
                  : ALWAYS_EXCLUDE_DETAIL.todoist}
              </span>
            </button>
          )}
          {deleteSpec && (
            <>
              <div class="row-more__divider" role="separator" />
              {renderMenuVerb(deleteSpec)}
            </>
          )}
          {excludeStatus && (
            <div class="row-more__status" role="status" aria-live="polite">
              {excludeStatus}
            </div>
          )}
        </div>
      )}
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
        Exclude hides it in this browser; Leave as-is records it on the server.
        Both are today only, can be undone, and leave the source untouched.
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
  // Planning 2a cleanup (2026-10-14): the "saved" chip no longer paints
  // inside the readout. Its words stay for assistive tech (the sr-only
  // `.qrow__dur-label` live region in the column); a sighted user gets a tiny
  // green ✓ for ~1.5s, absolutely positioned so the readout cannot reflow.
  const [savedTick, setSavedTick] = useState(false);
  useEffect(() => {
    if (srcTag !== "remembered") {
      setSavedTick(false);
      return;
    }
    setSavedTick(true);
    const t = window.setTimeout(() => setSavedTick(false), 1500);
    return () => window.clearTimeout(t);
  }, [srcTag]);

  const scheduledRow = s.sequence?.find((r) => r.id === item.id && r.kind === "work");
  const track = trackFor(cumBefore, blocks, budgetTotal(s), MAX_BLOCKS);
  // Row meta detail: everything the mockup folds off the visible line (the
  // estimate, provenance context, scheduled time, fixed/recurring note). It
  // rides the meta line's title and an sr-only span, never the paint.
  const metaDetail = [
    srcTag === "source" ? `est ${blocksLabel(blocks)}` : "",
    sourceContext(item),
    scheduledRow ? display12h(scheduledRow.start) : "",
    item.isRecurring && item.scheduledStart
      ? `fixed · recurring · ${display12h(item.scheduledStart)}`
      : "",
  ]
    .filter(Boolean)
    .join(" · ");
  // Planning 2a action row: Complete, Exclude today, Exact duration, Leave
  // as-is, More — one flat icon cluster in that order. The armed state for a
  // destructive verb stays on the row.
  const [confirming, setConfirming] = useState<string | null>(null);
  const doneSpec = DIRECT_VERBS.find((spec) => spec.verb === "done")!;
  const leaveSpec = DIRECT_VERBS.find((spec) => spec.verb === "drop_from_plan")!;

  return (
    <div
      class={`qrow ${state === "excluded" ? "qrow--excluded" : ""} ${state === "background" ? "qrow--background" : ""} ${hierarchy?.depth ? "qrow--nested" : ""} ${hierarchy?.groupKey ? "qrow--grouped" : ""}`}
      data-hierarchy-depth={hierarchy?.depth ?? 0}
      data-group-key={hierarchy?.groupKey ?? undefined}
      tabIndex={0}
      onKeyDown={(e) => rowKeydown(e as unknown as KeyboardEvent, item, s, controller, store)}
    >
      {/* Planning 2a status control: a toggle button reading Assigned /
          Unassigned in its own leading column. It flips the SAME today-only
          inclusion the eye control flips — the checkbox and its pill label are
          gone, the action and the keyboard shortcut (E) are unchanged. */}
      <button
        type="button"
        class={`qrow__assign ${included ? "qrow__assign--on" : "qrow__assign--off"}`}
        aria-pressed={included}
        aria-label={`${included ? "Unassign" : "Assign"} ${item.name} for today`}
        title={
          included
            ? "Assigned today — activate to exclude it from today's plan"
            : "Unassigned today — activate to put it back in today's plan"
        }
        onClick={(e) => {
          e.stopPropagation();
          controller.setOverride(item.id, !included, s.overrides[item.id]?.blocks ?? null);
        }}
      >
        <span class="qrow__assign-text">{included ? "Assigned" : "Unassigned"}</span>
      </button>
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
          <span class="qrow__name" title={item.path ?? item.name}>
            {item.name}
          </span>
        </span>
        {hierarchy?.depth && hierarchy.parentName ? (
          <span class="qrow__relationship">child of {hierarchy.parentName}</span>
        ) : null}
        {/* 2026-10-06 cockpit feedback item 2: no would-drop flag rides this
            line any more. Over-allotment is reported as the day's actual
            selected/budget/overage (queue header, rail, dock); a row leaves
            the plan only when the operator excludes, completes, or deletes
            it. */}
        <span class="qrow__source" title={metaDetail || (item.path ?? undefined)}>
          {/* Planning 2a meta line: an 8px source swatch, the source name,
              then the row's human KIND, then plain meta. The raw uppercase
              type badge is gone — a Capacities structure id resolves through
              the structure-titles cache to its title, and an untitled one
              reads "Capacities item", never a UUID. */}
          <span class={`qrow__swatch qrow__swatch--${chip.key}`} aria-hidden="true" />
          <span class={`qrow__src qrow__src--${chip.key}`}>{chip.label}</span>
          {kind && (
            <span
              class={`qrow__type qrow__type--${item.source === "capacities" ? "structure" : kind}`}
            >
              {kind}
            </span>
          )}
          {/* Planning 2a cleanup (2026-10-14): the visible meta line is just
              swatch · source · due. The estimate, provenance context, and
              scheduled/fixed time stay in the DOM as sr-only text (and in
              this line's title), so the row reads clean while assistive tech
              and the tests keep reading the same facts. */}
          <span class="qrow__meta sr-only">
            {metaDetail}
          </span>
          {/* Planning 2a: the due date folds onto the meta line (source
              swatch + source name + due date); there is no separate due
              column. A "due tomorrow" reads in the warning colour. */}
          {due && (
            <span
              class={due.tone ? `due due--${due.tone}` : "due"}
              title={item.deadline ?? undefined}
            >
              {due.text}
            </span>
          )}
          {/* A zero-duration row stays exactly where it is and says what it
              became. Previously it jumped to a trailing All-day section the
              moment the slider hit bottom. Planning 2a: the marker is sr-only
              (the duration column already reads "All day") and the same
              sentence rides the row's title for hover. */}
          {state === "background" && (
            <span class="qrow__allday sr-only" title="All day — included, unscheduled, uses no capacity">
              all day
            </span>
          )}
        </span>
      </div>
      <div class="qrow__priority">
        {urgency?.tier && (
          <span
            class={`qrow__prio qrow__prio--${urgency.tier}`}
            title="Priority"
          >
            {PRIORITY_LABEL[urgency.tier]}
          </span>
        )}
      </div>
      <div class="qrow__time">
        {included ? (
          <>
            <div class="qrow__time-readout">
              <span
                class={`alloc-track__value ${track.pastBudget ? "alloc-track__value--over" : ""}`}
                aria-live="polite"
              >
                {blocksLabel(blocks)}
                {overridden ? "*" : ""}
              </span>
              {durLabel != null && (
                <span
                  class={`qrow__dur-label qrow__dur-label--${srcTag}`}
                  data-src={srcTag}
                  title={srcTagLabel(srcTag)}
                  aria-label={srcTagLabel(srcTag)}
                  role="status"
                  aria-live="polite"
                >
                  {durLabel}
                </span>
              )}
              {savedTick && (
                <span class="qrow__saved-tick" aria-hidden="true">
                  ✓
                </span>
              )}
            </div>
            <div class="qrow__time-line">
                  <span class="alloc-track" style={{ "--trackbg": trackGradient(track) }}>
                    <input
                      type="range"
                      class="alloc-track__input"
                      /* Half-block notches, all the way down to All day: 30min →
                         15min → All day. The row no longer relocates when it hits
                         zero (see bandedRows), so the bottom of the track is a
                         place you can safely drag to. Planning 2a: the ±
                         steppers are gone — the track itself is the pointer
                         control, the row's ←/→ keys trim by 15 min, and Exact
                         duration (clock, D) covers precise entry. */
                      min={MIN_BLOCKS}
                      max={MAX_BLOCKS}
                      step={HALF_BLOCK}
                      value={blocks}
                      /* An all-day row (0 blocks) has nothing to trim: the
                         label reads "All day" and the track is inert, as in
                         the mockup. Exact duration still gives it time. */
                      disabled={state === "background"}
                      title={
                        state === "background"
                          ? "All day — no capacity; use Exact duration to give it time"
                          : undefined
                      }
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
            </div>
          </>
        ) : (
          <span class="qrow__excluded-note">excluded today</span>
        )}
      </div>
      <div class="qrow__actions">
        {/* Planning 2a: five 34px icon buttons — Complete (filled green
            check), Exclude today, Exact duration, Leave as-is, More. The old
            two labelled groups and the divider are gone; every aria name,
            tooltip, and frozen verb mapping is unchanged. */}
        <VerbButton
          spec={doneSpec}
          item={item}
          busy={s.runtimeBusy}
          armed={confirming === doneSpec.verb}
          onArm={setConfirming}
        />
        <Tooltip
          label={included ? "Exclude today" : "Include today"}
          detail={
            included
              ? "Hides it from today's plan in this browser only. Source unchanged. Shortcut E."
              : "Puts it back in today's plan. Source unchanged. Shortcut E."
          }
          anchorClass="qrow__tip qrow__tip--plan"
        >
          <button
            class="planbtn"
            aria-label={`${included ? "Exclude" : "Include"} ${item.name} today`}
            onClick={() => controller.setOverride(item.id, !included, s.overrides[item.id]?.blocks ?? null)}
          >
            <RowIcon name={included ? "eye-off" : "eye"} />
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
              <RowIcon name="clock" />
            </button>
          </Tooltip>
        )}
        <VerbButton
          spec={leaveSpec}
          item={item}
          busy={s.runtimeBusy}
          armed={confirming === leaveSpec.verb}
          onArm={setConfirming}
        />
        <MoreMenu
          item={item}
          busy={s.runtimeBusy}
          state={state}
          optIn={s.timeAdjustmentOptIns[item.id] === true}
          override={s.overrides[item.id]}
          allowTimeAdjustment={
            !item.isRecurring &&
            (!hasAdjustableNativeTime(item) || s.timeAdjustmentOptIns[item.id] === true)
          }
        />
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
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({
    // Planning 2a: the trailing "Out today" lane starts collapsed, so the
    // plan reads as one scan of what is in today.
    out: true,
  });
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
        <header class="queue__head">
          <div>
            <h1>Assigned this morning</h1>
            <p>Edits here affect today only. Mark complete writes back to the source.</p>
          </div>
        </header>
        {/* Planning 2a: the timeline is the same card on a quiet day too. */}
        <AlreadyPlaced />
        <div class="plan-grid">
          <div class="plan-grid__groups">
            <div class="queue__quiet" role="status">
              <p>
                No assigned items today. Assignment happens upstream — Todoist
                Today and Capacities. A quiet day is a valid day.
              </p>
            </div>
          </div>
          <div class="plan-grid__capacity">
            <CapacityCard />
          </div>
        </div>
      </section>
    );
  }

  // KTD12: section membership is a view projection over the band/state model.
  const sections = taskTableSections(s);

  // One cumulative-spend walk in the table's own display order (Critical →
  // Fixed → When there's room), so the budget line lands on exactly one row's
  // track in the order the operator is reading.
  const cumBefore = new Map<string, number>();
  let cum = 0;
  for (const sec of sections) {
    if (sec.key === "out") continue;
    for (const entry of sec.rows) {
      if (!(s.overrides[entry.item.id]?.included ?? true)) continue;
      cumBefore.set(entry.item.id, cum);
      cum += effectiveBlocks(s, entry.item.id);
    }
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
      </header>
      <AlreadyPlaced />
      <div class="plan-grid">
        <div class="plan-grid__groups">
          {sections.map((sec) => {
            const count = sec.rows.length + sec.dropped.length;
            // Planning 2a: "Out today" is only rendered when something is
            // actually out, and it is placed last.
            if (sec.key === "out" && count === 0) return null;
            const open = isOpen(sec.key);
            const regionId = `tasktable-${sec.key}`;
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
                        <span class="qrow__assign qrow__assign--none" aria-hidden="true" />
                        <div class="qrow__body">
                          <span class="qrow__nameline">
                            <span class="qrow__name">{d.name}</span>
                          </span>
                          <span class="qrow__source">
                            dropped{d.droppedAt ? ` · ${droppedTimeLabel(d.droppedAt)}` : ""}
                          </span>
                        </div>
                        <div class="qrow__time">
                          <span class="qrow__excluded-note">out today</span>
                        </div>
                        <div class="qrow__priority" />
                        <div class="qrow__actions" />
                      </div>
                    ))}
                    {count === 0 && <div class="queue__empty">nothing here</div>}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div class="plan-grid__capacity">
          <CapacityCard />
        </div>
      </div>
      <ShortcutHint />
    </section>
  );
}
