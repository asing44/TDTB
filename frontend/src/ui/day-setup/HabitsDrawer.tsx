/* HabitsDrawer — the Set up day "Review habits" slide-over (bottom sheet on
   narrow screens). A view over the same today-only overrides the Habits card
   edits: rows come from habitsView, every edit is a withHabitEntry patch, and
   saving is the card's save function. Habits are an active list switched on or
   off for today; completion is tracked in Grit and never shown here. Switching
   a habit off dims its row in place; the rows never move. Focus, Tab wrap,
   Escape, and focus return come from useDialog. */

import { useEffect, useState } from "preact/hooks";
import { minutesToBlocks } from "../../model/time";
import {
  HABIT_MINUTES_MAX,
  parseHabitMinutes,
  withHabitEntry,
  type HabitRow,
  type HabitsView,
} from "../../model/habits";
import type { HabitOverrides } from "../../model/types";
import { useDialog } from "../useDialog";

export const HABIT_STEP_MINUTES = 5;
/* A focused control must never go natively `disabled`: the browser drops
   focus to <body>, the dialog stops seeing Escape/Tab. Busy, limit and
   nothing-to-reset states use aria-disabled and ignore the click instead. */
const ariaOff = (on: boolean) => (on ? "true" : undefined);
const PRESETS = [5, 10, 15, 30];

export type HabitsSaveStatus = "idle" | "saving" | "saved" | "failed";

export function blocksText(minutes: number): string {
  const blocks = minutesToBlocks(minutes);
  return `${blocks} ${blocks === 1 ? "block" : "blocks"}`;
}

export interface HabitsDrawerProps {
  view: HabitsView;
  overrides?: HabitOverrides;
  dateLabel: string;
  status: HabitsSaveStatus;
  error: string;
  onSave: (value: HabitOverrides) => void;
  onRetry: () => void;
  onClose: () => void;
}

function HabitRowView({
  row,
  editing,
  onEdit,
  onPatch,
  onFieldError,
}: {
  row: HabitRow;
  editing: boolean;
  onEdit: (open: boolean) => void;
  onPatch: (patch: { on?: boolean; minutes?: number }) => void;
  onFieldError: (message: string) => void;
}) {
  const [text, setText] = useState(String(row.minutes));
  const off = !row.on;
  const open = editing && !off;
  function commit(raw: string) {
    const value = parseHabitMinutes(raw);
    if (value === null) {
      setText(String(row.minutes));
      onFieldError("Enter whole minutes from 0 to 480.");
      return;
    }
    if (value !== row.minutes) onPatch({ minutes: value });
  }
  const step = (delta: number) =>
    onPatch({ minutes: Math.max(0, Math.min(HABIT_MINUTES_MAX, row.minutes + delta)) });
  return (
    <li class={`habits-drawer__row${off ? " habits-drawer__row--off" : ""}`}>
      <div class="habits-drawer__main">
        <div class="habits-drawer__name">
          <span class="habits-drawer__label">{row.label}</span>
          {off ? (
            <span class="habits-drawer__meta">
              <span class="habits-drawer__chip">Off</span> not counted today
            </span>
          ) : row.edited ? (
            <span class="habits-drawer__meta">
              default {row.defaultMinutes} min · <b>edited</b>
            </span>
          ) : null}
        </div>
        <div class="habits-drawer__stepper">
          <button type="button" class="habits-drawer__step" disabled={off} aria-disabled={ariaOff(row.minutes <= 0)}
            aria-label={`Decrease ${row.label} minutes by ${HABIT_STEP_MINUTES}`}
            onClick={() => { if (row.minutes > 0) step(-HABIT_STEP_MINUTES); }}>−</button>
          <button type="button" class="habits-drawer__value" disabled={off} aria-expanded={open}
            aria-label={`${row.label}: ${row.minutes} minutes. ${off ? "Turned off" : "Edit minutes"}`}
            onClick={() => { setText(String(row.minutes)); onEdit(!open); }}>
            {row.minutes}<small>min</small>
          </button>
          <button type="button" class="habits-drawer__step" disabled={off} aria-disabled={ariaOff(row.minutes >= HABIT_MINUTES_MAX)}
            aria-label={`Increase ${row.label} minutes by ${HABIT_STEP_MINUTES}`}
            onClick={() => { if (row.minutes < HABIT_MINUTES_MAX) step(HABIT_STEP_MINUTES); }}>+</button>
        </div>
        <button type="button" role="switch" class="anchor-popover__switch all-blocks__switch"
          aria-checked={row.on} aria-label={`Count ${row.label} today`}
          onClick={() => onPatch({ on: !row.on })} />
      </div>
      {open && (
        <div class="habits-drawer__editor">
          <label>Minutes today
            <input type="number" min="0" max={HABIT_MINUTES_MAX} step="1" value={text}
              aria-label={`${row.label} minutes today`}
              onInput={(e) => setText(e.currentTarget.value)}
              onBlur={(e) => commit(e.currentTarget.value)}
              onKeyDown={(e) => { if (e.key === "Enter") commit(e.currentTarget.value); }} />
          </label>
          <div class="habits-drawer__presets" role="group" aria-label="Preset minutes">
            {PRESETS.map((p) => (
              <button type="button" key={p} aria-pressed={row.minutes === p}
                onClick={() => { setText(String(p)); onPatch({ minutes: p }); }}>{p}</button>
            ))}
          </div>
          <div class="habits-drawer__editor-actions">
            <button type="button" class="btn" aria-disabled={ariaOff(!row.edited)}
              onClick={() => { if (!row.edited) return; setText(String(row.defaultMinutes)); onPatch({ on: true, minutes: row.defaultMinutes }); }}>
              Reset to {row.defaultMinutes} min
            </button>
            <button type="button" class="btn btn--primary" onClick={() => onEdit(false)}>Done</button>
          </div>
        </div>
      )}
    </li>
  );
}

export function HabitsDrawer({
  view, overrides, dateLabel, status, error, onSave, onRetry, onClose,
}: HabitsDrawerProps) {
  const dialog = useDialog(onClose);
  // Safety net: if focus ever falls out of the dialog (to <body>), the dialog's
  // own onKeyDown never sees Escape. Events from inside are already handled.
  useEffect(() => {
    const onDocKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const el = dialog.ref.current;
      if (el && e.target instanceof Node && el.contains(e.target)) return;
      onClose();
    };
    document.addEventListener("keydown", onDocKey);
    return () => document.removeEventListener("keydown", onDocKey);
  }, [onClose]);
  const [editing, setEditing] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState("");
  const off = view.rows.filter((r) => !r.on);
  const offMinutes = off.reduce((sum, r) => sum + r.minutes, 0);
  const changed = view.rows.filter((r) => r.edited).length;
  const allOff = view.rows.length > 0 && off.length === view.rows.length;
  const busy = status === "saving";

  function patch(row: HabitRow, p: { on?: boolean; minutes?: number }) {
    if (busy) return;
    setFieldError("");
    onSave(withHabitEntry(overrides, row.key, p, row.defaultMinutes));
  }
  function bulk() {
    if (busy) return;
    setEditing(null); setFieldError("");
    onSave(view.rows.reduce(
      (acc, r) => withHabitEntry(acc, r.key, { on: allOff }, r.defaultMinutes), overrides ?? { items: {}, total: null }));
  }
  function resetAll() {
    if (busy || changed === 0) return;
    setEditing(null); setFieldError("");
    onSave({ items: {}, total: null });
  }

  return (
    <>
      <div class="drawer-backdrop" onClick={onClose} />
      <div class="habits-drawer" role="dialog" aria-modal="true" aria-label="Habits" tabIndex={-1}
        ref={dialog.ref} onKeyDown={(e) => dialog.onKeyDown(e as unknown as KeyboardEvent)}>
        <header class="habits-drawer__head">
          <div>
            <h2>Habits</h2>
            <p class="habits-drawer__sub">
              Today only · {dateLabel} · {view.countedMinutes} min counted · {blocksText(view.countedMinutes)}
            </p>
          </div>
          <button type="button" class="btn habits-drawer__close" aria-label="Close habits drawer" onClick={onClose}>✕</button>
        </header>
        <div class="habits-drawer__scroll">
          <section aria-labelledby="habits-drawer-today">
            <div class="habits-drawer__group-head">
              <h3 id="habits-drawer-today">Today <span>{view.habitCount}</span></h3>
              <button type="button" class="btn" disabled={view.rows.length === 0} aria-disabled={ariaOff(busy)} onClick={bulk}>
                {allOff
                  ? `Turn on all ${view.rows.length} ${view.rows.length === 1 ? "habit" : "habits"}`
                  : `Turn off all ${view.rows.length} habits`}
              </button>
            </div>
            <ul class="habits-drawer__list">
              {view.rows.map((row) => (
                <HabitRowView key={row.key} row={row} editing={editing === row.key}
                  onEdit={(open) => setEditing(open ? row.key : null)}
                  onPatch={(p) => patch(row, p)} onFieldError={setFieldError} />
              ))}
            </ul>
          </section>
          {off.length > 0 && (
            <p class="habits-drawer__summary">
              <b>Off today:</b> {off.map((r) => r.label).join(", ")} — {off.length} {off.length === 1 ? "habit" : "habits"}, {offMinutes} min not counted
            </p>
          )}
          {fieldError && <span class="field-error" role="alert">{fieldError}</span>}
        </div>
        <footer class="habits-drawer__foot">
          <div class="habits-drawer__status" role="status">
            {status === "saving" ? "Saving…" : status === "failed" ? (
              <>
                <span class="habits-drawer__chip habits-drawer__chip--bad">Not saved</span>
                <button type="button" class="btn" onClick={onRetry}>Retry</button>
                <span class="field-error" role="alert">{error}</span>
              </>
            ) : status === "saved" ? "✓ Saved for today" : ""}
          </div>
          <button type="button" class="btn" aria-disabled={ariaOff(busy || changed === 0)}
            aria-label={`Reset day overrides${changed ? ` (${changed} changed)` : ""}`} onClick={resetAll}>
            Reset day overrides{changed > 0 && <span class="habits-drawer__chip">{changed}</span>}
          </button>
        </footer>
      </div>
    </>
  );
}
