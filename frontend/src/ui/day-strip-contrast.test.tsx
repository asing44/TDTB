/* day-strip-contrast.test.tsx — Day setup touch-ups: cancelled calendar events
   are marked for a hatched treatment, and the theme-aware overrides keep the
   lane/field surfaces off hardcoded light hex. jsdom does no style resolution,
   so the CSS contract is read off disk like strip-cal-work-color.test.ts. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/preact";
import { DayStrip } from "./day-setup/DayStrip";

afterEach(cleanup);

const css = readFileSync(resolve(process.cwd(), "src/ui/day-strip-contrast.css"), "utf8");

function renderStrip(name: string) {
  return render(
    <DayStrip
      frameStart="07:30"
      frameEnd="23:45"
      anchors={[]}
      sessions={[]}
      selectableIds={[]}
      selectedIds={[]}
      onSelectIds={() => {}}
      onToggleSession={() => {}}
      onSelectAnchor={() => {}}
      calendar={[{ id: "ev", name, start: "09:15", durationMin: 30, counted: true }]}
    />,
  );
}

describe("cancelled calendar events", () => {
  it("marks a 'Canceled:' title as cancelled", () => {
    const { container } = renderStrip("Canceled: OPPD Internal Team Meeting");
    expect(container.querySelectorAll(".strip-cal--cancelled")).toHaveLength(1);
  });

  it("leaves an ordinary event alone", () => {
    const { container } = renderStrip("Trinoor Standup");
    expect(container.querySelector(".strip-cal--cancelled")).toBeNull();
  });
});

describe("theme-aware strip and field surfaces", () => {
  it("fills lanes and frame fields from tokens, not hex", () => {
    expect(css).toMatch(/\.day-strip__lane,[^{]*\{\s*background:\s*var\(--t-surface-2\)/);
    expect(css).toMatch(/\.frame-fields \.field select,[^{]*\{[^}]*background-color:\s*var\(--t-surface-2\)/);
    expect(css).not.toMatch(/background[^;]*#(?:f|e|d)[0-9a-f]{5}/i);
  });

  it("keeps disabled fields readable", () => {
    expect(css).toMatch(/input:disabled \{[^}]*opacity:\s*1/);
  });
});
