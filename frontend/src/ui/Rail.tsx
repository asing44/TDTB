/* Rail — compact day overview. The scheduling answer is pinned: a compact
   task-room status sits outside the scroll region showing selected / task
   room and either the available amount or the over-allotment instruction.
   The capacity story it summarizes scrolls beneath it as supporting evidence
   — the five-quantity ledger, the inspectable pie with its legend and
   complete readout, and the keyboard reference — and the readiness chips
   stay pinned to the bottom. The main surface stays focused on assigned rows
   and their local controls.

   2026-10-06 cockpit feedback item 4: the pie and remaining readouts are
   re-presented, not removed. The status carries the always-visible signal;
   the pie keeps its full inspection contract at reduced emphasis. Aggregate
   task room is not a contiguous free calendar window, and the status wording
   says so by naming task room rather than calendar space.

   Numbers follow the same live substitution as the table (localSelected in,
   server capacity authoritative on refresh) so the rail and the rows answer
   with one number. */

import { useAppState } from "./context";
import { budgetTotal, localSelected } from "../store/allocatorView";
import { AllocationPie } from "./AllocationPie";
import { ReadinessStrip } from "./ReadinessStrip";
import { display12h, formatBlockAmount } from "../model/time";

const DAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function prettyDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return `${DAY[d.getDay()]} ${d.getDate()} ${MON[d.getMonth()]}`;
}

/** Capacity ledger + segmented bar. Bar basis is the larger of the frame
    total and everything spent, so an over day extends past the budget line
    into a hatched overflow segment instead of silently rescaling. */
function BudgetCard() {
  const s = useAppState();
  const cap = s.capacity;
  if (!cap) return null;

  const spend = localSelected(s);
  const budget = budgetTotal(s);
  const reserved = cap.total - cap.availableForSelection;
  const over = Math.max(0, spend - budget);
  const usedAll = cap.fixed + cap.anchored + cap.habits + cap.mint + cap.buffer + spend;
  const basis = Math.max(usedAll, cap.total, 1);
  const seg = (blocks: number) => `${(Math.max(0, blocks) / basis) * 100}%`;
  const overflow = Math.max(0, usedAll - cap.total);
  const capacityLabel = [
    `Fixed ${formatBlockAmount(cap.fixed)}`,
    `Anchored ${formatBlockAmount(cap.anchored)}`,
    `Habits ${formatBlockAmount(cap.habits)}`,
    `Mint ${formatBlockAmount(cap.mint)}`,
    `Selected ${formatBlockAmount(spend)}`,
    `Buffer ${formatBlockAmount(cap.buffer)}`,
    `Free ${formatBlockAmount(cap.free)}`,
    `Total ${formatBlockAmount(cap.total)}`,
  ].join(" · ");

  return (
    <div class="rail__section" aria-label="Capacity">
      <div class="rail__label">Capacity</div>
      <dl class="rail-capacity">
        <div>
          <dt>Day capacity</dt>
          <dd>{formatBlockAmount(cap.total)}</dd>
        </div>
        <div>
          <dt>Reserved before tasks</dt>
          <dd>{formatBlockAmount(reserved)}</dd>
        </div>
        <div>
          <dt>Task room</dt>
          <dd>{formatBlockAmount(budget)}</dd>
        </div>
        <div>
          <dt>Chosen tasks</dt>
          <dd class={`rail-budget__spend ${over > 0 ? "rail-budget__spend--over rail-capacity__over" : ""}`}>
            {formatBlockAmount(spend)}
          </dd>
        </div>
        <div>
          <dt>Over by</dt>
          <dd class={over > 0 ? "rail-capacity__over" : ""}>
            {formatBlockAmount(over)}
          </dd>
        </div>
      </dl>
      <div
        class={`rail-budget__delta ${over > 0 ? "rail-budget__delta--over" : ""}`}
        role="status"
      >
        {over > 0
          ? `${formatBlockAmount(over)} over`
          : spend === budget
            ? "fully booked"
            : `${formatBlockAmount(budget - spend)} left`}
      </div>
      <p class="rail-capacity__note">Every chosen task is additive before Commit live.</p>
      <div class="rail-budget__barwrap">
        <div class="rail-budget__bar" role="img" aria-label={capacityLabel}>
          <div style={{ width: seg(cap.fixed), background: "var(--c-event)" }} />
          <div style={{ width: seg(cap.anchored), background: "var(--c-anchored)" }} />
          <div style={{ width: seg(cap.habits), background: "var(--c-habit)" }} />
          <div style={{ width: seg(cap.mint), background: "var(--c-minting)" }} />
          <div class="rail-budget__buffer" style={{ width: seg(cap.buffer) }} />
          <div
            style={{
              width: seg(Math.min(spend, spend - overflow)),
              background: "var(--c-selected)",
            }}
          />
          {overflow > 0 && <div class="rail-budget__overflow" style={{ width: seg(overflow) }} />}
          {/* Config's `free` tail — unspent capacity is a rendered segment, not
              the absence of one. Hatched rather than solid: free time is
              available, not allocated, and the diagonal says so at a glance
              (same device as the overflow hatch). */}
          {spend < budget && <div class="rail-budget__free" style={{ width: seg(budget - spend) }} />}
        </div>
        <div class="rail-budget__mark" style={{ left: `${(cap.total / basis) * 100}%` }} />
      </div>
    </div>
  );
}

/** S4 (feedback item 4): the pinned scheduling answer. Selected and task room
    are the same arithmetic the ledger and the table use (localSelected in,
    budgetTotal out), so a local duration or inclusion edit moves this status
    on the same frame. The over branch repeats the operator's own remedy
    (reduce durations or exclude) instead of proposing an automatic trim. */
function TaskRoomStatus() {
  const s = useAppState();
  if (!s.capacity) return null;

  const selected = localSelected(s);
  const taskRoom = budgetTotal(s);
  const over = Math.max(0, selected - taskRoom);
  const available = Math.max(0, taskRoom - selected);

  return (
    <section class="rail__status" aria-label="Room left">
      <div class="rail__status-head">
        <span class="rail__label">Room left</span>
        <p
          class={`rail__status-state ${over > 0 ? "rail__status-state--over" : ""}`}
          role="status"
        >
          {over > 0
            ? `${formatBlockAmount(over)} over — reduce durations or exclude`
            : `${formatBlockAmount(available)} available`}
        </p>
      </div>
      <p class="rail__status-ratio">
        {formatBlockAmount(selected)} selected / {formatBlockAmount(taskRoom)} task room
      </p>
    </section>
  );
}

function KeysCard() {
  const keys: Array<[string, string]> = [
    ["↑ ↓", "move row"],
    ["← →", "±15min"],
    ["x", "exclude today"],
    ["⏎", "mark done"],
  ];
  return (
    <div class="rail__section rail__section--keys" aria-label="Keyboard shortcuts">
      <div class="rail__label">Keys</div>
      <div class="rail-keys">
        {keys.map(([k, what]) => (
          <>
            <span class="rail-keys__key">{k}</span>
            <span>{what}</span>
          </>
        ))}
      </div>
    </div>
  );
}

export function Rail() {
  const s = useAppState();
  if (!s.inputs) return null;
  const t = s.inputs.time;
  const preset = s.inputs.daySemantics.selectedPreset?.name;

  return (
    <aside class="rail" aria-label="Day overview">
      <div class="rail__date">
        <div class="rail__date-top">
          <div>
            <div class="rail__kicker">Planning cockpit</div>
            <div class="rail__date-day">{prettyDate(s.inputs.validDate)}</div>
          </div>
        </div>
        <div class="rail__date-meta">
          {/* 12-hour everywhere the user reads a time — the wire carries 24h
              HH:MM, the UI never shows it raw. */}
          {display12h(t.now)}
          {preset ? ` · ${preset}` : ""} · frame {display12h(t.anchor)}–
          {display12h(t.effectiveEod)}
        </div>
      </div>
      <TaskRoomStatus />
      {/* Everything below the status scrolls; the date, the status, and the
          chips are pinned — the chips carry the Sources refresh, and on a
          busy day the ledger, pie, and keys grew tall enough to push them off
          the bottom of the rail. A control you have to go looking for is a
          control that is missing.
          S4 (feedback item 4): the pinned status now owns the always-visible
          scheduling signal, so the donut no longer sticks inside this region.
          The chart scrolls with the evidence it supports instead of
          competing with the status for the eye. CSS still flattens the pie
          wrapper (`display: contents`) so the chart and legend are direct
          children of this box; that is layout, not emphasis. */}
      <div class="rail__scroll">
        <BudgetCard />
        <div class="rail__pie">
          <AllocationPie />
        </div>
        <KeysCard />
      </div>
      <ReadinessStrip />
    </aside>
  );
}
