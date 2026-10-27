/* RowExpanded — task-actions F4b: the two-line expanded panel from the
   approved prototype (design/task-actions r2).

   Line 1 shapes the row (all staged on the row's draft, nothing committed):
   Time block / All-day, duration (one-tap chips, exact-minutes field, save-state
   chip, Remember for next time), Start (Auto + Set time, or the pinned time
   + Clear), and "May overlap Mint" with a short inline reason only when it is
   disabled. Line 2 holds Save and Discard on the left, rendered only while the draft
   differs from the committed session, and the Source menu on the right.

   Every transform goes through the pure rowDraft model (setMode, setPin,
   clearPin, canApplyPin) via controller.updateDraft; the store owns the draft
   lifecycle. Opening the panel opens a draft when none is kept; collapsing
   keeps the draft (the store only drops it on Save/Discard), so a staged row
   still reads "Staged" while collapsed. The Source menu (F4c) sits right on line 2;
   the batch bar (F6) is not part of this panel. */

import { useEffect, useRef, useState } from "preact/hooks";
import { useApp } from "./context";
import {
  draftIsStaged,
  draftSeedOf,
  rowFactsOf,
  type AppState,
} from "../store/store";
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
import { DurationChips } from "./DurationChips";
import { DurationRemember } from "./DurationRemember";
import { DurationStatus, saveDurationDraft } from "./DurationStatus";
import { rowId } from "./TaskRow";

export function RowExpanded({ item, s }: { item: AssignedItem; s: AppState }) {
  const { controller } = useApp();
  const id = item.id;
  const base = rowId(item, "x");
  const startBtnId = `start-btn-${base}`;
  const [editing, setEditing] = useState(false);
  const [customText, setCustomText] = useState("");
  const [customError, setCustomError] = useState<string | null>(null);
  const refocusStart = useRef(false);

  // Opening the panel opens a draft unless the store kept one.
  useEffect(() => {
    if (!s.drafts[id]) controller.openDraft(id);
  }, [id]);

  // Return focus to the Start trigger after the editor closes or Save/Discard
  // removes the focused button.
  useEffect(() => {
    if (!refocusStart.current) return;
    refocusStart.current = false;
    document.getElementById(startBtnId)?.focus();
  });

  const row = rowFactsOf(s, id);
  const draft = s.drafts[id] ?? draftSeedOf(s, id);
  if (!row || !draft) return null;

  const busy = s.runtimeBusy;
  const staged = draftIsStaged(s, id);
  const allDay = draft.mode === "allday";
  const protectedSource = isProtectedNative(row);

  const allDayWhy =
    row.source === "capacities"
      ? EXCLUSION_REASONS.allDayCapacities
      : protectedSource
        ? EXCLUSION_REASONS.protectedAllDay
        : null;
  const mintWhy = allDay
    ? EXCLUSION_REASONS.mintAllDay
    : row.isMintSession
      ? EXCLUSION_REASONS.mintSession
      : null;

  // Typed duration: valid text stages exact minutes (blocks = minutes / 30);
  // invalid text keeps the draft and surfaces an inline error. Empty text is
  // a no-op so tabbing through the field never stages or errors.
  const applyCustom = () => {
    if (customText.trim() === "") {
      setCustomError(null);
      return;
    }
    const parsed = parseDuration(customText);
    if (!parsed.ok) {
      setCustomError(parsed.error);
      return;
    }
    const blocks = minutesToBlocks(parsed.minutes);
    controller.updateDraft(id, (d) => ({ ...d, blocks }));
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
              controller.updateDraft(id, (d) => ({ ...clearPin(d), allowSourceTime: false }));
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
        <div class="trow__seg" role="radiogroup" aria-label="Scheduling">
          <label>
            <input
              type="radio"
              name={`mode-${base}`}
              value="time"
              checked={!allDay}
              disabled={busy}
              onChange={() => controller.updateDraft(id, (d) => setMode(d, "time"))}
            />{" "}
            Time block
          </label>
          <label title={allDayWhy ?? undefined}>
            <input
              type="radio"
              name={`mode-${base}`}
              value="allday"
              checked={allDay}
              disabled={busy || allDayWhy !== null}
              aria-describedby={allDayWhy ? `why-allday-${base}` : undefined}
              onChange={() => controller.updateDraft(id, (d) => setMode(d, "allday"))}
            />{" "}
            All-day
          </label>
        </div>
        {allDayWhy && (
          <span class="trow__why" id={`why-allday-${base}`}>
            {allDayWhy}
          </span>
        )}
        <DurationChips
          idBase={base}
          blocks={draft.blocks}
          allDay={allDay}
          disabled={busy}
          allDayWhy={allDayWhy}
          allDayWhyId={`why-allday-${base}`}
          onPickMinutes={(m) =>
            controller.updateDraft(id, (d) => ({
              ...setMode(d, "time"),
              blocks: minutesToBlocks(m),
            }))
          }
          onPickAllDay={() => controller.updateDraft(id, (d) => setMode(d, "allday"))}
        />
        <input
          class="trow__select dux-exact"
          type="text"
          inputMode="text"
          size={6}
          placeholder="e.g. 10m"
          aria-label="Custom duration"
          aria-invalid={customError ? "true" : undefined}
          aria-describedby={customError ? `why-dur-${base}` : undefined}
          title={allDay ? "All-day rows have no duration" : undefined}
          disabled={busy || allDay}
          value={customText}
          onInput={(e) => {
            setCustomText((e.target as HTMLInputElement).value);
            setCustomError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              applyCustom();
            } else if (e.key === "Escape") {
              setCustomText("");
              setCustomError(null);
            }
          }}
          onBlur={() => {
            if (customText.trim() !== "" && parseDuration(customText).ok) applyCustom();
          }}
        />
        {customError && (
          <span class="trow__why" id={`why-dur-${base}`} role="alert">
            {customError}
          </span>
        )}
        <DurationStatus s={s} item={item} />
        <DurationRemember item={item} s={s} blocks={draft.blocks} allDay={allDay} idBase={base} />
        <span class="trow__start" role="group" aria-label="Start">
          <span class="trow__muted">Start</span>
          {startControl()}
        </span>
        <label class="trow__ov" title={mintWhy ?? undefined}>
          <input
            type="checkbox"
            checked={draft.mintOverlap && mintWhy === null}
            disabled={busy || mintWhy !== null}
            aria-describedby={mintWhy ? `why-ov-${base}` : undefined}
            onChange={(e) => {
              const on = (e.target as HTMLInputElement).checked;
              controller.updateDraft(id, (d) => ({ ...d, mintOverlap: on }));
            }}
          />{" "}
          May overlap Mint
        </label>
        {mintWhy && (
          <span class="trow__why" id={`why-ov-${base}`}>
            {mintWhy}
          </span>
        )}
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
            controller.updateDraft(id, (d, r) => {
              const granted = protectedSource ? { ...d, allowSourceTime: allow } : d;
              return canApplyPin(granted, r) ? setPin(granted, r, time) : d;
            });
            closeEditor();
          }}
        />
      )}
      <div class="trow__l2">
        {staged && (
          <>
            <button
              type="button"
              class="trow__btn trow__btn--primary"
              disabled={busy}
              onClick={() => {
                refocusStart.current = true;
                saveDurationDraft(controller, id);
              }}
            >
              Save
            </button>
            <button
              type="button"
              class="trow__btn"
              disabled={busy}
              onClick={() => {
                refocusStart.current = true;
                setEditing(false);
                controller.discardDraft(id);
              }}
            >
              Discard
            </button>
          </>
        )}
        <SourceMenu item={item} s={s} />
      </div>
    </div>
  );
}
