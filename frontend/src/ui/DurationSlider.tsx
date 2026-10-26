/* DurationSlider — a native range input beside the expanded row's duration
   select. It stages through the same row-draft path as the select and the
   typed custom duration: minutes -> minutesToBlocks -> updateDraft. Nothing is
   committed here; Save/Discard stay the only commit path. */

import { minutesToBlocks } from "../model/parseDuration";
import { blocksLabel } from "../model/time";
import "./duration-slider.css";

/** Finest step matches the Block editor's exact-minutes field (5-minute steps);
    the max matches the largest preset in the duration select (4hr). */
export const SLIDER_MIN_MINUTES = 5;
export const SLIDER_MAX_MINUTES = 240;
export const SLIDER_STEP_MINUTES = 5;

export function DurationSlider({
  blocks,
  disabled,
  title,
  idBase,
  onChange,
}: {
  blocks: number;
  disabled: boolean;
  title?: string;
  idBase: string;
  onChange: (blocks: number) => void;
}) {
  const minutes = Math.round(blocks * 30);
  const shown = Math.min(SLIDER_MAX_MINUTES, Math.max(SLIDER_MIN_MINUTES, minutes));
  const label = blocks > 0 ? blocksLabel(blocks) : "";
  const listId = `dur-ticks-${idBase}`;
  const ticks: number[] = [];
  for (let m = 30; m <= SLIDER_MAX_MINUTES; m += 30) ticks.push(m);
  return (
    <span class="dslider" title={title}>
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
      <datalist id={listId}>
        {ticks.map((m) => (
          <option key={m} value={String(m)} />
        ))}
      </datalist>
      <output class="dslider__label" aria-live="off">
        {label}
      </output>
    </span>
  );
}
