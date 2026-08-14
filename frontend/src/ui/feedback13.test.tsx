/* FEEDBACK-13 — validate the bounded UI direction for the five observations:
   (1) intention one-focus helper, (2) readable intention textarea states,
   (3) calendar desktop + narrow hierarchy, (4) results/preview desktop +
   narrow hierarchy, (5) strict 12-hour user-facing time examples.
   Gates (hard-wall, quarantine, zero-writer, local-projection, commit) are
   left untouched — see calendar-capacity.test / components.test. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { CalendarImpact } from "./CalendarImpact";
import { ApprovalDrawer } from "./ApprovalDrawer";
import { SetupDrawer } from "./SetupDrawer";
import { makeHarness } from "./test-harness";

afterEach(cleanup);

// Resolve app.css from the frontend root regardless of vitest cwd.
const CSS_CANDIDATES = [
  resolve(process.cwd(), "app.css"),
  resolve(process.cwd(), "frontend", "app.css"),
  resolve(process.cwd(), "src", "app.css"),
];
const CSS_PATH = CSS_CANDIDATES.find((p) => existsSync(p)) ?? CSS_CANDIDATES[0];
const APP_CSS = readFileSync(CSS_PATH, "utf8");

describe("FEEDBACK-13 obs 1+2 — intention one-focus helper and readable textarea", () => {
  it("shows the one-focus helper and a distinct, readable textarea with focus/aria wiring", () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const r = h.ui(<SetupDrawer />);

    // obs 1: the helper names the single focus for today.
    expect(r.getByText("One thing to focus on today.")).toBeTruthy();

    // obs 2: the intention textarea carries the explicit treatment class and
    // is wired to the helper via aria-describedby.
    const ta = r.getByLabelText("Intention") as HTMLTextAreaElement;
    expect(ta.className).toContain("cap-intention");
    expect(ta.getAttribute("aria-describedby")).toBe("cap-intention-hint");

    // The treatment defines each required state (readable text, surface,
    // border, focus, spacing) in app.css.
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention\s*\{[\s\S]*color:\s*var\(--t-text\)/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention\s*\{[\s\S]*background:\s*var\(--t-surface\)/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention\s*\{[\s\S]*border:\s*1px solid var\(--t-border\)/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention:focus-visible\s*\{[\s\S]*outline/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention\s*\{[\s\S]*min-height:\s*64px/);
  });
});

describe("FEEDBACK-13 obs 5 — strict 12-hour user-facing times", () => {
  it("renders no bare 24-hour time and uses AM/PM (FEEDBACK-12 direction)", () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const r = h.ui(<SetupDrawer />);
    const text = r.container.textContent ?? "";

    // There are visible 12-hour times (anchored rows) — AM/PM must appear.
    expect(text).toMatch(/AM|PM/);

    // A bare HH:MM with no AM/PM suffix (a 24-hour display) must not appear.
    // display12h always appends " AM"/" PM", so any colon-time is followed by
    // the suffix; native <input type=time> values are not textContent.
    const bare = text.match(/\d{1,2}:\d{2}(?!\s*(AM|PM))/g);
    expect(bare).toBeNull();
  });
});

function calendarHarness() {
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
      id: "Fixed appointment", name: "Fixed appointment", kind: "calendar",
      start: "10:00", end: "10:30", durationMin: 30, overlapAllowed: false,
      on: true, skipToday: false, calendarId: "fixed", calendarTitle: "Personal",
      capacityClass: "fixed",
    },
  ];
  inputs.capacity.workBusy = 2;
  inputs.capacity.workOverflow = 0;
  h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
  return h;
}

describe("FEEDBACK-13 obs 3 — calendar hierarchy (desktop + narrow, no overlap)", () => {
  it("renders the full row hierarchy and keeps the flexible column shrinkable", () => {
    const h = calendarHarness();
    const { getByText, getAllByText, container } = h.ui(<CalendarImpact />);

    // Heading + per-row hierarchy: time · event · class · duration · count ·
    // reason · actions.
    expect(getByText("Calendar impact")).toBeTruthy();
    expect(getByText("9:30 AM–10:20 AM")).toBeTruthy();
    expect(getByText("Work sync")).toBeTruthy();
    expect(getByText("work")).toBeTruthy();
    expect(getByText("2 blk counted")).toBeTruthy();
    expect(getByText("Inside work budget")).toBeTruthy();
    // Both in-frame rows are attending, so the exclude verb appears per row.
    expect(getAllByText("Exclude from plan").length).toBeGreaterThan(0);

    // The row uses the documented hierarchy class…
    expect(container.querySelector(".calendar-impact__row")).toBeTruthy();
    // …and the flexible event column carries min-width:0 so it truncates
    // instead of pushing the row past the viewport (no horizontal overlap).
    expect(container.querySelector(".calendar-impact__event")).toBeTruthy();
    expect(APP_CSS).toMatch(/\.calendar-impact__event\s*\{[^}]*min-width:\s*0/);
    // …and the narrow-width re-template is defined.
    expect(APP_CSS).toMatch(/@media\s*\(max-width:\s*767px\)[\s\S]*\.calendar-impact__row/);
  });
});

describe("FEEDBACK-13 obs 4 — results/preview hierarchy (desktop + narrow, no overlap)", () => {
  it("renders surface totals, write rows, and the verification list in the approval drawer", () => {
    const h = makeHarness("commit-preview");
    const r = h.ui(<ApprovalDrawer />);
    const { container } = r;

    expect(container.querySelector(".surface-totals")).toBeTruthy();
    // Exact writes (and thus .write-row name/path) render only after the
    // disclosure is opened — the hierarchy is collapsed by default.
    expect(container.querySelectorAll(".write-row").length).toBe(0);
    fireEvent.click(r.getByRole("button", { name: /Exact writes/ }));
    expect(container.querySelectorAll(".write-row").length).toBeGreaterThan(0);
    expect(container.querySelector(".write-row__name")).toBeTruthy();
    expect(container.querySelector(".write-row__path")).toBeTruthy();
  });

  it("verified commit renders the verification result", () => {
    const h = makeHarness("verified");
    h.store.dispatch({ type: "UI", patch: { approvalOpen: true } });
    const { getByText } = h.ui(<ApprovalDrawer />);
    expect(getByText(/All surfaces verified — zero failures/)).toBeTruthy();
  });

  it("defines a narrow-width hierarchy for the approval drawer (no overlap)", () => {
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*520px\)[\s\S]*\.write-row[\s\S]*\.write-row__name\s*\{[^}]*flex-basis:\s*100%/,
    );
  });
});
