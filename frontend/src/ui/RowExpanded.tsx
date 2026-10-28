/* RowExpanded — the expanded row panel (row redesign).

   Every control here is LOCAL day state, applied the moment it changes through
   controller.applyRowEdit (duration, All-day, start pin, source-time grant):
   there is no Save/Discard step and no staged chip. The row's Undo (in the
   collapsed header) reverses the last change. The duration is set by the
   collapsed row's slider (15 min .. 4 h, All-day at the left end); this panel
   adds only what the slider cannot say: an exact-minutes field (debounced,
   also on blur/Enter), the All-day mode toggle, Remember (a durable override
   of the source on every day), and the Start pin. May overlap Mint lives on
   the collapsed row. Only Remember writes beyond the session (to the
   duration-memory config), and it is its own explicit toggle. */

import { useEffect, useRef, useState } from "preact/hooks";
import { useApp } from "./context";
import { draftSeedOf, rowFactsOf, type AppState } from "../store/store";
import {
  canApplyPin,
  clearPin,
  isProtectedNative,
  setMode,
  setPin,
} from "../model/rowDraft";
import { EXCLUSION_REASONS } from "../model/batchEligibility";
import { display12h } from "../model/time";
import { minutesToBlocks, parseDuration } from "../model/parseDuration";
import type { AssignedItem } from "../model/types";
import { SourceMenu } from "./SourceMenu";
import { TimeEditor } from "./TimeEditor";
import { DurationRemember } from "./DurationRemember";
import { rowId } from "./TaskRow";

const TYPE_DEBOUNCE_MS = 400;

export function RowExpanded({ item, s }: { item: AssignedItem; s: AppState }) {
  const { controller } = useApp();
  const id = item.id;
  const base = rowId(item, "x");
  const startBtnId = `start-btn-${base}`;
  const [editing, setEditing] = useState(false);
  const [customText, setCustomText] = useState("");
  const [customError, setCustomError] = useState<string | null>(null);
  const refocusStart = useRef(false);

  const typed = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(typed.current), []);

  // Return focus to the Start trigger after the editor closes or Clear
  // removes the focused button.
  useEffect(() => {
    if (!refocusStart.current) return;
    refocusStart.current = false;
    document.getElementById(startBtnId)?.focus();
  });

  const row = rowFactsOf(s, id);
  const draft = draftSeedOf(s, id);
  if (!row || !draft) return null;

  const busy = s.runtimeBusy;
  const allDay = draft.mode === "allday";
  const protectedSource = isProtectedNative(row);

  // All-day is a local plan state for every row except a protected Todoist
  // native time; a Capacities row may go all-day (blocks 0, no source write).
  const allDayWhy = protectedSource ? EXCLUSION_REASONS.protectedAllDay : null;

  // Typed duration: valid text applies exact minutes (blocks = minutes / 30)
  // at once; invalid text changes nothing and surfaces an inline error. Empty
  // text is a no-op so tabbing through the field never changes or errors.
  const applyCustom = (text: string) => {
    clearTimeout(typed.current);
    if (text.trim() === "") {
      setCustomError(null);
      return;
    }
    const parsed = parseDuration(text);
    if (!parsed.ok) {
      setCustomError(parsed.error);
      return;
    }
    const blocks = minutesToBlocks(parsed.minutes);
    controller.applyRowEdit(id, (d) => ({ ...setMode(d, "time"), blocks }));
    setCustomText("");
    setCustomError(null);
  };

  const closeEditor = () => {
    refocusStart.current = true;
    setEditing(false);
  };

  const startControl = () => {
    if (allDay) {
      return <span class="trow__muted">none (all-day)</span>;
    }
    const trigger = (label: string, aria?: string) => (
      <button
        type="button"
        class="trow__btn"
        id={startBtnId}
        aria-label={aria}
        aria-expanded={editing}
        disabled={busy}
        onClick={() => setEditing(true)}
      >
        {label}
      </button>
    );
    if (draft.pin) {
      return (
        <>
          {trigger(display12h(draft.pin), `Change start time, now ${display12h(draft.pin)}`)}
          <button
            type="button"
            class="trow__btn"
            disabled={busy}
            onClick={() => {
              refocusStart.current = true;
              controller.applyRowEdit(id, (d) => ({ ...clearPin(d), allowSourceTime: false }));
            }}
          >
            Clear
          </button>
        </>
      );
    }
    return (
      <>
        {protectedSource ? (
          <span>Source {display12h(row.nativeStart)}</span>
        ) : (
          <strong>Auto</strong>
        )}
        {trigger("Set time")}
      </>
    );
  };

  return (
    <div class="trow__panel">
      <div class="trow__l1">
        <span class="trow__grp" role="group" aria-label="Duration">
          <input
            class="trow__select dux-exact"
            type="text"
            inputMode="text"
            placeholder="Exact, e.g. 25m"
            aria-label="Exact minutes"
            aria-invalid={customError ? "true" : undefined}
            aria-describedby={customError ? `why-dur-${base}` : undefined}
            title={allDay ? "All-day rows have no duration" : "Type an exact duration such as 25m or 1h10"}
            disabled={busy || allDay}
            value={customText}
            onInput={(e) => {
              const text = (e.target as HTMLInputElement).value;
              setCustomText(text);
              setCustomError(null);
              clearTimeout(typed.current);
              if (parseDuration(text).ok) {
                typed.current = setTimeout(() => applyCustom(text), TYPE_DEBOUNCE_MS);
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                applyCustom(customText);
              } else if (e.key === "Escape") {
                clearTimeout(typed.current);
                setCustomText("");
                setCustomError(null);
              }
            }}
            onBlur={() => {
              if (customText.trim() !== "" && parseDuration(customText).ok) applyCustom(customText);
            }}
          />
          <button
            type="button"
            class={`trow__tog${allDay ? " trow__tog--on" : ""}`}
            aria-pressed={allDay}
            disabled={busy || (allDayWhy !== null && !allDay)}
            title={allDayWhy ?? (allDay ? "All-day: no start time. Click to give it a time block again." : "Make this an all-day item: no start time, no band capacity. Local to today's plan.")}
            aria-describedby={allDayWhy ? `why-allday-${base}` : undefined}
            onClick={() =>
              controller.applyRowEdit(id, (d) => setMode(d, allDay ? "time" : "allday"))
            }
          >
            All-day
          </button>
          <DurationRemember item={item} s={s} blocks={draft.blocks} allDay={allDay} idBase={base} />
        </span>
        {allDayWhy && (
          <span class="trow__why" id={`why-allday-${base}`}>
            {allDayWhy}
          </span>
        )}
        {customError && (
          <span class="trow__why" id={`why-dur-${base}`} role="alert">
            {customError}
          </span>
        )}
        <span class="trow__start" role="group" aria-label="Start">
          <span class="trow__muted">Start</span>
          {startControl()}
        </span>
      </div>
      {editing && !allDay && (
        <TimeEditor
          idBase={base}
          rowName={item.name}
          initialTime={draft.pin ?? row.nativeStart ?? "10:00"}
          protectedSource={protectedSource}
          initialAllow={draft.allowSourceTime}
          onClose={closeEditor}
          onApply={(time, allow) => {
            controller.applyRowEdit(id, (d, r) => {
              const granted = protectedSource ? { ...d, allowSourceTime: allow } : d;
              return canApplyPin(granted, r) ? setPin(granted, r, time) : d;
            });
            closeEditor();
          }}
        />
      )}
      <div class="trow__l2">
        <SourceMenu item={item} s={s} />
      </div>
    </div>
  );
}
