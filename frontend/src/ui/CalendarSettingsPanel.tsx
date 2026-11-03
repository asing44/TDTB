/* CalendarSettingsPanel — the per-calendar on/off defaults. Each switch is
   that calendar's default at the start of each planning day. Identity is the
   stable calendar id; titles are display-only. A calendar with no saved entry
   is on and carries a "New" badge; TDTB's own write calendars are locked on.
   Saved entries whose calendar is not in the live inventory stay listed so a
   full-replacement save never drops them silently.

   Mounted by the Settings hub for the life of the shell, so unsaved drafts
   survive panel switches; it renders nothing while inactive. A 409 or any
   other save failure keeps the draft and requires a reload before saving. */

import { useEffect, useMemo, useState } from "preact/hooks";
import { useApp } from "./context";
import type { CalendarSettings, CalendarSettingsDraft } from "../model/types";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function draftOf(settings: CalendarSettings): CalendarSettingsDraft {
  const saved = new Map(settings.calendars.map((entry) => [entry.id, entry]));
  const inventoryIds = new Set(settings.inventory.map((row) => row.id));
  const calendars = settings.inventory.map((row) => ({
    id: row.id,
    title: row.title,
    enabled: row.locked !== null ? true : (saved.get(row.id)?.enabled ?? true),
  }));
  for (const entry of settings.calendars) {
    if (!inventoryIds.has(entry.id)) calendars.push({ ...entry });
  }
  return { expectedRevision: settings.revision, calendars };
}

interface Row {
  id: string;
  title: string;
  source: string | null;
  locked: boolean;
  isNew: boolean;
  missing: boolean;
}

export function CalendarSettingsPanel({ active }: { active: boolean }) {
  const { controller } = useApp();
  const [settings, setSettings] = useState<CalendarSettings | null>(null);
  const [draft, setDraft] = useState<CalendarSettingsDraft | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "saving" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadRequired, setReloadRequired] = useState(false);

  const loadSettings = async () => {
    setPhase("loading");
    setError(null);
    setNotice(null);
    setReloadRequired(false);
    try {
      const loaded = await controller.loadCalendarSettings();
      setSettings(loaded);
      setDraft(draftOf(loaded));
      setPhase("ready");
    } catch (e) {
      setSettings(null);
      setDraft(null);
      setPhase("error");
      setError(messageOf(e));
    }
  };

  useEffect(() => {
    void loadSettings();
  }, [controller]);

  const rows = useMemo<Row[]>(() => {
    if (!settings || !draft) return [];
    const savedIds = new Set(settings.calendars.map((entry) => entry.id));
    const byId = new Map(settings.inventory.map((row) => [row.id, row]));
    return draft.calendars.map((entry) => {
      const live = byId.get(entry.id);
      return {
        id: entry.id,
        title: live?.title || entry.title || "Untitled calendar",
        source: live?.source || null,
        locked: live?.locked != null,
        isNew: live !== undefined && live.locked === null && !savedIds.has(entry.id),
        missing: live === undefined,
      };
    });
  }, [settings, draft]);

  if (!active) return null;

  const enabledOf = (id: string) => draft?.calendars.find((entry) => entry.id === id)?.enabled ?? true;

  const toggle = (id: string) =>
    setDraft((current) =>
      current
        ? {
            ...current,
            calendars: current.calendars.map((entry) =>
              entry.id === id ? { ...entry, enabled: !entry.enabled } : entry,
            ),
          }
        : current,
    );

  const save = async () => {
    if (!draft || phase === "saving" || reloadRequired) return;
    setPhase("saving");
    setError(null);
    setNotice(null);
    try {
      const { settings: saved, refreshError } = await controller.saveCalendarSettings(draft);
      setSettings(saved);
      setDraft(draftOf(saved));
      setPhase("ready");
      setNotice(
        refreshError
          ? "Saved; planning refresh failed — calendars shown may be stale."
          : "Saved. Sources were re-read.",
      );
    } catch (e) {
      setPhase("error");
      setError(messageOf(e));
      setReloadRequired(true);
    }
  };

  return (
    <div class="settings-panel settings-panel--calendars">
      {phase === "loading" && (
        <p class="calendar-settings__state" aria-live="polite">
          Loading calendars…
        </p>
      )}

      {phase === "error" && settings === null && (
        <section class="calendar-settings__error" role="alert">
          <strong>Calendars could not be loaded</strong>
          <span>{error}</span>
          <button class="btn" onClick={() => void loadSettings()}>
            Reload settings
          </button>
        </section>
      )}

      {settings !== null && draft && (
        <>
          <section
            class="setup-section calendar-settings__section"
            aria-labelledby="calendar-settings-sec-list"
          >
            <div class="setup-section__head">
              <h3 id="calendar-settings-sec-list">Calendars</h3>
              <span class="calendar-settings__revision">
                {settings.persisted ? `Saved (version ${settings.revision})` : "Using defaults"}
              </span>
            </div>
            <div class="setup-section__body">
              <p class="calendar-settings__intro">
                Each switch is that calendar's default at the start of each planning day.
                Calendars that are off are left out of your plan.
              </p>
              {settings.inventoryStatus === "unavailable" && (
                <p class="calendar-settings__warning" role="status">
                  Calendar list unavailable right now — saved choices are still listed.
                </p>
              )}
              {settings.warnings.map((warning) => (
                <p class="calendar-settings__warning" role="status" key={warning}>
                  {warning}
                </p>
              ))}
              {rows.length === 0 ? (
                <p class="calendar-settings__empty">No calendars are available.</p>
              ) : (
                <ul class="calendar-list" aria-label="Calendars">
                  {rows.map((row) => {
                    const on = row.locked ? true : enabledOf(row.id);
                    return (
                      <li class="calendar-row" key={row.id} data-calendar-id={row.id}>
                        <button
                          type="button"
                          role="switch"
                          class="anchor-popover__switch all-blocks__switch"
                          aria-checked={on}
                          aria-label={`Include ${row.title} by default`}
                          disabled={row.locked || phase === "saving"}
                          onClick={() => toggle(row.id)}
                        />
                        <span class="calendar-row__name">
                          <strong>{row.title}</strong>
                          {row.source && <small>{row.source}</small>}
                        </span>
                        {row.isNew && <span class="calendar-row__badge">New</span>}
                        {row.locked && (
                          <span class="calendar-row__reason">
                            Always on — TDTB writes your plan to this calendar
                          </span>
                        )}
                        {row.missing && (
                          <span class="calendar-row__reason">Not in your calendars right now</span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
              <p class="calendar-settings__hint">
                Saving re-reads your sources, which can make a staged sequence stale.
              </p>
            </div>
          </section>

          {error !== null && (
            <section class="calendar-settings__error" role="alert">
              <strong>Save failed</strong>
              <span>{error}</span>
              <span>The draft is retained; reload before saving again.</span>
            </section>
          )}
          {notice !== null && (
            <p class="calendar-settings__notice" role="status">
              {notice}
            </p>
          )}

          <div class="calendar-settings__actions">
            {reloadRequired && (
              <button class="btn" onClick={() => void loadSettings()}>
                Reload settings
              </button>
            )}
            <button
              class="btn btn--primary"
              onClick={() => void save()}
              disabled={phase === "saving" || reloadRequired}
              aria-label="Save calendars"
            >
              {phase === "saving" ? "Saving…" : "Save calendars"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
