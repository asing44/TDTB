/* RowIcon — 2026-10-13 icon pass. Operator request: the row buttons keep
   their exact functionality and tooltips ("row button functionality same but
   with icons instead of words but still tooltip hover"), but the face is an
   icon instead of a word.

   The accessible name stays on the button's own aria-label, and the Tooltip
   bubble carries label + detail; every glyph here is decorative, so it is
   aria-hidden. The icon never becomes the accessible name.

   Inline SVG, no dependencies: 16px, viewBox 24, stroke-based, with
   `stroke="currentColor"` so each icon inherits its button's colour and the
   light/dark tokens do the theming. Each glyph is sized by CSS
   (`.row-icon`), not by the button. */

import type { ComponentChildren } from "preact";

export type RowIconName =
  | "check"
  | "eye"
  | "eye-off"
  | "clock"
  | "blocks"
  | "undo"
  | "ellipsis";

const PATHS: Record<RowIconName, ComponentChildren> = {
  /* Mark complete. */
  check: <polyline points="20 6 9 17 4 12" />,
  /* Include today — the row is visible in the plan again. */
  eye: (
    <>
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  /* Exclude today — hidden for the day, reversible. */
  "eye-off": (
    <>
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
      <line x1="1" y1="1" x2="23" y2="23" />
    </>
  ),
  /* Exact duration. */
  clock: (
    <>
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </>
  ),
  /* Duration / blocks — a stack of bars that shortens as the blocks drop. */
  blocks: (
    <>
      <line x1="3" y1="6" x2="21" y2="6" />
      <line x1="3" y1="12" x2="15" y2="12" />
      <line x1="3" y1="18" x2="9" y2="18" />
    </>
  ),
  /* Leave as-is — hand the item back to its source untouched. */
  undo: (
    <>
      <polyline points="9 14 4 9 9 4" />
      <path d="M20 20v-7a4 4 0 0 0-4-4H4" />
    </>
  ),
  /* More actions. */
  ellipsis: (
    <>
      <circle cx="5" cy="12" r="1" />
      <circle cx="12" cy="12" r="1" />
      <circle cx="19" cy="12" r="1" />
    </>
  ),
};

export function RowIcon({ name }: { name: RowIconName }) {
  return (
    <svg
      class="row-icon"
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
