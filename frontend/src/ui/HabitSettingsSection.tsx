/* HabitSettingsSection — the standing per-habit plan (minutes and weekdays)
   that Grit habits are counted from. Distinct from the today-only Habits card
   on the Set up day screen: these values apply every day.

   Autosave, no Save button. Each minutes field commits on blur or Enter and
   each weekday button commits on press. The store is a versioned full
   replacement, so every commit goes through `createHabitSettingsAutosave`:
   saves are serialised and each one sends the revision the previous save
   returned. A 409 ("changed elsewhere") stops the chain; rows lock until the
   operator reloads. A rejected row or transport failure rolls the habit list
   back to the last server-confirmed copy and says so on that row.

   Rows come from the Grit inventory: configured (saved minutes), unconfigured
   (in Grit, counted at the default until minutes are set), missing (saved but
   no longer in Grit; the only row that can be deleted, immediately, with an
   Undo toast) and unverified (saved, Grit not read yet). Saved minutes stay
   editable while Grit is unavailable; only Refresh needs Grit. New habits come
   from Grit, so there is no add control. */

import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { createHabitSettingsAutosave, type HabitSettingsAutosave } from "../adapters/api";
import { HABIT_MINUTES_MAX, habitsView, parseHabitMinutes } from "../model/habits";
import type {
  HabitInventoryRow,
  HabitInventoryRowStatus,
  HabitInventoryStatus,
  HabitSetting,
  HabitSettings,
} from "../model/types";
import "./habit-settings.css";

/** Minutes an unconfigured Grit habit counts for until the operator sets it. */
export const HABIT_DEFAULT_MINUTES = 15;
const UNDO_MS = 8000;
const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];

type RowSave = { phase: "saving" | "saved" | "error"; message?: string };
type RowStatus = HabitInventoryRowStatus;

interface RowModel {
  name: string;
  status: RowStatus;
  minutes: number | null;
  weekdays: number[] | null;
}

/** Plain-language weekday summary: "Every day", "Weekdays", "Mon, Wed". */
export function weekdaySummary(days: number[] | null): string {
  if (days === null || days.length === 0 || days.length === 7) return "Every day";
  const sorted = [...days].sort((a, b) => a - b);
  if (sorted.join() === "0,1,2,3,4") return "Weekdays";
  if (sorted.join() === "5,6") return "Weekends";
  return sorted.map((d) => DAY_NAMES[d].slice(0, 3)).join(", ");
}

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function readLine(settings: HabitSettings | null, refreshFailed: boolean): string {
  if (!settings) return "";
  if (settings.gritReadAt === null) return "Grit has not been read yet";
  const at = new Date(settings.gritReadAt);
  const when = Number.isNaN(at.getTime())
    ? settings.gritReadAt
    : at.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
  return refreshFailed ? `Last good read ${when}` : `Last read ${when}`;
}

function HabitRowView({
  row,
  save,
  locked,
  onMinutes,
  onDays,
  onDelete,
}: {
  row: RowModel;
  save: RowSave | undefined;
  locked: boolean;
  onMinutes: (minutes: number) => void;
  onDays: (days: number[] | null) => void;
  onDelete: () => void;
}) {
  const [text, setText] = useState(row.minutes === null ? "" : String(row.minutes));
  const [fieldError, setFieldError] = useState("");
  const [daysOpen, setDaysOpen] = useState(false);
  useEffect(() => {
    setText(row.minutes === null ? "" : String(row.minutes));
  }, [row.minutes]);
  const missing = row.status === "missing";
  const unconfigured = row.status === "unconfigured";

  function commit() {
    setFieldError("");
    if (text.trim() === "" && unconfigured) return;
    const value = parseHabitMinutes(text);
    if (value === null) {
      setFieldError(`Enter whole minutes from 0 to ${HABIT_MINUTES_MAX}.`);
      return;
    }
    if (value !== row.minutes) onMinutes(value);
  }
  function toggleDay(day: number) {
    const current = row.weekdays ?? [0, 1, 2, 3, 4, 5, 6];
    const next = current.includes(day) ? current.filter((d) => d !== day) : [...current, day].sort();
    onDays(next.length === 0 || next.length === 7 ? null : next);
  }
  const days = row.weekdays ?? [0, 1, 2, 3, 4, 5, 6];

  return (
    <li
      class={`hb-row${missing ? " hb-row--missing" : ""}`}
      data-habit={row.name}
      data-status={row.status}
    >
      <div class="hb-row__main">
        <span class="hb-row__name">{row.name}</span>
        <span class="hb-row__min">
          <input
            type="number"
            inputMode="numeric"
            min="0"
            max={HABIT_MINUTES_MAX}
            step="1"
            value={text}
            placeholder={unconfigured ? String(HABIT_DEFAULT_MINUTES) : undefined}
            aria-label={`Minutes for ${row.name}`}
            aria-invalid={fieldError ? "true" : undefined}
            disabled={locked}
            onInput={(e) => setText(e.currentTarget.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
            }}
          />
          <span aria-hidden="true">min</span>
        </span>
        <span class="hb-row__state">
          {row.status === "configured" && <span class="chip chip--ok">configured</span>}
          {unconfigured && (
            <span
              class="chip chip--warn"
              title={`In Grit, no minutes yet. Counted at ${HABIT_DEFAULT_MINUTES} min.`}
            >
              unconfigured · {HABIT_DEFAULT_MINUTES} min
            </span>
          )}
          {missing && (
            <span class="chip chip--err" title="Saved here but no longer in Grit">
              missing
            </span>
          )}
          {row.status === "unverified" && <span class="chip">Grit not read yet</span>}
        </span>
      </div>
      <div class="hb-row__sub">
        {missing ? (
          <span class="hb-row__muted">Not in Grit</span>
        ) : unconfigured ? (
          <span class="hb-row__muted">Set minutes to choose days</span>
        ) : (
          <button
            type="button"
            class="hb-row__days-toggle"
            aria-expanded={daysOpen}
            aria-label={`Choose days for ${row.name}`}
            onClick={() => setDaysOpen(!daysOpen)}
          >
            {weekdaySummary(row.weekdays)} {daysOpen ? "▴" : "▾"}
          </button>
        )}
        {missing && (
          <button
            type="button"
            class="btn btn--sm btn--ghost"
            aria-label={`Delete ${row.name} from settings`}
            onClick={onDelete}
          >
            Delete
          </button>
        )}
        <span
          class={`hb-row__save${save?.phase === "error" ? " hb-row__save--err" : ""}`}
          role="status"
          aria-live="polite"
        >
          {save?.phase === "saving" && "Saving…"}
          {save?.phase === "saved" && "Saved ✓"}
          {save?.phase === "error" && (save.message ?? "Not saved")}
        </span>
      </div>
      {daysOpen && !missing && !unconfigured && (
        <div class="hb-row__days" role="group" aria-label={`Days for ${row.name}`}>
          {DAY_NAMES.map((name, day) => (
            <button
              type="button"
              key={name}
              class="hb-dow"
              aria-pressed={days.includes(day)}
              aria-label={name}
              disabled={locked}
              onClick={() => toggleDay(day)}
            >
              {DAY_LETTERS[day]}
            </button>
          ))}
          <span class="hb-row__muted">None selected = every day</span>
        </div>
      )}
      {fieldError && (
        <span class="field-error" role="alert">
          {fieldError}
        </span>
      )}
    </li>
  );
}

export function HabitSettingsSection({ active }: { active: boolean }) {
  const { controller } = useApp();
  const s = useAppState();
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState("");
  const [habits, setHabits] = useState<HabitSetting[]>([]);
  const [inventory, setInventory] = useState<HabitInventoryRow[]>([]);
  const [meta, setMeta] = useState<HabitSettings | null>(null);
  const [inventoryStatus, setInventoryStatus] = useState<HabitInventoryStatus>("ok");
  const [conflict, setConflict] = useState<string | null>(null);
  const [rowSave, setRowSave] = useState<Record<string, RowSave>>({});
  const [refreshing, setRefreshing] = useState(false);
  const [refreshFailed, setRefreshFailed] = useState<string | null>(null);
  const [toast, setToast] = useState<{ name: string; restore: HabitSetting[] } | null>(null);
  const auto = useRef<HabitSettingsAutosave | null>(null);
  const pending = useRef<Record<string, number>>({});
  const desired = useRef<HabitSetting[]>([]);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const adopt = (settings: HabitSettings) => {
    auto.current = createHabitSettingsAutosave((d) => controller.saveHabitSettings(d), settings);
    desired.current = settings.habits.map((h) => ({ ...h }));
    pending.current = {};
    setHabits(desired.current);
    setInventory(settings.inventory);
    setInventoryStatus(settings.inventoryStatus);
    setMeta(settings);
    setConflict(null);
    setRowSave({});
  };

  const load = async () => {
    setPhase("loading");
    setLoadError("");
    try {
      adopt(await controller.loadHabitSettings());
      setPhase("ready");
    } catch (e) {
      setPhase("error");
      setLoadError(messageOf(e));
    }
  };

  useEffect(() => {
    void load();
    return () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, [controller]);

  const rows = useMemo<RowModel[]>(() => {
    const saved = new Map(habits.map((h) => [h.name, h]));
    const grit = new Set(
      inventory.filter((r) => r.status === "configured" || r.status === "unconfigured").map((r) => r.name),
    );
    const names: string[] = [];
    for (const r of inventory) {
      if (saved.has(r.name) || r.status === "unconfigured" || r.status === "configured") names.push(r.name);
    }
    for (const h of habits) if (!names.includes(h.name)) names.push(h.name);
    return names.map((name) => {
      const h = saved.get(name);
      let status: RowStatus;
      if (inventoryStatus === "unavailable") status = "unverified";
      else if (!h) status = "unconfigured";
      else status = grit.has(name) ? "configured" : "missing";
      return { name, status, minutes: h ? h.minutes : null, weekdays: h ? h.weekdays : null };
    });
  }, [habits, inventory, inventoryStatus]);

  if (!active) return null;

  const blocked = conflict !== null;
  const mark = (names: string[], state: RowSave | null) =>
    setRowSave((cur) => {
      const next = { ...cur };
      for (const n of names) {
        if (state) next[n] = state;
        else delete next[n];
      }
      return next;
    });

  function commit(next: HabitSetting[], name: string, afterFail?: () => void) {
    desired.current = next;
    setHabits(next);
    pending.current[name] = (pending.current[name] ?? 0) + 1;
    mark([name], { phase: "saving" });
    const chain = auto.current;
    if (!chain) return;
    const rollback = () => {
      const confirmed = chain.current().habits.map((h) => ({ ...h }));
      desired.current = confirmed;
      setHabits(confirmed);
    };
    const settle = () => {
      pending.current[name] = Math.max(0, (pending.current[name] ?? 1) - 1);
      return pending.current[name] === 0;
    };
    chain
      .save(next)
      .then((outcome) => {
        const last = settle();
        if (outcome.ok) {
          setInventory(outcome.settings.inventory);
          setInventoryStatus(outcome.settings.inventoryStatus);
          setMeta(outcome.settings);
          if (last) mark([name], { phase: "saved" });
          return;
        }
        if (outcome.reason === "conflict") {
          setConflict(outcome.message);
          mark([name], { phase: "error", message: "Not saved" });
          return;
        }
        rollback();
        afterFail?.();
        mark([name], {
          phase: "error",
          message: outcome.reason === "invalid" ? "Not saved — check the value" : "Not saved",
        });
      })
      .catch(() => {
        settle();
        rollback();
        afterFail?.();
        mark([name], { phase: "error", message: "Not saved — try again" });
      });
  }

  const replace = (name: string, patch: Partial<HabitSetting>, fallback: HabitSetting) => {
    const current = desired.current;
    const exists = current.some((h) => h.name === name);
    const next = exists
      ? current.map((h) => (h.name === name ? { ...h, ...patch } : h))
      : [...current, { ...fallback, ...patch }];
    commit(next, name);
  };

  const onMinutes = (row: RowModel, minutes: number) =>
    replace(row.name, { minutes }, { name: row.name, minutes, weekdays: null });
  const onDays = (row: RowModel, weekdays: number[] | null) =>
    replace(row.name, { weekdays }, { name: row.name, minutes: row.minutes ?? HABIT_DEFAULT_MINUTES, weekdays });

  const dismissToast = () => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = null;
    setToast(null);
  };
  const onDelete = (row: RowModel) => {
    const restore = desired.current.map((h) => ({ ...h }));
    commit(
      desired.current.filter((h) => h.name !== row.name),
      row.name,
    );
    setToast({ name: row.name, restore });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), UNDO_MS);
  };
  const onUndo = () => {
    if (!toast) return;
    const { name, restore } = toast;
    const entry = restore.find((h) => h.name === name);
    dismissToast();
    if (entry && !desired.current.some((h) => h.name === name)) {
      commit([...desired.current, entry], name);
    }
  };

  const refresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshFailed(null);
    try {
      const result = await controller.refreshGritHabits();
      setInventory(result.settings.inventory);
      setInventoryStatus(result.settings.inventoryStatus);
      setMeta((cur) => ({ ...(cur ?? result.settings), gritReadAt: result.settings.gritReadAt, inventoryStatus: result.settings.inventoryStatus }));
      if (!result.refresh.ok) setRefreshFailed(result.refresh.reason ?? "unavailable");
    } catch (e) {
      setRefreshFailed(messageOf(e));
    } finally {
      setRefreshing(false);
    }
  };

  const unavailable = inventoryStatus === "unavailable" || refreshFailed !== null;
  const unconfigured = rows.filter((r) => r.status === "unconfigured").length;
  const gritFound = rows.filter((r) => r.status === "configured" || r.status === "unconfigured").length;
  const view = habitsView(s.inputs?.habits, s.daySetup.habits);
  const todayLine = view.mode === "items" && !unavailable ? view.summary : null;
  const chip = unavailable
    ? { cls: "chip--err", text: "Grit unavailable" }
    : inventoryStatus === "stale"
      ? { cls: "chip--warn", text: "Grit stale" }
      : { cls: "chip--ok", text: "Grit connected" };

  return (
    <section
      class="setup-section hb"
      id="settings-section-habits"
      data-settings-section="habits"
      aria-labelledby="habit-settings-heading"
    >
      <div class="setup-section__head">
        <h3 id="habit-settings-heading">Habits</h3>
        <span class="hb__sub">every day · from Grit</span>
      </div>
      <div class="setup-section__body">
        {phase === "loading" && (
          <p class="hb__muted" aria-live="polite">
            Loading habits…
          </p>
        )}
        {phase === "error" && (
          <div class="hb__callout hb__callout--err" role="alert">
            <span>
              <b>Habits could not be loaded.</b> {loadError}
            </span>
            <button type="button" class="btn btn--sm" onClick={() => void load()}>
              Try again
            </button>
          </div>
        )}
        {phase === "ready" && (
          <>
            <div class="hb__top">
              <span class={`chip ${chip.cls}`}>{chip.text}</span>
              <span class="hb__muted hb__grow">{readLine(meta, refreshFailed !== null)}</span>
              <button
                type="button"
                class="btn btn--sm"
                aria-disabled={refreshing ? "true" : undefined}
                onClick={() => void refresh()}
              >
                {refreshing ? "Reading Grit…" : "Refresh from Grit"}
              </button>
            </div>
            {todayLine && (
              <p class="hb__today" id="habit-settings-today">
                Today: <b>{todayLine}</b>
              </p>
            )}
            {unavailable && (
              <div class="hb__callout hb__callout--err" role="alert" id="habit-settings-unavailable">
                <span>
                  <b>Can’t read Grit.</b> Is the app open? Showing your last read; saved minutes still
                  work. New or missing habits can’t be checked until Grit is read.
                </span>
                <button type="button" class="btn btn--sm" onClick={() => void refresh()}>
                  Try again
                </button>
              </div>
            )}
            {blocked && (
              <div class="hb__callout hb__callout--err" role="alert" id="habit-settings-conflict">
                <span>
                  <b>Changed elsewhere.</b> Habits were saved from another window since you opened
                  this. Your last edit was not saved. Reload to see the latest.
                </span>
                <button type="button" class="btn btn--sm btn--primary" onClick={() => void load()}>
                  Reload latest
                </button>
              </div>
            )}
            {rows.length === 0 ? (
              <div class="hb__empty" id="habit-settings-empty">
                <strong>No habits found in Grit</strong>
                <span>
                  Add habits in Grit, then refresh. Habits you saved here earlier would show as
                  “missing”.
                </span>
                <button type="button" class="btn btn--sm btn--primary" onClick={() => void refresh()}>
                  Refresh from Grit
                </button>
              </div>
            ) : (
              <>
                <p class="hb__hint">
                  {gritFound} found · {unconfigured} unconfigured. An unconfigured habit counts{" "}
                  {HABIT_DEFAULT_MINUTES} min until you set minutes (0–{HABIT_MINUTES_MAX}). Each change
                  saves when you leave the field.
                </p>
                <ul class="hb__list" aria-label="Habits from Grit">
                  {rows.map((row) => (
                    <HabitRowView
                      key={row.name}
                      row={row}
                      save={rowSave[row.name]}
                      locked={blocked}
                      onMinutes={(m) => onMinutes(row, m)}
                      onDays={(d) => onDays(row, d)}
                      onDelete={() => onDelete(row)}
                    />
                  ))}
                </ul>
              </>
            )}
            {toast && (
              <div class="hb__toast" role="status" id="habit-settings-undo">
                <span>Deleted {toast.name}.</span>
                <button type="button" class="btn btn--sm" onClick={onUndo}>
                  Undo
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
