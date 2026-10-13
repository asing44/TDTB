/* ReadinessStrip — the one readiness presentation, mounted at the foot of the
   Rail. The Rail owns date, capacity evidence, chart, and keys; this section
   owns setup/captures, source refresh, billed ledger, theme, and refresh
   summary. Editing happens in the setup drawer (locked decision 10).

   The Sources chip keeps its refresh-on-click behavior. A separate small
   details affordance (a caret beside the chip) opens a compact popover with
   the freshness facts the chip line cannot carry: live Todoist/Calendar
   counts, the cached Capacities generation and coverage, per-structure check
   times, any skipped objects, and the plain next action. */

import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import type { Theme } from "../store/store";
import { summaryHasChanges, type RefreshSummary } from "../model/refresh";
import type { CapacitiesCoverage, CapacitiesIntakeSkipped } from "../model/types";
import { capacitiesCoverageOf } from "../adapters/wire";
import { display12h } from "../model/time";
import { formatRelative } from "../model/relative";
import { computePopoverPlacement, type PopoverPlacement } from "./popoverPlacement";

const PANEL_ID = "rail-sources-details";
const TEN_MINUTES_MS = 10 * 60_000;
const MAX_SKIPPED = 8;
/** Keys longer than this are opaque structure ids; show a short form. */
const SHORT_ID_AFTER = 24;

function clockFromDate(d: Date): string {
  return display12h(
    `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`,
  );
}

function clock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : clockFromDate(d);
}

/** Local "Oct 13, 2:45 PM" for a server epoch-seconds timestamp. */
function dateTimeAt(ms: number): string {
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "unknown";
  const date = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return `${date} ${clockFromDate(d)}`;
}

/** A short structure key is a name; a long one is an opaque id shown short. */
function structureLabel(key: string): string {
  return key.length > SHORT_ID_AFTER ? key.slice(0, 8) : key;
}

/** Short "Oct 10" for a server ISO timestamp, or null when unparseable.
    Capacities timestamps are UTC (trailing ``Z``) but the Capacities app shows
    edit times in the viewer's local time, so the date is read locally: an edit
    at 8:33 PM on Oct 9 must read "Oct 9", not the UTC "Oct 10". */
function shortEditedDate(iso: string): string | null {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

/** The compact find-it clue line for one skipped row, or null when empty.
    Parts are joined with " · ": labels, "edited {date}", then the hint in
    quotes. Empty parts are omitted and an unreadable clue never throws. */
function skippedClues(row: CapacitiesIntakeSkipped): string | null {
  const parts: string[] = [];
  if (row.labels && row.labels.length > 0) parts.push(row.labels.join(" · "));
  const edited = row.updatedAt ? shortEditedDate(row.updatedAt) : null;
  if (edited) parts.push(`edited ${edited}`);
  if (typeof row.hint === "string" && row.hint !== "") {
    parts.push(`"${row.hint}"`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
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
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [placement, setPlacement] = useState<PopoverPlacement | null>(null);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const detailsRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const naturalSizeRef = useRef<{ width: number; height: number } | null>(null);

  useEffect(() => {
    if (!detailsOpen) return;
    panelRef.current?.focus();
    const onPointer = (e: MouseEvent | TouchEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setDetailsOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setDetailsOpen(false);
        detailsRef.current?.focus();
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
  }, [detailsOpen]);

  // Place the panel from the trigger and panel rects: prefer upward, else
  // downward, always clamped inside the viewport. Recomputed on open and on
  // window resize/scroll while open. The natural panel size is measured once
  // (before a max-height clamp is applied) so later recomputes stay stable.
  useLayoutEffect(() => {
    if (!detailsOpen) {
      naturalSizeRef.current = null;
      setPlacement(null);
      return;
    }
    const place = () => {
      const trigger = detailsRef.current?.getBoundingClientRect();
      const panel = panelRef.current;
      if (!trigger || !panel) return;
      if (!naturalSizeRef.current) {
        const rect = panel.getBoundingClientRect();
        naturalSizeRef.current = { width: rect.width, height: rect.height };
      }
      setPlacement(
        computePopoverPlacement(
          trigger,
          { width: window.innerWidth, height: window.innerHeight },
          naturalSizeRef.current,
        ),
      );
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [detailsOpen]);

  if (!s.inputs) return null;

  // No timer or poll: the age is computed at render, and opening the popover
  // re-renders so it is fresh when read.
  const now = new Date();
  const captures = s.daySetup.captures;
  const captureCount = [captures.intention, captures.forMeegy, captures.stoic].filter(
    (c) => c.trim() !== "",
  ).length;
  const health = s.inputs.sourceHealth;
  const ledger = s.ledger;
  const refresh = s.refresh;
  const coverage = capacitiesCoverageOf(s.inputs.sourceWarnings, s.inputs.capacitiesIntake);
  const intake = s.inputs.capacitiesIntake;
  const refreshedAt = refresh.lastRefreshed ? new Date(refresh.lastRefreshed) : null;
  const refreshedDate =
    refreshedAt && !Number.isNaN(refreshedAt.getTime()) ? refreshedAt : null;
  const ageSuffix =
    refreshedDate !== null && now.getTime() - refreshedDate.getTime() > TEN_MINUTES_MS
      ? ` (${formatRelative(now, refreshedDate)})`
      : "";
  const cycleTheme = () => {
    const next: Theme =
      s.theme === "system" ? "light" : s.theme === "light" ? "dark" : "system";
    store.dispatch({ type: "THEME_SET", theme: next });
  };
  const toggleDetails = () => setDetailsOpen(!detailsOpen);

  const liveLine = refreshedDate
    ? `Refreshed ${formatRelative(now, refreshedDate)} (${clockFromDate(refreshedDate)})`
    : "Not refreshed in this session";
  const sourceCountsLine = `${s.inputs.sourceCounts.todoist} tasks · ${s.inputs.sourceCounts.calendar} events`;
  const intakeCounts = intake
    ? `${intake.coverage.evaluated} of ${intake.coverage.members} cached objects checked · ` +
      `${intake.coverage.malformed} skipped · ${intake.coverage.unreadable} unreadable`
    : null;
  const generationLine = intake
    ? intake.generation !== null
      ? `Cached generation ${intake.generation}, installed ${
          intake.installedAt !== null
            ? `${formatRelative(now, new Date(intake.installedAt * 1000))} (${dateTimeAt(intake.installedAt * 1000)})`
            : "unknown"
        }`
      : "No cached generation reported."
    : "Capacities intake is not reported by this server.";
  const typeEntries = intake ? Object.entries(intake.typeCheckTimes) : [];
  const checkedLine =
    typeEntries.length > 0
      ? `Structures last checked: ${typeEntries
          .map(
            ([key, seconds]) =>
              `${structureLabel(key)} ${formatRelative(now, new Date(seconds * 1000))}`,
          )
          .join(" · ")}`
      : null;
  const skipped = intake?.skipped;
  const shownSkipped = skipped?.slice(0, MAX_SKIPPED) ?? [];
  const skippedOverflow = (skipped?.length ?? 0) - shownSkipped.length;
  const deferred = coverage?.deferred ?? 0;
  const action =
    shownSkipped.length > 0
      ? "These do not block planning but are left out of the plan. Fix or delete them in Capacities, then Refresh."
      : deferred > 0
        ? "Refresh again to evaluate the rest."
        : "Nothing needs attention.";

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
      <span class="rail__sources" ref={wrapRef}>
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
            refreshedDate
              ? `Refresh sources (last refreshed ${refreshedDate.toLocaleDateString(undefined, { month: "short", day: "numeric" })}, ${clockFromDate(refreshedDate)} — ${formatRelative(now, refreshedDate)})`
              : "Refresh sources"
          }
        >
          {refresh.phase === "loading"
            ? "Sources ⟳ refreshing…"
            : `Sources ${health === "ok" ? "✓" : health} ↻`}
        </button>
        <button
          ref={detailsRef}
          class="chip chip--btn rail__sources-toggle"
          aria-expanded={detailsOpen}
          aria-controls={PANEL_ID}
          aria-label="Source details"
          title="Source details"
          onClick={toggleDetails}
        >
          <span aria-hidden="true">{detailsOpen ? "▴" : "▾"}</span>
        </button>
        {detailsOpen && (
          <div
            class="rail__sources-pop"
            id={PANEL_ID}
            role="dialog"
            aria-label="Source freshness details"
            tabIndex={-1}
            ref={panelRef}
            style={
              placement
                ? {
                    top: `${placement.top}px`,
                    left: `${placement.left}px`,
                    maxHeight: `${placement.maxHeight}px`,
                  }
                : { visibility: "hidden" }
            }
          >
            <div class="rail__sources-src">
              <div class="rail__sources-src-head">
                <span class="rail__sources-src-name">Todoist &amp; Calendar</span>
                <span class="rail__sources-src-meta">Fetched live on each refresh</span>
              </div>
              <div class="rail__sources-src-line">{liveLine}</div>
              <div class="rail__sources-src-counts">{sourceCountsLine}</div>
            </div>

            <div class="rail__sources-src">
              <div class="rail__sources-src-head">
                <span class="rail__sources-src-name">Capacities</span>
                <span class="rail__sources-src-meta">Cached snapshot</span>
              </div>
              <div class="rail__sources-src-line">{generationLine}</div>
              <div class="rail__sources-src-counts">
                Read from cache — Refresh re-reads Capacities (billed).
              </div>
              {intakeCounts && <div class="rail__sources-src-counts">{intakeCounts}</div>}
              {checkedLine && <div class="rail__sources-src-counts">{checkedLine}</div>}
            </div>

            {skipped === undefined ? (
              coverage && coverage.warnings.length > 0 ? (
                <div class="rail__sources-skipped">
                  {coverage.warnings.map((warning) => (
                    <p class="rail__sources-src-line" key={warning}>
                      {warning}
                    </p>
                  ))}
                </div>
              ) : null
            ) : (
              <div class="rail__sources-skipped">
                <div class="rail__sources-skipped-head">Skipped objects</div>
                {shownSkipped.length === 0 ? (
                  <div class="rail__sources-src-counts">No skipped objects.</div>
                ) : (
                  <ul class="rail__sources-skipped-list">
                    {shownSkipped.map((row) => {
                      const clues = skippedClues(row);
                      return (
                        <li
                          class="rail__sources-skipped-item"
                          key={row.identity}
                          title={row.identity.slice(0, 8)}
                        >
                          <span class="rail__sources-skipped-title">
                            {row.title && row.title.trim() !== "" ? row.title : "Untitled"}
                          </span>
                          <span class="rail__sources-skipped-reason"> — {row.reason}</span>
                          {row.structureName && (
                            <span class="rail__sources-skipped-meta">{row.structureName}</span>
                          )}
                          {clues && (
                            <span class="rail__sources-skipped-clues">{clues}</span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
                {skippedOverflow > 0 && (
                  <div class="rail__sources-src-counts">and {skippedOverflow} more</div>
                )}
              </div>
            )}

            <p class="rail__sources-action">{action}</p>
          </div>
        )}
      </span>
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
              ? `Refreshed ${clock(refresh.lastRefreshed as string)}${ageSuffix} · ${refreshSummaryText(refresh.summary, refresh.coverage)}`
              : `Refreshed ${clock(refresh.lastRefreshed as string)}${ageSuffix}`}
        </span>
      )}
    </section>
  );
}
