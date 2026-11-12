/* SettingsHub — the body of the one Settings dialog: a status summary line, a
   sticky jump bar, and ONE scrolling page of five sections (Day, Habits, Plan
   contents, Sources, Advanced). Every section is always rendered, so nothing
   hides behind a tab; rare tools stay in closed folds. The jump bar scrolls to
   a section and records it as the destination (`ui.settingsPanel` plus, for
   Habits, `ui.settingsSection`). SettingsShell owns the dialog chrome and
   focus; the hub owns the sections.

   Save semantics are per panel and unchanged: Habits autosaves per field;
   Day, calendars, tags and Capacities rules keep their own explicit saves
   (some re-read sources, so they are never silent). Each wrapped component is
   mounted exactly once. Pre-hub ids (`capacities`, `tags`) arrive already
   mapped by the store. */

import { useEffect, useId, useState } from "preact/hooks";
import type { ComponentChildren } from "preact";
import { useApp, useAppState } from "./context";
import { DaySetupPanel } from "./DaySetupPanel";
import { CapacitiesRefreshPanel } from "./CapacitiesRefreshPanel";
import { CapacitiesUnknownReview } from "./CapacitiesUnknownReview";
import { CapacitiesRulesEditor } from "./CapacitiesRulesEditor";
import { CapacitiesSettingsPanel } from "./CapacitiesSettingsPanel";
import { TagExclusionSettingsPanel } from "./TagExclusionSettingsPanel";
import { CalendarSettingsPanel } from "./CalendarSettingsPanel";
import { CapacitiesPlanPreview } from "./CapacitiesPlanPreview";
import { CapacitiesStructureList } from "./CapacitiesStructureList";
import { HabitSettingsSection } from "./HabitSettingsSection";
import { usePlanData, type PlanData } from "./planPreview";
import { missingGenerationWarnings, sourceHealthBySource } from "../adapters/wire";
import type { SettingsPanel } from "../store/store";
import type { TagExclusionSettings } from "../model/types";
import "./settings-hub.css";

/** Jump-bar destinations, in page order. `habits` is a section of the Day
    destination, not a store panel of its own. */
export type HubEntryId = "day" | "habits" | "plan" | "freshness" | "repair";

export const HUB_ENTRIES: ReadonlyArray<{
  id: HubEntryId;
  panel: SettingsPanel;
  /** `ui.settingsSection` the destination carries, when it has one. */
  section: string | null;
  label: string;
}> = [
  { id: "day", panel: "day", section: null, label: "Day" },
  { id: "habits", panel: "day", section: "habits", label: "Habits" },
  { id: "plan", panel: "plan", section: null, label: "Plan contents" },
  { id: "freshness", panel: "freshness", section: null, label: "Sources" },
  { id: "repair", panel: "repair", section: null, label: "Advanced" },
];

/** `ui.settingsSection` value that opens the per-type admission section. */
export const ADMISSION_SECTION = "admission-per-type";

export const hubEntryId = (id: HubEntryId) => `settings-entry-${id}`;
/** Section element ids. `missing` is a fold inside Sources, so it shares that id. */
export const hubPanelId = (panel: SettingsPanel) =>
  `settings-panel-${panel === "missing" ? "freshness" : panel}`;
export const HABITS_SECTION_ID = "settings-section-habits";

function Fold({
  title,
  note,
  live,
  keepMounted = false,
  openState,
  children,
}: {
  title: string;
  note?: string;
  /** The owning entry is open. A fold in a hidden entry renders no control,
      so the dialog's Tab wrap (which ignores visibility) cannot land there. */
  live: boolean;
  /** Once opened, the children stay active (only hidden) when the fold closes
      or the entry is left, so unsaved edits inside are not discarded. */
  keepMounted?: boolean;
  /** Lets the owner drive the open state (a link can open the fold for the user). */
  openState?: readonly [boolean, (open: boolean) => void];
  /** Receives whether the fold is open; wrapped panels take it as `active`
      and render nothing (keeping their draft state) while the fold is closed. */
  children: (open: boolean) => ComponentChildren;
}) {
  // Closed by default; the wrapped components stay mounted so drafts survive.
  const [ownOpen, setOwnOpen] = useState(false);
  const [open, setOpen] = openState ?? [ownOpen, setOwnOpen];
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (live && open) setSeen(true);
  }, [live, open]);
  const bodyId = useId();
  return (
    <div class="settings-fold">
      {live && (
        <button
          type="button"
          class="settings-fold__toggle"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen(!open)}
        >
          <span>{title}</span>
          {note && <span class="settings-fold__note">{note}</span>}
        </button>
      )}
      <div class="settings-fold__body" id={bodyId} hidden={!(live && open)}>
        {children((live && open) || (keepMounted && seen))}
      </div>
    </div>
  );
}

function PlanBody({ here }: { here: boolean }) {
  const [savedTags, setSavedTags] = useState<TagExclusionSettings | null>(null);
  const data: PlanData = usePlanData(here, savedTags);
  const { store } = useApp();
  return (
    <>
      {here && (
      <p class="settings-hub__muted">
        Projects, events and other Capacities types are chosen one type at a time.{" "}
        <button
          type="button"
          class="settings-hub__link"
          onClick={() =>
            store.dispatch({
              type: "UI",
              patch: { settingsPanel: "repair", settingsSection: ADMISSION_SECTION },
            })
          }
        >
          How each Capacities type gets in
        </button>
      </p>
      )}
      <CapacitiesPlanPreview data={data} />
      <TagExclusionSettingsPanel active={here} onSettings={setSavedTags} />
      <CalendarSettingsPanel active={here} />
      <Fold title="Fine-tune by item type" note="rarely needed" live={here} keepMounted>
        {(foldOpen) => (
          <>
            {foldOpen && <CapacitiesStructureList data={data} />}
            <CapacitiesRulesEditor active={foldOpen} />
          </>
        )}
      </Fold>
    </>
  );
}

export function SettingsHub() {
  const s = useAppState();
  const { store } = useApp();
  const panel = s.ui.settingsPanel;
  const section = s.ui.settingsSection;

  // A jump records the destination and brings its section under the bar. The
  // pressed button keeps focus; programmatic navigation is focused by the shell.
  const go = (entry: (typeof HUB_ENTRIES)[number]) => {
    store.dispatch({
      type: "UI",
      patch: { settingsPanel: entry.panel, settingsSection: entry.section },
    });
    const id = entry.id === "habits" ? HABITS_SECTION_ID : hubPanelId(entry.panel);
    document.getElementById(id)?.scrollIntoView?.({ block: "start" });
  };
  const goPanel = (id: HubEntryId) => {
    const entry = HUB_ENTRIES.find((e) => e.id === id);
    if (entry) go(entry);
  };
  const isHere = (entry: (typeof HUB_ENTRIES)[number]) =>
    entry.id === "habits"
      ? panel === "day" && section === "habits"
      : entry.id === "freshness"
        ? panel === "freshness" || panel === "missing"
        : entry.id === "day"
          ? panel === "day" && section !== "habits"
          : panel === entry.id;

  // Technical details stay unmounted until the second click, then stay mounted.
  const [techShown, setTechShown] = useState(false);
  const [techOn, setTechOn] = useState(false);
  useEffect(() => {
    if (techOn) setTechShown(true);
  }, [techOn]);

  // The per-type admission section lives inside the Advanced fold: a link from
  // the plan view opens the fold and the technical details, then scrolls to it.
  const [repairOpen, setRepairOpen] = useState(false);
  const wantAdmission = panel === "repair" && section === ADMISSION_SECTION;
  useEffect(() => {
    if (!wantAdmission) return;
    setRepairOpen(true);
    setTechOn(true);
  }, [wantAdmission, panel, section]);
  useEffect(() => {
    if (!wantAdmission || !repairOpen || !techOn) return;
    const reveal = (): boolean => {
      const target = document.getElementById("capacities-sec-admission-per-type");
      if (!target) return false;
      target.scrollIntoView?.({ block: "start" });
      const heading = target.querySelector<HTMLElement>("h3");
      if (heading) {
        heading.tabIndex = -1;
        heading.focus({ preventScroll: true });
      }
      return true;
    };
    if (reveal()) return;
    // The panel loads its policy asynchronously; finish when the section lands.
    const observer = new MutationObserver(() => {
      if (reveal()) observer.disconnect();
    });
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [wantAdmission, repairOpen, techOn, section]);

  // The missing-items checklist is a fold in Sources; asking for it opens it.
  const [missingOpen, setMissingOpen] = useState(false);
  useEffect(() => {
    if (panel === "missing") setMissingOpen(true);
  }, [panel, section]);

  const health = sourceHealthBySource(s.inputs?.sourceWarnings ?? []);
  const healthy = s.inputs?.sourceHealth === "ok";
  const sources: ReadonlyArray<{ name: string; ok: boolean; count: number; noun: string }> = [
    { name: "Todoist", ok: health.todoist === "ok", count: s.inputs?.sourceCounts.todoist ?? 0, noun: "tasks" },
    { name: "Calendar", ok: health.calendar === "ok", count: s.inputs?.sourceCounts.calendar ?? 0, noun: "events" },
  ];

  const intakeState = s.inputs?.capacitiesIntake?.state;
  const syncText =
    intakeState === "ok"
      ? "Capacities is up to date."
      : intakeState === "degraded"
        ? "Capacities had a problem. Your plan keeps using the last read."
        : intakeState === "unavailable"
          ? "Capacities is unavailable right now."
          : "Capacities has not synced yet.";
  const missingCount = missingGenerationWarnings(s.inputs?.sourceWarnings ?? []).length;
  const missingNote =
    missingCount > 0 ? `${missingCount} mapped types are not yet in the latest refresh` : null;

  const sectionOf = (
    id: SettingsPanel,
    title: string,
    children: ComponentChildren,
    headingId = `settings-heading-${id}`,
  ) => (
    <section
      key={id}
      class="settings-hub__panel"
      id={hubPanelId(id)}
      aria-labelledby={headingId}
    >
      <h3 class="settings-hub__title" id={headingId}>
        {title}
      </h3>
      {children}
    </section>
  );

  return (
    <div class="settings-hub">
      <p class="settings-hub__summary">
        <span class={`settings-hub__dot${healthy ? "" : " settings-hub__dot--warn"}`} aria-hidden="true" />
        {healthy
          ? "Your sources are reading cleanly."
          : "A source had a problem. Your plan keeps using the last read."}
      </p>
      <nav class="settings-hub__jump" aria-label="Jump to a section">
        {HUB_ENTRIES.map((entry) => (
          <button
            key={entry.id}
            type="button"
            id={hubEntryId(entry.id)}
            class="settings-hub__jump-btn"
            aria-current={isHere(entry) ? "location" : undefined}
            data-entry={entry.id}
            onClick={() => go(entry)}
          >
            {entry.label}
            {entry.id === "freshness" && !healthy && (
              <span class="settings-hub__dot settings-hub__dot--warn" role="img" aria-label="needs attention" />
            )}
          </button>
        ))}
      </nav>

      {sectionOf("day", "Day", <DaySetupPanel active />)}

      <HabitSettingsSection active />

      {sectionOf("plan", "Plan contents", <PlanBody here />)}

      {sectionOf(
        "freshness",
        "Sources",
        <>
          <p class="settings-hub__muted" role="status">
            {syncText}
          </p>
          <CapacitiesRefreshPanel active extraNotes={missingNote ? [missingNote] : []} />
          <CapacitiesUnknownReview active />
          <Fold title="Todoist and Calendar" note="status of each" live>
            {() => (
              <ul class="settings-hub__rows" aria-label="Todoist and Calendar">
                {sources.map((src) => (
                  <li key={src.name}>
                    <span>{src.name}</span>
                    <span class="settings-hub__muted">
                      {src.ok ? "Up to date" : "Had a problem"} · {src.count} {src.noun}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Fold>
          <Fold
            title="Why is something missing?"
            note="a short checklist"
            live
            openState={[missingOpen, setMissingOpen]}
          >
            {(foldOpen) =>
              foldOpen && (
              <div class="settings-hub__missing">
                <h4 class="settings-hub__sub">Check these in order</h4>
                <ol>
                  <li>
                    <b>Is it hidden by a tag?</b> Look at the tags in{" "}
                    <button type="button" class="settings-hub__link" onClick={() => goPanel("plan")}>
                      Plan contents
                    </button>
                  </li>
                  <li>
                    <b>Did you hide it yourself?</b> Look under Tasks I hid in{" "}
                    <button type="button" class="settings-hub__link" onClick={() => goPanel("plan")}>
                      Plan contents
                    </button>{" "}
                    and choose Show again.
                  </li>
                  <li>
                    <b>Is it new in Capacities?</b> Press Refresh now in Capacities refresh above.
                  </li>
                  <li>
                    <b>Still missing?</b> Open{" "}
                    <button type="button" class="settings-hub__link" onClick={() => goPanel("repair")}>
                      Advanced
                    </button>
                  </li>
                </ol>
              </div>
              )
            }
          </Fold>
        </>,
      )}

      {sectionOf(
        "repair",
        "Advanced",
        <>
          <p class="settings-hub__muted">
            Repair connections. Only needed when something looks wrong. It is folded away so it cannot
            be changed by accident.
          </p>
          <Fold
            title="Repair tools"
            note="changing these can hide items from your plan"
            live
            openState={[repairOpen, setRepairOpen]}
          >
            {(foldOpen) => (
              <>
                {foldOpen && (
                  <button
                    type="button"
                    class="btn btn--sm"
                    aria-pressed={techOn}
                    onClick={() => setTechOn((on) => !on)}
                  >
                    {techOn ? "Hide" : "Show"} technical details
                  </button>
                )}
                {techShown && (
                  <div class="settings-hub__tech" hidden={!techOn}>
                    <CapacitiesSettingsPanel active={foldOpen && techOn} hosted />
                  </div>
                )}
              </>
            )}
          </Fold>
        </>,
      )}
    </div>
  );
}
