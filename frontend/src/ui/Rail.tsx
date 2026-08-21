/* Rail — compact day overview. The capacity story lives here beside the
   assigned work it describes: budget number + segmented bar, the inspectable
   pie, keyboard reference, and readiness chips pinned to the bottom. The main
   surface stays focused on assigned rows and their local controls.

   Numbers follow the same live substitution as the table (localSelected in,
   server capacity authoritative on refresh) so the rail and the rows answer
   with one number. */

import { useApp, useAppState } from "./context";
import type { Theme } from "../store/store";
import { budgetTotal, localSelected } from "../store/allocatorView";
import { AllocationPie } from "./AllocationPie";
import { refreshSummaryText } from "./ReadinessStrip";
import { display12h, formatBlockAmount } from "../model/time";

function themeLabel(t: Theme): string {
  return t === "system" ? "Auto" : t === "light" ? "Light" : "Dark";
}

function clock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : display12h(`${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`);
}

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
      <p class="rail-capacity__note">Every chosen task is additive before Send.</p>
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

/** The Sources refresh sits in the rail HEADER, not with the bottom chips.
    Pinned at the bottom it still landed under the sticky footer, which owns
    the last ~110px of every viewport — a control you have to hunt for reads as
    a control that isn't there. Top of the rail is always on screen. */
function SourcesButton() {
  const s = useAppState();
  const { controller } = useApp();
  if (!s.inputs) return null;
  const health = s.inputs.sourceHealth;
  const refresh = s.refresh;
  const loading = refresh.phase === "loading";

  return (
    <button
      class={`chip chip--btn rail__refresh-btn ${
        refresh.error
          ? "chip--err"
          : health === "ok"
            ? "chip--ok"
            : health === "degraded"
              ? "chip--warn"
              : "chip--err"
      }`}
      onClick={() => void controller.refreshSources()}
      disabled={loading}
      aria-busy={loading}
      aria-label={
        refresh.lastRefreshed
          ? `Refresh sources (last refreshed ${clock(refresh.lastRefreshed)})`
          : "Refresh sources"
      }
    >
      {loading ? "⟳ refreshing…" : `Sources ${health === "ok" ? "✓" : health} ↻`}
    </button>
  );
}

function Chips() {
  const s = useAppState();
  const { store } = useApp();
  if (!s.inputs) return null;

  const captures = s.daySetup.captures;
  const captureCount = [captures.intention, captures.forMeegy, captures.stoic].filter(
    (c) => c.trim() !== "",
  ).length;
  const ledger = s.ledger;
  const refresh = s.refresh;

  const cycleTheme = () => {
    const next: Theme =
      s.theme === "system" ? "light" : s.theme === "light" ? "dark" : "system";
    store.dispatch({ type: "THEME_SET", theme: next });
  };

  return (
    <div class="rail__chips">
      {s.inputs.daySemantics.selectedPreset && (
        <span class="chip">
          Preset {s.inputs.daySemantics.selectedPreset.name}
          {s.daySetup.dayPreset ? " · today" : " · automatic"}
        </span>
      )}
      {/* FEEDBACK-08 (A07): pending setup is the next action — the chip
          carries the pending state in accent styling and says what to do,
          instead of reading as a low-priority status note. */}
      <button
        class={`chip chip--btn ${
          s.daySetup.confirmed ? "chip--ok" : "chip--warn chip--setup-pending"
        }`}
        onClick={() => store.dispatch({ type: "UI", patch: { setupOpen: true } })}
        aria-label={
          s.daySetup.confirmed
            ? "Open day setup"
            : "Open day setup — setup not confirmed"
        }
      >
        {s.daySetup.confirmed ? "Setup ✓" : "Setup pending — start here"}
      </button>
      <button
        class={`chip chip--btn ${captureCount === 3 ? "chip--ok" : ""}`}
        onClick={() => store.dispatch({ type: "UI", patch: { setupOpen: true } })}
        aria-label="Open captures in day setup"
      >
        Captures {captureCount}/3
      </button>
      {ledger && (
        <span class={`chip ${ledger.remaining > 0 ? "" : "chip--warn"}`}>
          Calls {ledger.remaining}/{ledger.cap}
        </span>
      )}
      <button class="chip chip--btn" onClick={cycleTheme} aria-label="Cycle theme">
        Theme: {themeLabel(s.theme)}
      </button>
      {(refresh.error || refresh.lastRefreshed) && (
        <span class="rail__refresh" role="status">
          {refresh.error
            ? `Refresh failed: ${refresh.error} — showing last good data`
            : refresh.summary
              ? `Refreshed ${clock(refresh.lastRefreshed as string)} · ${refreshSummaryText(refresh.summary)}`
              : `Refreshed ${clock(refresh.lastRefreshed as string)}`}
        </span>
      )}
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
          <SourcesButton />
        </div>
        <div class="rail__date-meta">
          {/* 12-hour everywhere the user reads a time — the wire carries 24h
              HH:MM, the UI never shows it raw. */}
          {display12h(t.now)}
          {preset ? ` · ${preset}` : ""} · frame {display12h(t.anchor)}–
          {display12h(t.effectiveEod)}
        </div>
      </div>
      {/* Everything below the date scrolls, and only the date and the chips are
          pinned — the chips carry the Sources refresh, and on a busy day the
          budget card, pie, and keys grew tall enough to push them off the
          bottom of the rail. A control you have to go looking for is a control
          that is missing.
          The donut's CHART is `position: sticky` inside this region, so the
          crucial visual is on screen at every point of staging (Adam,
          2026-07-27 21:11) while costing its height only once. It is second,
          not first: the budget card reads before it and scrolls away under it.
          Pinning either one OUTSIDE this box (two earlier attempts) starved the
          scroll region on a short window — first clipping the keys card, then
          leaving the legend as a single peeking row. CSS flattens the pie
          wrapper (`display: contents`) so the chart is a direct child here:
          sticky is bounded by its parent, and only a direct child spans the
          whole scroll length. */}
      <div class="rail__scroll">
        <BudgetCard />
        <div class="rail__pie">
          <AllocationPie />
        </div>
        <KeysCard />
      </div>
      <Chips />
    </aside>
  );
}
