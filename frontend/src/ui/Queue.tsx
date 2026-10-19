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

import { useEffect, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { effectiveBlocks, type AppState } from "../store/store";
import type { HierarchyRow } from "../model/allocatorView";
import { taskTableSections } from "../store/allocatorView";
import { formatBlockAmount } from "../model/time";
import type { AssignedItem } from "../model/types";
import { AlreadyPlaced } from "./AlreadyPlaced";
import { CapacityCard } from "./CapacityCard";
import { BatchBar, BatchPanel } from "./BatchActions";
import { SkippedToday } from "./SkippedToday";
import { UnassignedToday } from "./UnassignedToday";
import { TaskRow } from "./TaskRow";

export { sourceChip, kindLabel } from "./TaskRow";

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
        Capacities items have no Skip today: C or E sets them Unassigned for
        today, and the Assigned switch brings them back from Unassigned.
      </span>
    </div>
  );
}

/** Keys-card navigation: ↑/↓ move between rows, E skips the focused row for
    today, C completes it. A Capacities or calendar row has no completable
    write and no Skip today (KTD4), so C and E set it Unassigned instead. Skips when the event originates in a
    control that owns the key (buttons own Enter). The old ←/→ duration trim
    and D exact-duration keys left with the slider and the Exact duration
    button; duration shaping returns with the expanded row panel. */
function rowKeydown(
  e: KeyboardEvent,
  item: AssignedItem,
  controller: {
    skipToday: (id: string) => Promise<unknown>;
    setAssigned: (id: string, assigned: boolean) => Promise<unknown>;
    runRowBatch: (id: string, action: "complete") => Promise<unknown>;
  },
) {
  const target = e.target as HTMLElement;
  const inControl = target.tagName === "INPUT" || target.tagName === "BUTTON";
  const row = (target.closest(".qrow") ?? target) as HTMLElement;
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  const heldBySwitch = item.source === "capacities" || item.source === "calendar";

  if ((key === "ArrowDown" || key === "ArrowUp") && !inControl) {
    const rows = Array.from(
      row.closest(".queue")?.querySelectorAll<HTMLElement>(".qrow") ?? [],
    );
    const i = rows.indexOf(row);
    const next = rows[key === "ArrowDown" ? i + 1 : i - 1];
    if (next) next.focus();
    e.preventDefault();
  } else if (key === "e" && !inControl) {
    if (heldBySwitch) void controller.setAssigned(item.id, false);
    else void controller.skipToday(item.id);
    e.preventDefault();
  } else if (key === "c" && !inControl) {
    if (heldBySwitch) void controller.setAssigned(item.id, false);
    else void controller.runRowBatch(item.id, "complete");
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
  return (
    <TaskRow
      item={item}
      s={s}
      hierarchy={hierarchy}
      structureTitles={structureTitles}
      onKeyDown={(e) => rowKeydown(e, item, controller)}
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
      <BatchBar />
      <BatchPanel />
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
          <UnassignedToday />
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
