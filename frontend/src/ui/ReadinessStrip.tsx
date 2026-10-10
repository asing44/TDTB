/* ReadinessStrip — the one readiness presentation, mounted at the foot of the
   Rail. The Rail owns date, capacity evidence, chart, and keys; this section
   owns setup/captures, source refresh, billed ledger, theme, and refresh
   summary. Editing happens in the setup drawer (locked decision 10). */

import { useApp, useAppState } from "./context";
import type { Theme } from "../store/store";
import { summaryHasChanges, type RefreshSummary } from "../model/refresh";
import type { CapacitiesCoverage } from "../model/types";
import { capacitiesCoverageOf } from "../adapters/wire";
import { display12h } from "../model/time";

function clock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : display12h(`${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`);
}

export function refreshSummaryText(
  x: RefreshSummary,
  coverage?: CapacitiesCoverage | null,
): string {
  const parts: string[] = [];
  if (summaryHasChanges(x) || x.invalidated) {
    if (x.added.length) parts.push(`${x.added.length} added`);
    if (x.removed.length) parts.push(`${x.removed.length} removed`);
    if (x.changed.length) parts.push(`${x.changed.length} changed`);
    if (x.overridesRetained.length)
      parts.push(`override retained: ${x.overridesRetained.join(", ")}`);
    if (x.overridesDropped.length)
      parts.push(`override dropped: ${x.overridesDropped.join(", ")}`);
    if (x.invalidated) parts.push("staged plan invalidated");
  }
  if (parts.length === 0) parts.push("no changes");
  // A completed refresh is not complete coverage: the summary must not read
  // "no changes" while rows are still deferred (the visible warning beside
  // Sources carries the verbatim adapter text).
  const deferred = coverage?.deferred ?? 0;
  if (deferred > 0) parts.push(`Capacities partial: ${deferred} deferred`);
  return parts.join(" · ");
}

export function ReadinessStrip() {
  const s = useAppState();
  const { controller, store } = useApp();
  if (!s.inputs) return null;

  const captures = s.daySetup.captures;
  const captureCount = [captures.intention, captures.forMeegy, captures.stoic].filter(
    (c) => c.trim() !== "",
  ).length;
  const health = s.inputs.sourceHealth;
  const ledger = s.ledger;
  const refresh = s.refresh;
  const coverage = capacitiesCoverageOf(s.inputs.sourceWarnings, s.inputs.capacitiesIntake);
  const cycleTheme = () => {
    const next: Theme =
      s.theme === "system" ? "light" : s.theme === "light" ? "dark" : "system";
    store.dispatch({ type: "THEME_SET", theme: next });
  };

  return (
    <section class="rail__chips" aria-label="Readiness">
      {s.inputs.daySemantics.selectedPreset && (
        <span class="chip">
          Preset {s.inputs.daySemantics.selectedPreset.name}
          {s.daySetup.dayPreset ? " · today" : " · automatic"}
        </span>
      )}
      <button
        class={`chip chip--btn ${
          s.daySetup.confirmed ? "chip--ok" : "chip--warn chip--setup-pending"
        }`}
        onClick={() => store.dispatch({ type: "UI", patch: { settingsPanel: "day" } })}
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
        onClick={() =>
          store.dispatch({
            type: "UI",
            patch: { settingsPanel: "day", settingsSection: "captures" },
          })
        }
        aria-label="Open captures in day setup"
      >
        Captures {captureCount}/3
      </button>
      {/* One settings entry replaces the two policy chips: it opens the
          shared shell on Day setup, and the shell's tabs reach Capacities
          and Tag exclusions without mounting a second drawer. */}
      <button
        class="chip chip--btn"
        onClick={() => store.dispatch({ type: "UI", patch: { settingsPanel: "day" } })}
        aria-label="Open settings"
      >
        Settings
      </button>
      <button
        class={`chip chip--btn ${
          refresh.error
            ? "chip--err"
            : health === "ok"
              ? "chip--ok"
              : health === "degraded"
                ? "chip--warn"
                : "chip--err"
        }`}
        onClick={() => void controller.refreshSources()}
        disabled={refresh.phase === "loading"}
        aria-busy={refresh.phase === "loading"}
        aria-label={
          refresh.lastRefreshed
            ? `Refresh sources (last refreshed ${clock(refresh.lastRefreshed)})`
            : "Refresh sources"
        }
      >
        {refresh.phase === "loading"
          ? "Sources ⟳ refreshing…"
          : `Sources ${health === "ok" ? "✓" : health} ↻`}
      </button>
      {/* A partial source read is a planning-surface fact, not a popover
          secret: the adapter's verbatim warning stays visible beside Sources
          on initial load and after every refresh. */}
      {coverage && (
        <div class="rail__partial" role="status" aria-label="Capacities coverage partial">
          {coverage.warnings.map((warning) => (
            <p class="rail__partial-warning" key={warning}>
              {warning}
            </p>
          ))}
        </div>
      )}
      {ledger && (
        <span class={`chip ${ledger.remaining > 0 ? "" : "chip--warn"}`}>
          Calls {ledger.remaining}/{ledger.cap}
        </span>
      )}
      <button class="chip chip--btn" onClick={cycleTheme} aria-label="Cycle theme">
        Theme: {s.theme === "system" ? "Auto" : s.theme === "light" ? "Light" : "Dark"}
      </button>
      {(refresh.error || refresh.lastRefreshed) && (
        <span class="rail__refresh" role="status">
          {refresh.error
            ? `Refresh failed: ${refresh.error} — showing last good data`
            : refresh.summary
              ? `Refreshed ${clock(refresh.lastRefreshed as string)} · ${refreshSummaryText(refresh.summary, refresh.coverage)}`
              : `Refreshed ${clock(refresh.lastRefreshed as string)}`}
        </span>
      )}
    </section>
  );
}
