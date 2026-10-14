/* Header — U7 page frame (KTD11). The retired left rail and floating tab pill
   are replaced by one persistent header: the date and workday-frame line, a
   three-step stepper bound to the existing `ui.screen` state, a source-health
   pill with a Refresh action (Retry while degraded) wired to the existing
   refresh lifecycle, and a Connections link that opens the existing
   connections screen.

   The stepper preserves ScreenNav's keyboard behavior (roving tabindex,
   Arrow/Home/End, visible focus, ARIA roles). Step 3 (Commit) maps onto the
   existing approval/commit flow — it is never a separate screen. */

import { footerBlockedReason } from "./Footer";
import { Fragment } from "preact";
import { useApp, useAppState } from "./context";
import { display12h } from "../model/time";
import { formatRelativeShort } from "../model/relative";
import { countAdvisorySkipped } from "../adapters/wire";
import { useCapacitiesRefreshJob } from "./useCapacitiesRefreshJob";

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

export function Header() {
  const s = useAppState();
  const { controller, store } = useApp();
  // The healthy-state Refresh is a real Capacities refresh job: the pill
  // reports the job (and the snapshot reload that follows it) as refreshing
  // instead of briefly flashing through the in-flight read. Called before the
  // `s.inputs` guard so the hook order stays stable across renders.
  const job = useCapacitiesRefreshJob(s.inputs !== null, () => {
    void controller.refreshSources();
  });
  if (!s.inputs) return null;

  const screen = s.ui.screen;
  const commitFlow = s.ui.approvalOpen || s.commitPhase !== "idle";
  // Step 3 is derived from the approval/commit phase, never a screen of its
  // own. Connections is a header link, not a step.
  const selectedStep: StepId | null =
    screen === "setup"
      ? "setup"
      : screen === "connections"
        ? null
        : commitFlow
          ? "commit"
          : "plan";
  const focusStep: StepId = selectedStep ?? "plan";

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

  const onKeyDown = (e: KeyboardEvent) => {
    const index = STEPS.findIndex((step) => step.id === selectedStep);
    const base = index < 0 ? STEPS.findIndex((step) => step.id === focusStep) : index;
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
  const refreshing = s.refresh.phase === "loading" || job.running;
  // A click is in flight from the POST until the job's first status read: the
  // action stays disabled across that handoff so a double click cannot race a
  // second start (which the server would answer with a single-flight 409).
  const refreshBusy = refreshing || job.busy === "starting";
  const pillState = refreshing ? "refreshing" : health === "ok" ? "healthy" : "degraded";
  const pillBase = refreshing
    ? "Sources refreshing…"
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
                tabIndex={focusStep === step.id ? 0 : -1}
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
          class={`health-pill health-pill--${pillState}`}
          role="status"
          aria-live="polite"
          title={pillHint}
          aria-label={pillHint}
        >
          <span class="health-pill__text">{pillText}</span>
        </span>
        {pillState !== "refreshing" && (
          // One shared class for the pill action (healthy Refresh / degraded
          // Retry): the <=1100px compaction styles it by class and must keep
          // seeing it regardless of label. Healthy Refresh starts the real
          // Capacities refresh job; degraded Retry keeps the existing
          // snapshot-only retry path.
          <button
            class="btn health-pill__retry"
            onClick={() =>
              void (pillState === "healthy" ? job.start("refresh") : controller.refreshSources())
            }
            disabled={refreshBusy}
            aria-label={pillState === "healthy" ? "Refresh sources" : "Retry source refresh"}
          >
            {pillState === "healthy" ? "Refresh" : "Retry"}
          </button>
        )}
        <button
          class="btn header__connections"
          onClick={() =>
            store.dispatch({ type: "UI", patch: { screen: "connections", settingsPanel: null } })
          }
        >
          Connections
        </button>
      </div>
    </header>
  );
}
