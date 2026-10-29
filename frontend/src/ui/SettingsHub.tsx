/* SettingsHub — the body of the one Settings dialog: a status summary line, a compact
   grid of five plain-language entry buttons that always stays in view, and
   the one open entry's panel beneath the grid (buttons with
   aria-expanded/aria-controls). SettingsShell
   owns the dialog chrome and focus; the hub owns the entries.

   Every panel body stays mounted for the life of the shell so unsaved drafts
   survive switching entries (panels render nothing while inactive; closed
   folds only hide). Each wrapped component is mounted exactly once. Pre-hub
   ids (`capacities`, `tags`) arrive already mapped by the store. */

import { useEffect, useId, useState } from "preact/hooks";
import type { ComponentChildren } from "preact";
import { useApp, useAppState } from "./context";
import { DaySetupPanel } from "./DaySetupPanel";
import { CapacitiesRefreshPanel } from "./CapacitiesRefreshPanel";
import { CapacitiesUnknownReview } from "./CapacitiesUnknownReview";
import { CapacitiesRulesEditor } from "./CapacitiesRulesEditor";
import { CapacitiesSettingsPanel } from "./CapacitiesSettingsPanel";
import { TagExclusionSettingsPanel } from "./TagExclusionSettingsPanel";
import { CapacitiesPlanPreview } from "./CapacitiesPlanPreview";
import { CapacitiesStructureList } from "./CapacitiesStructureList";
import { usePlanData, type PlanData } from "./planPreview";
import { missingGenerationWarnings, sourceHealthBySource } from "../adapters/wire";
import type { SettingsPanel } from "../store/store";
import type { TagExclusionSettings } from "../model/types";
import "./settings-hub.css";

export const HUB_ENTRIES: ReadonlyArray<{
  id: SettingsPanel;
  label: string;
  hint: string;
}> = [
  { id: "freshness", label: "Is my data up to date?", hint: "Todoist, calendar and Capacities, plus Refresh now" },
  { id: "plan", label: "What shows up in my plan?", hint: "Which items appear, and tags you hide" },
  { id: "missing", label: "Why is something missing?", hint: "A short checklist" },
  { id: "day", label: "Change my day setup", hint: "Hours, sessions, anchored blocks, captures" },
  { id: "repair", label: "Repair connections", hint: "Only if something looks wrong" },
];

export const hubEntryId = (panel: SettingsPanel) => `settings-entry-${panel}`;
export const hubPanelId = (panel: SettingsPanel) => `settings-panel-${panel}`;

function Fold({
  title,
  note,
  live,
  keepMounted = false,
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
  /** Receives whether the fold is open; wrapped panels take it as `active`
      and render nothing (keeping their draft state) while the fold is closed. */
  children: (open: boolean) => ComponentChildren;
}) {
  // Closed by default; the wrapped components stay mounted so drafts survive.
  const [open, setOpen] = useState(false);
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
          onClick={() => setOpen((o) => !o)}
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
  return (
    <>
      <CapacitiesPlanPreview data={data} />
      <TagExclusionSettingsPanel active={here} onSettings={setSavedTags} />
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
  const open = (id: SettingsPanel) => store.dispatch({ type: "UI", patch: { settingsPanel: id } });
  // The open entry can be folded away without leaving Settings; a new
  // destination (panel or section) always reopens it.
  const [collapsed, setCollapsed] = useState(false);
  const section = s.ui.settingsSection;
  useEffect(() => setCollapsed(false), [panel, section]);
  const isOpen = (id: SettingsPanel) => panel === id && !collapsed;

  // Technical details stay unmounted until the second click, then stay mounted.
  const [techShown, setTechShown] = useState(false);
  const [techOn, setTechOn] = useState(false);
  useEffect(() => {
    if (techOn) setTechShown(true);
  }, [techOn]);

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

  const body = (id: SettingsPanel) => {
    const here = isOpen(id);
    switch (id) {
      case "freshness":
        return (
          <>
            <p class="settings-hub__muted" role="status">
              {syncText}
              {missingNote && ` · ${missingNote}`}
            </p>
            <CapacitiesRefreshPanel active={here} />
            <CapacitiesUnknownReview active={here} />
            <Fold title="Todoist and Calendar" note="status of each" live={here}>
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
          </>
        );
      case "plan":
        return (
          <PlanBody here={here} />
        );
      case "missing":
        return here ? (
          <div class="settings-hub__missing">
            <h3 class="settings-hub__sub">Check these in order</h3>
            <ol>
              <li>
                <b>Is it hidden by a tag?</b> Look at the tags in{" "}
                <button type="button" class="settings-hub__link" onClick={() => open("plan")}>
                  What shows up in my plan?
                </button>
              </li>
              <li>
                <b>Did you hide it yourself?</b> Look under Tasks I hid in{" "}
                <button type="button" class="settings-hub__link" onClick={() => open("plan")}>
                  What shows up in my plan?
                </button>{" "}
                and choose Show again.
              </li>
              <li>
                <b>Is it new in Capacities?</b> Press Refresh now in{" "}
                <button type="button" class="settings-hub__link" onClick={() => open("freshness")}>
                  Is my data up to date?
                </button>
              </li>
              <li>
                <b>Still missing?</b> Open{" "}
                <button type="button" class="settings-hub__link" onClick={() => open("repair")}>
                  Repair connections
                </button>
              </li>
            </ol>
          </div>
        ) : null;
      case "day":
        return <DaySetupPanel active={here} />;
      case "repair":
        return (
          <>
            {here && <h3 class="settings-hub__sub">Repair connections</h3>}
            <p class="settings-hub__muted">
              Only needed when something looks wrong. It is folded away so it cannot be changed by accident.
            </p>
            <Fold title="Repair tools" note="changing these can hide items from your plan" live={here}>
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
          </>
        );
    }
  };

  return (
    <div class="settings-hub">
      <p class="settings-hub__summary">
        <span class={`settings-hub__dot${healthy ? "" : " settings-hub__dot--warn"}`} aria-hidden="true" />
        {healthy
          ? "Your sources are reading cleanly."
          : "A source had a problem. Your plan keeps using the last read."}
      </p>
      <ul class="settings-hub__entries">
        {HUB_ENTRIES.map((entry) => {
          const expanded = isOpen(entry.id);
          return (
            <li key={entry.id} class="settings-hub__entry">
              <button
                type="button"
                id={hubEntryId(entry.id)}
                class="settings-hub__toggle"
                aria-expanded={expanded}
                aria-controls={hubPanelId(entry.id)}
                onClick={() => (panel === entry.id ? setCollapsed((c) => !c) : open(entry.id))}
                data-entry={entry.id}
              >
                <span>
                  <b>{entry.label}</b>
                  <span class="settings-hub__muted">{entry.hint}</span>
                </span>
                <span aria-hidden="true">{expanded ? "▾" : "›"}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {HUB_ENTRIES.map((entry) => (
        <div
          key={entry.id}
          class="settings-hub__panel"
          id={hubPanelId(entry.id)}
          role="region"
          aria-labelledby={hubEntryId(entry.id)}
          hidden={!isOpen(entry.id)}
        >
          {body(entry.id)}
        </div>
      ))}
    </div>
  );
}
