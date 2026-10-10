/* FEEDBACK-17 — quarantined calendar detail layout (re-pointed to the U10
   "Already placed" timeline). A quarantined block must open a detail row that
   renders its time, event, badge, duration, counted state, reason, and
   controls without overlap at desktop or narrow width. Classification,
   capacity, projection, and writer semantics stay unchanged (FEEDBACK-04/09
   contracts), and the timeline desktop layout plus narrow re-template hold. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";
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

function openBlock(
  r: ReturnType<ReturnType<typeof makeHarness>["ui"]>,
  name: string,
) {
  fireEvent.click(r.getByRole("button", { name: new RegExp(name) }));
}

describe("FEEDBACK-17 — quarantined calendar detail layout", () => {
  it("renders every quarantined detail cell in the documented hierarchy order", () => {
    const h = quarantinedHarness();
    const r = h.ui(<CalendarImpact />);
    openBlock(r, "Steelers Game");
    // Time, badge, duration, counted state, and reason all render with exact
    // content in the inline detail row.
    expect(r.getByText("8 PM–10 PM")).toBeTruthy();
    // The name appears twice — the timeline block label and the detail row.
    expect(r.getAllByText("Steelers Game").length).toBeGreaterThan(0);
    expect(r.getByText("quarantined")).toBeTruthy();
    expect(r.getByText("2hr")).toBeTruthy();
    expect(r.getByText("0 blk counted")).toBeTruthy();
    expect(r.getByText("Not counted")).toBeTruthy();
    // Hierarchy order: time · class · reason · count — one row of cells, no
    // cell skipped or reordered.
    const meta: Element = r.container.querySelector(".timeline__detail-meta")!;
    const cellClasses = Array.from(meta.querySelectorAll(":scope > span")).map(
      (el) => el.className.split(" ")[0],
    );
    expect(cellClasses).toEqual([
      "timeline__detail-time",
      "calendar-impact__class",
      "timeline__detail-reason",
      "timeline__detail-count",
    ]);
  });

  it("contains the class badge inside its own cell so quarantined cannot overlap the duration readout", () => {
    // The badge caps at its cell and ellipsizes instead of spilling into the
    // adjacent duration cell ("QUARANTINED" at 10px uppercase is ~80px wide).
    expect(APP_CSS).toMatch(/\.calendar-impact__class\s*\{[^}]*max-width:\s*100%/);
    expect(APP_CSS).toMatch(/\.calendar-impact__class\s*\{[^}]*overflow:\s*hidden/);
    expect(APP_CSS).toMatch(/\.calendar-impact__class\s*\{[^}]*text-overflow:\s*ellipsis/);
    expect(APP_CSS).toMatch(/\.calendar-impact__class\s*\{[^}]*white-space:\s*nowrap/);
  });

  it("preserves quarantined classification, capacity, and hard-wall semantics", () => {
    const h = quarantinedHarness();
    const r = h.ui(<CalendarImpact />);
    // Quarantined: badge, Not counted, zero blocks, no hard-block affordance.
    openBlock(r, "Steelers Game");
    expect(r.getByText("quarantined")).toBeTruthy();
    expect(r.getByText("Not counted")).toBeTruthy();
    expect(r.getByText("0 blk counted")).toBeTruthy();
    expect(r.queryByText("hard block")).toBeNull();
    // Fixed and work rows keep the hard-wall affordance and accounting.
    openBlock(r, "Cooking");
    expect(r.getByText("Fixed")).toBeTruthy();
    expect(r.getByText("1 blk counted")).toBeTruthy();
    expect(r.getByText("hard block")).toBeTruthy();
    openBlock(r, "Work sync");
    expect(r.getByText("Inside work budget")).toBeTruthy();
    expect(r.getByText("hard block")).toBeTruthy();
  });

  it("keeps every visible row time on the authoritative 12-hour display", () => {
    const h = quarantinedHarness();
    const r = h.ui(<CalendarImpact />);
    openBlock(r, "Steelers Game");
    const meta = r.container.querySelector(".timeline__detail-meta")?.textContent ?? "";
    expect(meta).toMatch(/8 PM–10 PM/);
    const bare = meta.match(/\d{1,2}:\d{2}(?!\s*(AM|PM))/g);
    expect(bare).toBeNull();
  });

  it("preserves the timeline desktop layout and narrow re-template", () => {
    expect(APP_CSS).toMatch(/\.timeline__track\s*\{[^}]*position:\s*relative/);
    expect(APP_CSS).toMatch(/\.timeline__track\s*\{[^}]*min-width:\s*0/);
    expect(APP_CSS).toMatch(/\.timeline__block\s*\{[^}]*position:\s*absolute/);
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*767px\)[\s\S]*\.timeline__lane-label\s*\{[^}]*flex-basis:\s*62px/,
    );
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*767px\)[\s\S]*\.timeline__detail\s*\{[^}]*flex-wrap:\s*wrap/,
    );
  });
});
