/* Compact glyphs for the row's icon buttons (Complete, Expand/Collapse).
   Same 16px stroke style as the unassign trigger; decorative only, the
   button's name and tooltip carry the words. */

import type { ComponentChildren } from "preact";

function Glyph({ children }: { children: ComponentChildren }) {
  return (
    <svg
      class="trow__icon"
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      stroke-width="1.75"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export function CheckIcon() {
  return (
    <Glyph>
      <path d="M3 8.5l3.2 3.2L13 4.8" />
    </Glyph>
  );
}

export function ChevronIcon({ up }: { up: boolean }) {
  return (
    <Glyph>
      <path d={up ? "M3.5 10.5L8 6l4.5 4.5" : "M3.5 5.5L8 10l4.5-4.5"} />
    </Glyph>
  );
}
