/* FEEDBACK-17 — Repair the quarantined Calendar Impact row layout.
   Presentation-only repair: a quarantined row must render its time, event,
   badge, duration, counted state, reason, and controls without overlap at
   desktop or narrow width. Classification, capacity, projection, and writer
   semantics stay unchanged (FEEDBACK-04/09 contracts), and the FEEDBACK-13
   desktop grid plus narrow re-template remain untouched. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup } from "@testing-library/preact";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { CalendarImpact } from "./CalendarImpact";
import { makeHarness } from "./test-harness";

afterEach(cleanup);

const CSS_CANDIDATES = [
  resolve(process.cwd(), "app.css"),
  resolve(process.cwd(), "frontend", "app.css"),
  resolve(process.cwd(), "src", "app.css"),
];
const CSS_PATH = CSS_CANDIDATES.find((p) => existsSync(p)) ?? CSS_CANDIDATES[0];
const APP_CSS = readFileSync(CSS_PATH, "utf8");

/** Extract the brace-balanced body of the first block whose opening rule
    matches `open` (mirrors responsive.test.ts). */
function extractBlock(css: string, open: RegExp): string | null {
  const start = css.search(open);
  if (start < 0) return null;
  const openIdx = css.indexOf("{", start);
  if (openIdx < 0) return null;
  let depth = 0;
  for (let i = openIdx; i < css.length; i++) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}") {
      depth--;
      if (depth === 0) return css.slice(openIdx + 1, i);
    }
  }
  return null;
}

function quarantinedHarness() {
  const h = makeHarness("ready");
  const inputs = structuredClone(h.store.getState().inputs!);
  inputs.anchored = [
    {
      id: "Work sync", name: "Work sync", kind: "calendar",
      start: "09:30", end: "10:20", durationMin: 50, overlapAllowed: false,
      on: true, skipToday: false, calendarId: "work", calendarTitle: "Trinoor",
      capacityClass: "work",
    },
    {
      id: "Cooking", name: "Cooking", kind: "calendar",
      start: "20:30", end: "21:00", durationMin: 30, overlapAllowed: false,
      on: true, skipToday: false, calendarId: "cooking", calendarTitle: "Personal",
      capacityClass: "fixed",
    },
    {
      id: "Steelers Game", name: "Steelers Game", kind: "calendar",
      start: "20:00", end: "22:00", durationMin: 120, overlapAllowed: false,
      on: true, skipToday: false, calendarId: "sports", calendarTitle: "Sports",
      capacityClass: "quarantined",
    },
  ];
  inputs.capacity.workBusy = 2;
  inputs.capacity.workOverflow = 0;
  h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
  return h;
}

describe("FEEDBACK-17 — quarantined calendar row layout", () => {
  it("renders every quarantined row cell in the documented hierarchy order", () => {
    const h = quarantinedHarness();
    const { getByText, container } = h.ui(<CalendarImpact />);
    // Time, event, badge, duration, counted state, reason, and controls all
    // render for the quarantined row with exact content.
    expect(getByText("8 PM–10 PM")).toBeTruthy();
    expect(getByText("Steelers Game")).toBeTruthy();
    expect(getByText("quarantined")).toBeTruthy();
    expect(getByText("2hr")).toBeTruthy();
    expect(getByText("0 blk counted")).toBeTruthy();
    expect(getByText("Not counted")).toBeTruthy();
    // FEEDBACK-13 hierarchy order: time · event · class · duration · count ·
    // reason · actions — one row of cells, no cell skipped or reordered.
    // (Pin the receiver to the DOM-lib Element type; the harness container
    // loses querySelectorAll typing through ReturnType<typeof render>.)
    const box: Element = container;
    const row = Array.from(box.querySelectorAll(".calendar-impact__row")).find(
      (el) => el.textContent?.includes("Steelers Game"),
    );
    expect(row).toBeTruthy();
    const cellClasses = Array.from(row!.querySelectorAll(":scope > span")).map(
      (el) => el.className.split(" ")[0],
    );
    expect(cellClasses).toEqual([
      "calendar-impact__time",
      "calendar-impact__event",
      "calendar-impact__class",
      "calendar-impact__duration",
      "calendar-impact__count",
      "calendar-impact__reason",
      "calendar-impact__actions",
    ]);
  });

  it("contains the class badge inside its grid track so quarantined cannot overlap the duration cell", () => {
    // The desktop class track is 64px (FEEDBACK-13 contract). "QUARANTINED"
    // at 10px uppercase is ~80px, so without containment the pill overflows
    // into the adjacent duration track and overlaps the "2hr" readout. The
    // badge must cap at its track and ellipsize instead of spilling.
    expect(APP_CSS).toMatch(/\.calendar-impact__class\s*\{[^}]*max-width:\s*100%/);
    expect(APP_CSS).toMatch(/\.calendar-impact__class\s*\{[^}]*overflow:\s*hidden/);
    expect(APP_CSS).toMatch(/\.calendar-impact__class\s*\{[^}]*text-overflow:\s*ellipsis/);
    expect(APP_CSS).toMatch(/\.calendar-impact__class\s*\{[^}]*white-space:\s*nowrap/);
  });

  it("keeps the badge readable and non-overlapping at narrow widths", () => {
    // FEEDBACK-13 narrow re-template places the class badge in the fixed
    // 116px column; containment from the base rule applies there too, so the
    // longest badge stays inside its own column and never bleeds into the
    // flexible column that carries duration/reason.
    const narrow = extractBlock(APP_CSS, /@media\s*\(max-width:\s*767px\)/);
    expect(narrow).toBeTruthy();
    expect(narrow).toMatch(/\.calendar-impact__class\s*\{[^}]*justify-self:\s*start/);
    expect(APP_CSS).toMatch(/\.calendar-impact__class\s*\{[^}]*max-width:\s*100%/);
  });

  it("preserves quarantined classification, capacity, and hard-wall semantics", () => {
    const h = quarantinedHarness();
    const { getAllByText } = h.ui(<CalendarImpact />);
    // Quarantined: badge, Not counted, zero blocks, no hard-block affordance.
    expect(getAllByText("quarantined").length).toBe(1);
    expect(getAllByText("Not counted").length).toBe(1);
    expect(getAllByText("0 blk counted").length).toBe(1);
    // Fixed and work rows keep the hard-wall affordance and accounting.
    expect(getAllByText("hard block").length).toBe(2);
    expect(getAllByText("Inside work budget").length).toBe(1);
    expect(getAllByText("Fixed").length).toBe(1);
    expect(getAllByText("1 blk counted").length).toBe(1);
  });

  it("keeps every visible row time on the authoritative 12-hour display", () => {
    const h = quarantinedHarness();
    const { container } = h.ui(<CalendarImpact />);
    const text = container.textContent ?? "";
    expect(text).toMatch(/AM|PM/);
    expect(text).toMatch(/8 PM–10 PM/);
    const bare = text.match(/\d{1,2}:\d{2}(?!\s*(AM|PM))/g);
    expect(bare).toBeNull();
  });

  it("preserves the FEEDBACK-13 desktop grid and narrow re-template", () => {
    expect(APP_CSS).toMatch(
      /\.calendar-impact__row\s*\{[^}]*grid-template-columns:\s*116px\s+minmax\(160px,\s*1fr\)\s+64px\s+70px\s+92px\s+124px\s+auto/,
    );
    expect(APP_CSS).toMatch(/\.calendar-impact__event\s*\{[^}]*min-width:\s*0/);
    const narrow = extractBlock(APP_CSS, /@media\s*\(max-width:\s*767px\)/);
    expect(narrow).toMatch(
      /\.calendar-impact__row\s*\{[^}]*grid-template-columns:\s*116px\s+minmax\(0,\s*1fr\)/,
    );
    expect(narrow).toMatch(/\.calendar-impact__attend\s*\{[^}]*grid-column:\s*1\s*\/\s*-1/);
  });
});
