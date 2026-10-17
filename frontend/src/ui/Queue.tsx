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
import { effectiveBlocks, queueState, type AppState } from "../store/store";
import type { QueueState } from "../model/types";
import type { HierarchyRow } from "../model/allocatorView";
import { taskTableSections } from "../store/allocatorView";
import { formatBlockAmount } from "../model/time";
import { MORE_VERBS, type StagingVerbSpec } from "../model/staging";
import { isCanonicalCapacitiesIdentity } from "../adapters/wire";
import type { AssignedItem, TodayOverride } from "../model/types";
import { AlreadyPlaced } from "./AlreadyPlaced";
import { CapacityCard } from "./CapacityCard";
import { Tooltip } from "./Tooltip";
import { RowIcon } from "./RowIcon";
import { SkippedToday } from "./SkippedToday";
import { TaskRow, rowId } from "./TaskRow";

export { sourceChip, kindLabel } from "./TaskRow";

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

/** #18: the per-row Mint overlap grant entry for the More menu. Mirrors
    allDayEntry — one slot, both directions, disabled with a spelled-out reason
    for rows the grant cannot apply to. A Mint session row IS the wall, so it
    can never be granted an overlap of itself. */
type MintOverlapEntry = {
  label: string;
  detail: string;
  disabled: boolean;
  reason: string | null;
};

function mintOverlapEntry(
  item: AssignedItem,
  override: TodayOverride | undefined,
  on: boolean,
): MintOverlapEntry {
  const included = override?.included ?? true;
  const blocks = override?.blocks ?? item.blocks;
  let reason: string | null = null;
  if (/^Mint /i.test(item.name)) reason = "A Mint session is the wall itself";
  else if (!included) reason = "Excluded today — include the row first";
  else if (blocks === 0) reason = "All-day rows have no time to overlap";
  return {
    label: on ? "Stop overlapping Mint" : "May overlap Mint",
    detail:
      reason ??
      (on
        ? "Overlapping Mint allowed — always flagged"
        : "Allows this row to share time with a Mint session; always flagged"),
    disabled: reason != null,
    reason,
  };
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
  mintOptIn,
  override,
}: {
  item: AssignedItem;
  busy: boolean;
  state: QueueState;
  allowTimeAdjustment: boolean;
  optIn: boolean;
  mintOptIn: boolean;
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
  // #18: the row's Mint overlap grant entry (enabled/disabled state + reason).
  const mintOverlap = mintOverlapEntry(item, override, mintOptIn);

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
          {/* #18: per-row Mint overlap grant, adjacent to the all-day slot in
              the row-shaping group. Off by default (Mint is a hard wall); when
              on, the row is explicitly allowed to share time with a selected
              Mint session and the server always flags the overlap. */}
          <button
            role="menuitemcheckbox"
            aria-checked={mintOptIn}
            class="alloc-verb row-more__item"
            aria-label={`May overlap Mint for ${item.name}`}
            disabled={mintOverlap.disabled}
            title={mintOverlap.reason ?? undefined}
            onClick={() => {
              controller.setMintOverlapOptIn(item.id, !mintOptIn);
            }}
          >
            <span class="row-more__item-title">
              <span class="row-more__item-check" aria-hidden="true">
                {mintOptIn ? "✓" : ""}
              </span>
              {mintOverlap.label}
            </span>
            <span class="row-more__item-detail">{mintOverlap.detail}</span>
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

function ShortcutHint() {
  return (
    <div class="queue__hint" role="note" aria-label="Task table keyboard shortcuts">
      <span class="queue__hint-keys">
        Row shortcuts · <kbd>C</kbd> complete · <kbd>E</kbd> skip today ·{" "}
        <kbd>↑↓</kbd> move
      </span>
      <span class="queue__hint-note">
        Skip today records it on the server for today only; restore it from
        Skipped today. The source is untouched. Complete updates the source.
      </span>
    </div>
  );
}

/** Keys-card navigation: ↑/↓ move between rows, E skips the focused row for
    today, C marks it complete. Skips when the event originates in a control
    that owns the key (buttons own Enter). The old ←/→ duration trim and D
    exact-duration keys left with the slider and the Exact duration button;
    duration shaping returns with the expanded row panel. */
function rowKeydown(
  e: KeyboardEvent,
  item: AssignedItem,
  controller: {
    skipToday: (id: string) => Promise<unknown>;
    stagingAction: (v: string, id: string) => Promise<unknown>;
  },
) {
  const target = e.target as HTMLElement;
  const inControl = target.tagName === "INPUT" || target.tagName === "BUTTON";
  const row = (target.closest(".qrow") ?? target) as HTMLElement;
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;

  if ((key === "ArrowDown" || key === "ArrowUp") && !inControl) {
    const rows = Array.from(
      row.closest(".queue")?.querySelectorAll<HTMLElement>(".qrow") ?? [],
    );
    const i = rows.indexOf(row);
    const next = rows[key === "ArrowDown" ? i + 1 : i - 1];
    if (next) next.focus();
    e.preventDefault();
  } else if (key === "e" && !inControl) {
    void controller.skipToday(item.id);
    e.preventDefault();
  } else if (key === "c" && !inControl) {
    void controller.stagingAction("done", item.id);
    e.preventDefault();
  }
}

function Row({
  item,
  s,
  hierarchy,
  structureTitles,
}: {
  item: AssignedItem;
  s: AppState;
  hierarchy?: HierarchyRow;
  structureTitles?: Record<string, string>;
}) {
  const { controller } = useApp();
  const state = queueState(s, item.id);
  return (
    <TaskRow
      item={item}
      s={s}
      hierarchy={hierarchy}
      structureTitles={structureTitles}
      onKeyDown={(e) => rowKeydown(e, item, controller)}
      /* Transitional: More stays on the row (Adjust time, Allow move,
         Unschedule, all-day, Mint overlap, Always exclude, Delete) until the
         expanded panel and Source menu take those over. */
      more={
        <MoreMenu
          item={item}
          busy={s.runtimeBusy}
          state={state}
          optIn={s.timeAdjustmentOptIns[item.id] === true}
          mintOptIn={s.mintOverlapOptIns[item.id] === true}
          override={s.overrides[item.id]}
          allowTimeAdjustment={
            !item.isRecurring &&
            (!hasAdjustableNativeTime(item) || s.timeAdjustmentOptIns[item.id] === true)
          }
        />
      }
    />
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
    // The trailing lane for legacy browser-only exclusions starts collapsed.
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

  const hierarchyById = new Map(
    sections.flatMap((sec) => sec.rows).map((entry) => [entry.item.id, entry]),
  );
  const row = (i: AssignedItem) => (
    <Row
      key={i.id}
      item={i}
      s={s}
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
            // Durable skips render in the Skipped today section below. The
            // "out" lane now only carries legacy browser-only exclusions
            // that have not yet converted to a Skip today.
            const count = sec.rows.length;
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
                  label={sec.key === "out" ? "Excluded in this browser" : sec.label}
                  sub={
                    sec.key === "out"
                      ? "Legacy exclusion — converts to Skip today once Day Setup is confirmed"
                      : sec.sub
                  }
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
      <SkippedToday />
      <ShortcutHint />
    </section>
  );
}
