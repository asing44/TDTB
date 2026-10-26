/* DurationSlider — the row's inline duration control: the readout above a
   compact [−] range [+] strip (restored from the earlier plan-row design:
   readout above the slider, 15-minute steppers flanking it, a steady track).
   It lives in the collapsed row's duration cell and stays there while the row
   is expanded, so a row never carries two sliders. It stages through the same
   row-draft path as the select and the typed custom duration: minutes ->
   minutesToBlocks -> updateDraft. Nothing is committed here; Save/Discard stay
   the only commit path. The cell is a size container (duration-slider.css):
   steppers hide first, then the track, leaving the readout with its tooltip. */

import { minutesToBlocks } from "../model/parseDuration";
import { blocksLabel } from "../model/time";
import "./duration-slider.css";

/** Finest step matches the Block editor's exact-minutes field (5-minute steps);
    the max matches the largest preset in the duration select (4hr). */
export const SLIDER_MIN_MINUTES = 5;
export const SLIDER_MAX_MINUTES = 240;
export const SLIDER_STEP_MINUTES = 5;
/** The −/+ steppers move a quarter hour, the earlier design's half block. */
export const STEPPER_MINUTES = 15;

/** Clamp a stepped minute value to the slider's range. */
export function stepMinutes(minutes: number, direction: 1 | -1): number {
  const next = minutes + direction * STEPPER_MINUTES;
  return Math.min(SLIDER_MAX_MINUTES, Math.max(SLIDER_MIN_MINUTES, next));
}

export function DurationSlider({
  blocks,
  disabled,
  title,
  idBase,
  name,
  onChange,
}: {
  blocks: number;
  disabled: boolean;
  title?: string;
  idBase: string;
  /** The row's name, carried by the stepper labels. */
  name?: string;
  onChange: (blocks: number) => void;
}) {
  const minutes = Math.round(blocks * 30);
  const shown = Math.min(SLIDER_MAX_MINUTES, Math.max(SLIDER_MIN_MINUTES, minutes));
  const label = blocksLabel(blocks);
  const listId = `dur-ticks-${idBase}`;
  const ticks: number[] = [];
  for (let m = 30; m <= SLIDER_MAX_MINUTES; m += 30) ticks.push(m);
  const forName = name ? ` for ${name}` : "";
  return (
    <span class="dslider" title={title ?? `Duration ${label}`}>
      <output class="dslider__label" aria-live="off">
        {label}
      </output>
      <span class="dslider__ctl">
        <button
          type="button"
          class="dslider__step"
          aria-label={`15 minutes less${forName}`}
          disabled={disabled || minutes <= SLIDER_MIN_MINUTES}
          onClick={() => onChange(minutesToBlocks(stepMinutes(shown, -1)))}
        >
          −
        </button>
        <input
          class="dslider__range"
          type="range"
          aria-label="Duration slider"
          aria-valuetext={label}
          min={SLIDER_MIN_MINUTES}
          max={SLIDER_MAX_MINUTES}
          step={SLIDER_STEP_MINUTES}
          list={listId}
          disabled={disabled}
          value={shown}
          onInput={(e) => {
            const m = Number((e.target as HTMLInputElement).value);
            if (Number.isFinite(m) && m > 0) onChange(minutesToBlocks(m));
          }}
        />
        <button
          type="button"
          class="dslider__step"
          aria-label={`15 minutes more${forName}`}
          disabled={disabled || minutes >= SLIDER_MAX_MINUTES}
          onClick={() => onChange(minutesToBlocks(stepMinutes(shown, 1)))}
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
