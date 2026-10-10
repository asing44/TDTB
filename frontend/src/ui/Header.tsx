/* Header — U7 page frame (KTD11). The retired left rail and floating tab pill
   are replaced by one persistent header: the date and workday-frame line, a
   three-step stepper bound to the existing `ui.screen` state, a source-health
   pill with Retry wired to the existing refresh lifecycle, and a Connections
   link that opens the existing connections screen.

   The stepper preserves ScreenNav's keyboard behavior (roving tabindex,
   Arrow/Home/End, visible focus, ARIA roles). Step 3 (Commit) maps onto the
   existing approval/commit flow — it is never a separate screen. */

import { Fragment } from "preact";
import { useApp, useAppState } from "./context";
import { display12h } from "../model/time";

const DAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function prettyDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return `${DAY[d.getDay()]} ${d.getDate()} ${MON[d.getMonth()]}`;
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
  const refreshing = s.refresh.phase === "loading";
  const pillState = refreshing ? "refreshing" : health === "ok" ? "healthy" : "degraded";
  const pillText = refreshing
    ? "Refreshing sources…"
    : health === "ok"
      ? "Sources healthy"
      : "Sources degraded · commit blocked";

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
        class="stepper"
        role="tablist"
        aria-label="Planning steps"
        onKeyDown={(e) => onKeyDown(e as unknown as KeyboardEvent)}
      >
        {STEPS.map((step, i) => {
          const selected = selectedStep === step.id;
          return (
            <Fragment key={step.id}>
              {i > 0 && (
                <span class="stepper__sep" aria-hidden="true">
                  —
                </span>
              )}
              <button
                type="button"
                role="tab"
                id={stepId(step.id)}
                class={`stepper__step${selected ? " stepper__step--active" : ""}${
                  done[step.id] ? " stepper__step--done" : ""
                }`}
                aria-selected={selected}
                tabIndex={focusStep === step.id ? 0 : -1}
                onClick={(e) => activate(step.id, e.currentTarget as HTMLElement)}
              >
                <span class="stepper__mark" aria-hidden="true">
                  {done[step.id] ? "✓" : i + 1}
                </span>
                <span class="stepper__label">{step.label}</span>
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
        >
          {pillText}
        </span>
        {pillState === "degraded" && (
          <button
            class="health-pill__retry"
            onClick={() => void controller.refreshSources()}
            disabled={refreshing}
            aria-label="Retry source refresh"
          >
            Retry
          </button>
        )}
        <button
          class="header__connections"
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
