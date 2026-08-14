/* ReadinessStrip — date, setup/captures completion, source refresh, billed
   ledger, theme toggle. Completion state only; editing happens in the setup
   drawer (locked decision 10). The Sources chip is the explicit refresh
   control (locked decision 20): loading/error/last-refreshed feedback plus
   a compact added/removed/changed/override summary. */

import { useApp, useAppState } from "./context";
import type { Theme } from "../store/store";
import { summaryHasChanges, type RefreshSummary } from "../model/refresh";

function themeLabel(t: Theme): string {
  return t === "system" ? "Auto" : t === "light" ? "Light" : "Dark";
}

function clock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function refreshSummaryText(x: RefreshSummary): string {
  if (!summaryHasChanges(x) && !x.invalidated) return "no changes";
  const parts: string[] = [];
  if (x.added.length) parts.push(`${x.added.length} added`);
  if (x.removed.length) parts.push(`${x.removed.length} removed`);
  if (x.changed.length) parts.push(`${x.changed.length} changed`);
  if (x.overridesRetained.length)
    parts.push(`override retained: ${x.overridesRetained.join(", ")}`);
  if (x.overridesDropped.length)
    parts.push(`override dropped: ${x.overridesDropped.join(", ")}`);
  if (x.invalidated) parts.push("staged plan invalidated");
  return parts.join(" · ");
}

export function ReadinessStrip() {
  const s = useAppState();
  const { store } = useApp();
  if (!s.inputs) return null;

  const captures = s.daySetup.captures;
  const captureCount = [captures.intention, captures.forMeegy, captures.stoic].filter(
    (c) => c.trim() !== "",
  ).length;
  const health = s.inputs.sourceHealth;
  const ledger = s.ledger;
  const refresh = s.refresh;
  const controller = useApp().controller;

  const cycleTheme = () => {
    const next: Theme =
      s.theme === "system" ? "light" : s.theme === "light" ? "dark" : "system";
    store.dispatch({ type: "THEME_SET", theme: next });
  };

  return (
    <header class="strip">
      <span class="strip__date">{s.inputs.validDate}</span>
      <span class="strip__spacer" />
      {s.inputs.daySemantics.selectedPreset && (
        <span class="chip">
          Preset {s.inputs.daySemantics.selectedPreset.name}
          {s.daySetup.dayPreset ? " · today" : " · automatic"}
        </span>
      )}
      <button
        class={`chip chip--btn ${s.daySetup.confirmed ? "chip--ok" : ""}`}
        onClick={() => store.dispatch({ type: "UI", patch: { setupOpen: true } })}
        aria-label="Open day setup"
      >
        {s.daySetup.confirmed ? "Setup ✓" : "Setup pending"}
      </button>
      <button
        class={`chip chip--btn ${captureCount === 3 ? "chip--ok" : ""}`}
        onClick={() => store.dispatch({ type: "UI", patch: { setupOpen: true } })}
        aria-label="Open captures in day setup"
      >
        Captures {captureCount}/3
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
      {ledger && (
        <span class={`chip ${ledger.remaining > 0 ? "" : "chip--warn"}`}>
          Budget {ledger.remaining}/{ledger.cap}
        </span>
      )}
      <button class="chip chip--btn" onClick={cycleTheme} aria-label="Cycle theme">
        Theme: {themeLabel(s.theme)}
      </button>
      {(refresh.error || refresh.lastRefreshed) && (
        <span class="strip__refresh" role="status">
          {refresh.error
            ? `Refresh failed: ${refresh.error} — showing last good data`
            : refresh.summary
              ? `Refreshed ${clock(refresh.lastRefreshed as string)} · ${refreshSummaryText(refresh.summary)}`
              : `Refreshed ${clock(refresh.lastRefreshed as string)}`}
        </span>
      )}
    </header>
  );
}
