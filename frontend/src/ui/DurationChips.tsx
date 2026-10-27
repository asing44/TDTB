/* DurationChips — one-tap duration for the expanded row: a single-row chip
   group (role="radiogroup") of quick durations plus All-day. A click stages
   through the row-draft path the slider and the typed field already use
   (the caller's onPickMinutes / onPickAllDay); nothing is committed here. An
   off-grid value (a 10-minute tag estimate, a typed 25m) adds one extra
   "Custom: 25m" chip so the group always shows what is staged.

   Radio semantics: the checked chip is the one tab stop (the first enabled
   chip when none is checked); arrow keys move AND pick, Home/End jump. */

import type { JSX } from "preact";
import "./duration-ux.css";

/** The quick durations, in minutes. 15 and 45 are not 30-minute block
    multiples; they stage as fractional blocks via minutesToBlocks, exactly as
    the slider's quarter-hour notches do. */
export const QUICK_MINUTES = [15, 30, 45, 60, 90, 120];

/** Compact chip text: 60 → "1h", 90 → "1h30", 45 → "45m". */
export function chipLabel(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m === 0 ? `${h}h` : `${h}h${String(m).padStart(2, "0")}`;
}

interface Chip {
  key: string;
  /** The chip's duration in minutes; absent for All-day and Custom. */
  minutes?: number;
  label: string;
  checked: boolean;
  disabled: boolean;
  title?: string;
  /** Skipped by arrow navigation: the Custom chip only reports state. */
  inert?: boolean;
  pick: () => void;
}

export function DurationChips({
  idBase,
  blocks,
  allDay,
  disabled,
  allDayWhy,
  allDayWhyId,
  onPickMinutes,
  onPickAllDay,
}: {
  idBase: string;
  /** The draft's duration in 30-minute blocks (kept while all-day). */
  blocks: number;
  allDay: boolean;
  /** Runtime busy. */
  disabled: boolean;
  /** Why All-day is closed for this row, or null when it is open. */
  allDayWhy: string | null;
  /** Id of the element carrying the visible reason text, for aria-describedby. */
  allDayWhyId?: string;
  onPickMinutes: (minutes: number) => void;
  onPickAllDay: () => void;
}) {
  const minutes = Math.round(blocks * 30);
  const onGrid = QUICK_MINUTES.includes(minutes);
  const chips: Chip[] = QUICK_MINUTES.map((m) => ({
    key: String(m),
    minutes: m,
    label: chipLabel(m),
    checked: !allDay && m === minutes,
    disabled,
    pick: () => onPickMinutes(m),
  }));
  if (!allDay && !onGrid && minutes > 0) {
    chips.push({
      key: "custom",
      minutes,
      label: `Custom: ${chipLabel(minutes)}`,
      checked: true,
      disabled,
      inert: true,
      pick: () => {},
    });
  }
  chips.push({
    key: "allday",
    label: "All-day",
    checked: allDay,
    disabled: disabled || allDayWhy !== null,
    title: allDayWhy ?? undefined,
    pick: onPickAllDay,
  });

  const tabStop =
    chips.find((c) => c.checked && !c.disabled)?.key ??
    chips.find((c) => !c.disabled)?.key;

  const onKeyDown = (e: JSX.TargetedKeyboardEvent<HTMLButtonElement>, from: number) => {
    const nav = chips.map((c, i) => ({ c, i })).filter(({ c }) => !c.inert && !c.disabled);
    if (nav.length === 0) return;
    let target: { c: Chip; i: number } | undefined;
    const pos = nav.findIndex(({ i }) => i === from);
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      // From the inert Custom chip, "next" is the first chip after it.
      target = pos >= 0 ? nav[(pos + 1) % nav.length] : nav.find(({ i }) => i > from) ?? nav[0];
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      target =
        pos >= 0
          ? nav[(pos - 1 + nav.length) % nav.length]
          : [...nav].reverse().find(({ i }) => i < from) ?? nav[nav.length - 1];
    } else if (e.key === "Home") {
      target = nav[0];
    } else if (e.key === "End") {
      target = nav[nav.length - 1];
    } else {
      return;
    }
    e.preventDefault();
    const group = e.currentTarget.parentElement;
    target.c.pick();
    group?.querySelector<HTMLElement>(`[data-chip="${target.c.key}"]`)?.focus();
  };

  return (
    <div class="dux-chips" role="radiogroup" aria-label="Duration" id={`dux-${idBase}`}>
      {chips.map((c, i) => (
        <button
          key={c.key}
          type="button"
          role="radio"
          class={`dux-chip${c.checked ? " dux-chip--on" : ""}${c.inert ? " dux-chip--custom" : ""}`}
          data-chip={c.key}
          data-minutes={c.minutes === undefined ? undefined : String(c.minutes)}
          aria-checked={c.checked}
          aria-describedby={c.key === "allday" && allDayWhy ? allDayWhyId : undefined}
          title={c.title}
          disabled={c.disabled}
          tabIndex={c.key === tabStop ? 0 : -1}
          onClick={c.pick}
          onKeyDown={(e) => onKeyDown(e, i)}
        >
          {c.label}
        </button>
      ))}
    </div>
  );
}
