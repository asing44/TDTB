/* Header — U7 page frame (KTD11). The retired left rail and floating tab pill
   are replaced by one persistent header: the date and workday-frame line, a
   three-step stepper bound to the existing `ui.screen` state, a source-health
   pill with ONE action, "Refresh sources", in both pill states, and a
   Settings control that opens the Settings hub on its "Is my data up to
   date?" entry. There is no second Settings control, so this pill IS the
   header's Settings entry point; its class name stays `header__connections`
   because app.css and the <=1100px compaction rules style it by that class.

   The stepper preserves ScreenNav's keyboard behavior (roving tabindex,
   Arrow/Home/End, visible focus, ARIA roles). Step 3 (Commit) maps onto the
   existing approval/commit flow — it is never a separate screen. */

import { footerBlockedReason } from "./Footer";
import { Fragment } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { display12h } from "../model/time";
import { formatRelativeShort } from "../model/relative";
import { countAdvisorySkipped } from "../adapters/wire";
import { ApiError } from "../adapters/api";
import { refreshProgressText, useCapacitiesRefreshJob } from "./useCapacitiesRefreshJob";

const DAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function prettyDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return `${DAY[d.getDay()]} ${d.getDate()} ${MON[d.getMonth()]}`;
}

function dateTimeLabel(d: Date): string {
  const date = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${date}, ${display12h(`${hh}:${mm}`)}`;
}

type StepId = "setup" | "plan" | "commit";

const STEPS: ReadonlyArray<{ id: StepId; label: string }> = [
  { id: "setup", label: "Set up day" },
  { id: "plan", label: "Plan tasks" },
  { id: "commit", label: "Commit" },
];

const stepId = (step: StepId) => `step-tab-${step}`;

/** The source-refresh actions shared by the header split-button and the
    Sources popover, so the two surfaces never duplicate the start logic.

    - `refreshAll`: the main press — Capacities job, then the snapshot read.
    - `refreshSnapshot`: Todoist & Calendar only (the snapshot read); never
      starts a Capacities job.
    - `refreshCapacities`: the Capacities job only, with no snapshot fallback;
      the terminal phase still reloads the snapshot when `reloadOnTerminal`.
    Only one mounted surface should pass `reloadOnTerminal` (the header), since
    every hook instance watches the same shared job. A surface that is not
    always visible passes `enabled` false until it is, so it issues no status
    read while hidden. */
export function useSourcesRefresh(reloadOnTerminal: boolean, enabled = true) {
  const s = useAppState();
  const { controller } = useApp();
  // Set only by the full press: a Capacities-only start that is refused must
  // not silently turn into a Todoist read the owner did not ask for.
  const fallbackOnRefusal = useRef(false);
  // One action in both pill states (R1, KTD1, KTD2): the pill's press starts
  // the Capacities refresh job, whose terminal phase re-reads the snapshot
  // (and with it Todoist). Called before any `s.inputs` guard so the hook
  // order stays stable across renders.
  const job = useCapacitiesRefreshJob(
    s.inputs !== null && enabled,
    reloadOnTerminal
      ? () => {
          void controller.refreshSources();
        }
      : undefined,
    // A source that cannot be prepared for a job refuses the start with 503.
    // The snapshot read still re-reads Todoist live, so it is the whole
    // refresh and runs directly instead of leaving the press dead.
    (error) => {
      if (fallbackOnRefusal.current && error instanceof ApiError && error.status === 503) {
        void controller.refreshSources();
      }
    },
  );
  const refreshing = s.refresh.phase === "loading" || job.running;
  // A click is in flight from the POST until the job's first status read: the
  // action stays disabled across that handoff so a double click cannot race a
  // second start (which the server would answer with a single-flight 409).
  const refreshBusy = refreshing || job.busy === "starting";
  const capacitiesConfigured = job.status?.configured !== false;
  const refreshSnapshot = () => {
    if (refreshBusy) return;
    void controller.refreshSources();
  };
  // Unconfigured Capacities has nothing to refresh: the local status read says
  // so up front, so the snapshot read is the whole action (no doomed POST).
  const refreshAll = () => {
    if (refreshBusy) return;
    if (!capacitiesConfigured) {
      void controller.refreshSources();
      return;
    }
    fallbackOnRefusal.current = true;
    void job.start("refresh");
  };
  const refreshCapacities = () => {
    if (refreshBusy || !capacitiesConfigured) return;
    fallbackOnRefusal.current = false;
    void job.start("refresh");
  };
  return {
    job,
    refreshing,
    refreshBusy,
    capacitiesConfigured,
    refreshAll,
    refreshSnapshot,
    refreshCapacities,
  };
}

export function Header() {
  const s = useAppState();
  const { store } = useApp();
  const {
    job,
    refreshing,
    refreshBusy,
    capacitiesConfigured,
    refreshAll,
    refreshSnapshot,
    refreshCapacities,
  } = useSourcesRefresh(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const splitRef = useRef<HTMLSpanElement>(null);
  const menuItems = () =>
    Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ?? []);

  useEffect(() => {
    if (!menuOpen) return;
    menuItems().find((el) => !el.disabled)?.focus();
    const onPointer = (e: MouseEvent | TouchEvent) => {
      if (splitRef.current && !splitRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setMenuOpen(false);
        toggleRef.current?.focus();
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
  }, [menuOpen]);
  // A run that starts elsewhere (or this press) hides the split; close the
  // menu so it cannot reappear stale when the run ends.
  useEffect(() => {
    if (refreshing && menuOpen) setMenuOpen(false);
  }, [refreshing, menuOpen]);

  if (!s.inputs) return null;

  const screen = s.ui.screen;
  const commitFlow = s.ui.approvalOpen || s.commitPhase !== "idle";
  // Step 3 is derived from the approval/commit phase, never a screen of its
  // own. Settings is a header control, not a step.
  const selectedStep: StepId =
    screen === "setup" ? "setup" : commitFlow ? "commit" : "plan";

  const done: Record<StepId, boolean> = {
    setup: s.daySetup.confirmed,
    plan: s.seqPhase === "valid" && s.validation?.ok === true,
    commit: s.commitPhase === "done",
  };
  const commitReady = s.seqPhase === "valid" && s.validation?.ok === true;
  const commitBlockedReason = commitReady
    ? null
    : (footerBlockedReason(s) ??
      (s.inputs.assigned.length === 0
        ? "Nothing to commit yet — no assigned work to plan today."
        : "Sequence the plan first — Commit opens once a valid sequence is staged."));

  const activate = (step: StepId, focusTarget?: HTMLElement | null) => {
    if (step === "setup") {
      store.dispatch({ type: "UI", patch: { screen: "setup", settingsPanel: null } });
    } else if (step === "plan") {
      store.dispatch({ type: "UI", patch: { screen: "plan", settingsPanel: null } });
    } else if (commitReady) {
      store.dispatch({ type: "UI", patch: { approvalOpen: true } });
    }
    (focusTarget ?? document.getElementById(stepId(step)))?.focus();
  };

  const pickItem = (run: () => void) => {
    setMenuOpen(false);
    toggleRef.current?.focus();
    run();
  };
  const onMenuKeyDown = (e: KeyboardEvent) => {
    const items = menuItems().filter((el) => !el.disabled);
    if (items.length === 0) return;
    const at = items.indexOf(document.activeElement as HTMLButtonElement);
    let next: number;
    if (e.key === "ArrowDown") next = (at + 1) % items.length;
    else if (e.key === "ArrowUp") next = (at - 1 + items.length) % items.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = items.length - 1;
    else if (e.key === "Tab") {
      setMenuOpen(false);
      return;
    } else return;
    e.preventDefault();
    items[next].focus();
  };
  const onToggleKeyDown = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown" && !menuOpen) {
      e.preventDefault();
      setMenuOpen(true);
    }
  };

  const onKeyDown = (e: KeyboardEvent) => {
    const base = STEPS.findIndex((step) => step.id === selectedStep);
    let next = base;
    if (e.key === "ArrowRight") next = (base + 1) % STEPS.length;
    else if (e.key === "ArrowLeft") next = (base + STEPS.length - 1) % STEPS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = STEPS.length - 1;
    else return;
    e.preventDefault();
    activate(STEPS[next].id);
  };

  const health = s.inputs.sourceHealth;
  const pillState = refreshing ? "refreshing" : health === "ok" ? "healthy" : "degraded";
  // One press, one meaning: the label never names a second mode. A run this
  // surface is watching reports its wait countdown and read progress; an old
  // rescan-mode job document still reads as a plain refresh.
  const refreshingLabel =
    (job.status?.progress.waiting ? refreshProgressText(job.status, job.nowMs) : null) ??
    "Refreshing sources…";
  const pillBase = refreshing
    ? refreshingLabel
    : health === "ok"
      ? "Sources healthy"
      : "Sources degraded · commit blocked";
  // Skipped Capacities objects no longer block planning, but they are left out
  // of the plan, so a healthy pill still says how many.
  const skippedCount = Math.max(
    countAdvisorySkipped(s.inputs.sourceWarnings),
    s.inputs.capacitiesIntake?.skipped?.length ?? 0,
  );
  const skippedNote =
    !refreshing && health === "ok" && skippedCount > 0
      ? `${skippedCount} Capacities objects were skipped (not plannable). They are not in the plan. See Capacity > Details > Sources.`
      : null;
  // Discoverability: the freshness evidence lives inside the collapsed
  // capacity-card Details disclosure, so the header pill carries the age and
  // a plain-language hint to where the details are.
  const now = new Date();
  const refreshedAt = s.refresh.lastRefreshed ? new Date(s.refresh.lastRefreshed) : null;
  const refreshed =
    refreshedAt && !Number.isNaN(refreshedAt.getTime()) ? refreshedAt : null;
  const age = refreshed ? formatRelativeShort(now, refreshed) : null;
  const pillLead = skippedNote ? `${pillBase} · ${skippedCount} skipped` : pillBase;
  const pillText = !refreshing && age ? `${pillLead} · ${age}` : pillLead;
  const pillAgeHint =
    age && refreshed
      ? `Sources last refreshed ${age} (${dateTimeLabel(refreshed)}). Open Capacity > Details > Sources for more.`
      : "Open Capacity > Details > Sources for more.";
  const pillHint = skippedNote ? `${skippedNote} ${pillAgeHint}` : pillAgeHint;

  const preset = s.inputs.daySemantics.selectedPreset?.name;
  const t = s.inputs.time;

  return (
    <header class="app-header" aria-label="Cockpit header">
      <div class="app-header__date">
        <span class="app-header__day">{prettyDate(s.inputs.validDate)}</span>
        <span class="app-header__frame">
          {preset ?? "Workday"} · {display12h(t.anchor)}–{display12h(t.effectiveEod)}
        </span>
      </div>

      <div
        class="app-stepper"
        role="tablist"
        aria-label="Planning steps"
        onKeyDown={(e) => onKeyDown(e as unknown as KeyboardEvent)}
      >
        {STEPS.map((step, i) => {
          const selected = selectedStep === step.id;
          return (
            <Fragment key={step.id}>
              {i > 0 && <span class="app-stepper__sep" aria-hidden="true" />}
              <button
                type="button"
                role="tab"
                id={stepId(step.id)}
                class={`app-stepper__step${selected ? " app-stepper__step--active" : ""}${
                  done[step.id] ? " app-stepper__step--done" : ""
                }${step.id === "commit" && commitBlockedReason !== null ? " app-stepper__step--blocked" : ""}`}
                aria-selected={selected}
                aria-disabled={step.id === "commit" && commitBlockedReason !== null ? true : undefined}
                title={step.id === "commit" && commitBlockedReason !== null ? commitBlockedReason : undefined}
                tabIndex={selectedStep === step.id ? 0 : -1}
                onClick={(e) => activate(step.id, e.currentTarget as HTMLElement)}
              >
                <span class="app-stepper__mark" aria-hidden="true">
                  {done[step.id] ? "✓" : i + 1}
                </span>
                <span class="app-stepper__label">{step.label}</span>
              </button>
            </Fragment>
          );
        })}
      </div>

      <div class="app-header__right">
        <span
          class={`header-pill health-pill health-pill--${pillState}`}
          role="status"
          aria-live="polite"
          title={pillHint}
          aria-label={pillHint}
        >
          <span class="health-pill__text">{pillText}</span>
        </span>
        {pillState !== "refreshing" && (
          // One shared class for the pill action: the <=1100px compaction styles
          // it by class and must keep seeing it regardless of label. The label
          // is the same in the healthy and degraded states — one control, no
          // separate Retry path — and a Capacities job that cannot run falls
          // back to the snapshot read inside the handler.
          <span class="header-split" ref={splitRef}>
            <button
              class="header-pill btn health-pill__retry"
              onClick={refreshAll}
              disabled={refreshBusy}
              aria-label="Refresh sources"
            >
              Refresh sources
            </button>
            <button
              ref={toggleRef}
              class="header-pill btn header-split__toggle"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              aria-controls="header-refresh-menu"
              aria-label="Refresh options"
              title="Choose which sources to refresh"
              disabled={refreshBusy}
              onClick={() => setMenuOpen(!menuOpen)}
              onKeyDown={(e) => onToggleKeyDown(e as unknown as KeyboardEvent)}
            >
              <span aria-hidden="true">{menuOpen ? "▴" : "▾"}</span>
            </button>
            {menuOpen && (
              <div
                class="header-split__menu"
                id="header-refresh-menu"
                role="menu"
                aria-label="Refresh sources by source"
                ref={menuRef}
                onKeyDown={(e) => onMenuKeyDown(e as unknown as KeyboardEvent)}
              >
                <button
                  type="button"
                  role="menuitem"
                  class="header-split__item"
                  disabled={refreshBusy}
                  onClick={() => pickItem(refreshSnapshot)}
                >
                  Todoist &amp; Calendar
                  <span class="header-split__hint">Fast, no Capacities wait</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  class="header-split__item"
                  disabled={refreshBusy || !capacitiesConfigured}
                  onClick={() => pickItem(refreshCapacities)}
                >
                  Capacities
                  <span class="header-split__hint">
                    {capacitiesConfigured ? "Paced, billed re-read" : "Not configured"}
                  </span>
                </button>
              </div>
            )}
          </span>
        )}
        <button
          class="header-pill btn header__connections"
          onClick={() =>
            store.dispatch({ type: "UI", patch: { settingsPanel: "freshness" } })
          }
        >
          Settings
        </button>
      </div>
    </header>
  );
}
