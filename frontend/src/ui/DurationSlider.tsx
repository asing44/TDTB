/* DurationSlider — the row's inline duration control: the readout above a
   compact [−] range [+] strip (restored from the earlier plan-row design:
   readout above the slider, 15-minute steppers flanking it, a steady track).
   It lives in the collapsed row's duration cell and stays there while the row
   is expanded, so a row never carries two sliders. It stages through the same
   row-draft path as the select and the typed custom duration: minutes ->
   minutesToBlocks -> updateDraft. Nothing is committed here; Save/Discard stay
   the only commit path. The cell is a size container (duration-slider.css):
   steppers hide first, then the track, leaving the readout with its tooltip.

   The track is a quarter-hour grid whose leftmost notch is All-day (value 0),
   the earlier plan-row design's bottom-of-track meaning: the thumb sits at 0
   for an all-day row and moving right picks 15, 30, 45 … up to the max. */

import type { ComponentChildren } from "preact";
import { minutesToBlocks } from "../model/parseDuration";
import { blocksLabel } from "../model/time";
import "./duration-slider.css";

/** The leftmost notch. 0 minutes is All-day (blocks 0), so the track can both
    read and stage the state the expanded row's All-day radio stages. */
export const SLIDER_MIN_MINUTES = 0;
/** The largest notch matches the biggest preset in the duration select (4hr). */
export const SLIDER_MAX_MINUTES = 240;
/** Quarter-hour notches. 15 minutes is not a 30-minute block multiple, so it
    stages as 0.5 blocks — the exact-minutes path the typed custom duration
    already uses (minutesToBlocks, which the backend reads back as
    int(blocks * 30) = 15 minutes). Nothing is rounded to a whole block. */
export const SLIDER_STEP_MINUTES = 15;
/** The −/+ steppers move one notch, the same quarter hour as the track. */
export const STEPPER_MINUTES = 15;

/** The nearest quarter-hour notch, clamped to the track. A source row below
    the grid (a 10-minute tag estimate) keeps its exact readout while the thumb
    rests on the closest notch, so every drag starts from a real notch. */
export function snapMinutes(minutes: number): number {
  const clamped = Math.min(SLIDER_MAX_MINUTES, Math.max(SLIDER_MIN_MINUTES, minutes));
  return Math.round(clamped / SLIDER_STEP_MINUTES) * SLIDER_STEP_MINUTES;
}

/** Clamp a stepped minute value to the track: − from 15 lands on All-day. */
export function stepMinutes(minutes: number, direction: 1 | -1): number {
  const next = minutes + direction * STEPPER_MINUTES;
  return Math.min(SLIDER_MAX_MINUTES, Math.max(SLIDER_MIN_MINUTES, next));
}

export function DurationSlider({
  blocks,
  disabled,
  allDayLocked = false,
  title,
  idBase,
  name,
  onChange,
  status,
}: {
  /** The row's duration in 30-minute blocks; 0 = All-day. */
  blocks: number;
  /** Locked: the runtime is busy, or the row's all-day path is closed. */
  disabled: boolean;
  /** The All-day notch is closed (Capacities / protected source time): the
      track starts at the first timed notch and − stops there. */
  allDayLocked?: boolean;
  title?: string;
  idBase: string;
  /** The row's name, carried by the stepper labels. */
  name?: string;
  onChange: (blocks: number) => void;
  /** Save-state chip, set beside the readout on the same line. */
  status?: ComponentChildren;
}) {
  const minutes = Math.round(blocks * 30);
  const floor = allDayLocked ? STEPPER_MINUTES : SLIDER_MIN_MINUTES;
  const shown = Math.max(floor, snapMinutes(minutes));
  const label = blocksLabel(blocks);
  const listId = `dur-ticks-${idBase}`;
  const ticks: number[] = [];
  for (let m = 30; m <= SLIDER_MAX_MINUTES; m += 30) ticks.push(m);
  const forName = name ? ` for ${name}` : "";
  const step = (direction: 1 | -1) => onChange(minutesToBlocks(stepMinutes(shown, direction)));
  return (
    <span class="dslider" title={title ?? `Duration ${label}`}>
      <span class="dslider__top">
        {status}
        <output class="dslider__label" aria-live="off">
          {label}
        </output>
      </span>
      <span class="dslider__ctl">
        <button
          type="button"
          class="dslider__step"
          aria-label={`15 minutes less${forName}`}
          disabled={disabled || shown <= floor}
          onClick={() => step(-1)}
        >
          −
        </button>
        <input
          class="dslider__range"
          type="range"
          aria-label="Duration slider"
          aria-valuetext={label}
          min={floor}
          max={SLIDER_MAX_MINUTES}
          step={SLIDER_STEP_MINUTES}
          list={listId}
          disabled={disabled}
          value={shown}
          onInput={(e) => {
            const m = Number((e.target as HTMLInputElement).value);
            if (Number.isFinite(m)) onChange(minutesToBlocks(m));
          }}
        />
        <button
          type="button"
          class="dslider__step"
          aria-label={`15 minutes more${forName}`}
          disabled={disabled || shown >= SLIDER_MAX_MINUTES}
          onClick={() => step(1)}
        >
          +
        </button>
      </span>
      <datalist id={listId}>
        {ticks.map((m) => (
          <option key={m} value={String(m)} />
        ))}
      </datalist>
    </span>
  );
}
