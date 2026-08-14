/* FEEDBACK-16 — Enhance the broader Day Setup visual hierarchy. Validates a
   bounded visual enhancement only: each section reads as a distinct grouped
   card in a fixed scan order, the Frame group contains every day-frame field,
   and the terminal action bar keeps Cancel/Save as secondary/primary while
   being visually separated from the last section. FEEDBACK-15 one-focus copy
   and textarea treatment, dialog focus/Escape/focus restore, 12-hour labels,
   and persistence behavior are pinned as preserved contracts. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent } from "@testing-library/preact";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { SetupDrawer } from "./SetupDrawer";
import { useAppState } from "./context";
import { ActionDock } from "./ActionDock";
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

function openDrawer() {
  const h = makeHarness("ready");
  h.store.dispatch({ type: "UI", patch: { setupOpen: true } });
  const r = h.ui(<SetupDrawer />);
  return { h, r };
}

function sectionHeadings(r: ReturnType<ReturnType<typeof makeHarness>["ui"]>) {
  return Array.from(r.container.querySelectorAll(".setup-section h3") as NodeListOf<HTMLElement>).map(
    (el) => el.textContent,
  );
}

/* Mirror App.tsx's drawer lifecycle: drawers mount only while open, so the
   useDialog mount effect (initial focus) runs with the dialog present. */
function DrawerHost() {
  const s = useAppState();
  return (
    <>
      <ActionDock />
      {s.ui.setupOpen && <SetupDrawer />}
    </>
  );
}

describe("FEEDBACK-16 — Day Setup sections have a clear scan order and distinct grouping", () => {
  it("renders each section as a grouped card in fixed scan order", () => {
    const { r } = openDrawer();
    const sections = Array.from(
      r.container.querySelectorAll(".setup-section") as NodeListOf<HTMLElement>,
    );
    expect(sections.length).toBeGreaterThanOrEqual(4);
    expect(sectionHeadings(r)).toEqual([
      "Frame",
      "Anchored blocks",
      "Live micro-adventure",
      "Captures",
    ]);
    for (const section of sections) {
      expect(section.querySelector(".setup-section__head h3")).toBeTruthy();
      expect(section.querySelector(".setup-section__body")).toBeTruthy();
    }
  });

  it("groups the day-frame fields together under the Frame section", () => {
    const { r } = openDrawer();
    const frame = r.container.querySelector(".setup-section") as HTMLElement;
    expect(frame).toBeTruthy();
    for (const label of [
      "Day preset",
      "Work allotment",
      "Start (anchor)",
      "End of day",
      "Buffering",
    ]) {
      const el = r.getByLabelText(label);
      expect(el.closest(".setup-section"), label).toBe(frame);
    }
  });

  it("renders the Mint sessions section between Frame and Anchored blocks when mint sessions exist", () => {
    const h = makeHarness("ready");
    const inputs = h.store.getState().inputs!;
    const sessions = [
      { id: "mint:morning:08:30", name: "Mint Morning · 08:30", slot: "Morning", start: "08:30", end: "09:00" },
      { id: "mint:morning:09:00", name: "Mint Morning · 09:00", slot: "Morning", start: "09:00", end: "09:30" },
    ];
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        daySetup: {
          ...inputs.daySetup,
          workAllotmentMinutes: 60,
          schedulable: { minting: { on: true, sessions: [sessions[0].id] } },
        },
        daySemantics: {
          ...inputs.daySemantics,
          effectiveAllotmentMinutes: 60,
          mintSessions: sessions,
        },
      },
      ledger: h.store.getState().ledger!,
    });
    h.store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const r = h.ui(<SetupDrawer />);

    const headings = sectionHeadings(r);
    const mintIdx = headings.indexOf("Mint sessions");
    expect(mintIdx).toBeGreaterThan(headings.indexOf("Frame"));
    expect(mintIdx).toBeLessThan(headings.indexOf("Anchored blocks"));
  });
});

describe("FEEDBACK-16 — action hierarchy is preserved", () => {
  it("Cancel stays secondary and Save stays primary in the separated action bar", () => {
    const { r } = openDrawer();
    const bar = r.container.querySelector(".setup__actions") as HTMLElement;
    expect(bar).toBeTruthy();
    const cancel = r.getByText("Cancel").closest("button")!;
    expect(cancel.className).toContain("btn");
    expect(cancel.className).not.toContain("btn--primary");
    const save = r.getByText("Save day setup").closest("button")!;
    expect(save.className).toContain("btn--primary");
  });

  it("Cancel closes without persisting; Save persists and closes (behavior unchanged)", async () => {
    const { h, r } = openDrawer();
    const save = vi.spyOn(h.controller, "saveDaySetup").mockResolvedValue();

    fireEvent.click(r.getByText("Cancel"));
    expect(h.store.getState().ui.setupOpen).toBe(false);
    expect(save).not.toHaveBeenCalled();

    const h2 = makeHarness("ready");
    h2.store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const r2 = h2.ui(<SetupDrawer />);
    const save2 = vi.spyOn(h2.controller, "saveDaySetup").mockResolvedValue();
    await act(async () => {
      fireEvent.click(r2.getByText("Save day setup"));
    });
    expect(save2).toHaveBeenCalled();
    expect(h2.store.getState().ui.setupOpen).toBe(false);
  });

  it("Reset to config remains a secondary action inside the Frame group", () => {
    const { r } = openDrawer();
    const reset = r.getByText("Reset to config").closest("button")!;
    expect(reset.className).toContain("btn");
    expect(reset.className).not.toContain("btn--primary");
    expect(reset.closest(".setup-section")?.querySelector("h3")?.textContent).toBe("Frame");
  });
});

describe("FEEDBACK-16 — FEEDBACK-15 copy and textarea treatment remain intact", () => {
  it("keeps the one-focus helper, placeholder, aria wiring, and cap-intention class", () => {
    const { r } = openDrawer();
    expect(r.getByText("One thing to focus on today.")).toBeTruthy();
    const ta = r.getByLabelText("Intention") as HTMLTextAreaElement;
    expect(ta.className).toContain("cap-intention");
    expect(ta.getAttribute("placeholder")?.toLowerCase()).toContain("one thing");
    expect(ta.getAttribute("aria-describedby")).toBe("cap-intention-hint");
  });

  it("keeps every readable textarea state and the narrow no-clip rule in CSS", () => {
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention\s*\{[\s\S]*color:\s*var\(--t-text\)/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention\s*\{[\s\S]*background:\s*var\(--t-surface\)/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention\s*\{[\s\S]*min-height:\s*64px/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention::placeholder\s*\{[\s\S]*color:\s*var\(--t-muted\)/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention:focus-visible\s*\{[\s\S]*outline/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention:disabled\s*\{[\s\S]*cursor:\s*not-allowed/);
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*520px\)[\s\S]*\.field textarea\.cap-intention\s*\{[^}]*flex-basis:\s*100%/,
    );
  });
});

describe("FEEDBACK-16 — dialog focus, Escape, focus restore, and 12-hour labels remain intact", () => {
  it("Day setup keeps initial focus, Escape close, and focus restore", async () => {
    const h = makeHarness("fresh");
    const r = h.ui(<DrawerHost />);
    const setup = r.getByRole("button", { name: "Open day setup" });
    setup.focus();
    await act(async () => {
      fireEvent.click(setup);
    });
    expect(h.store.getState().ui.setupOpen).toBe(true);
    const dialog = r.container.querySelector('[role="dialog"]') as HTMLElement;
    expect(dialog).toBeTruthy();
    expect(dialog.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(dialog, { key: "Escape" });
    await act(async () => {});
    expect(h.store.getState().ui.setupOpen).toBe(false);
    expect(document.activeElement).toBe(setup);
  });

  it("keeps 12-hour user-facing labels in the drawer", () => {
    const { r } = openDrawer();
    const text = r.container.textContent ?? "";
    expect(text).toMatch(/AM|PM/);
    const bare = text.match(/\d{1,2}:\d{2}(?!\s*(AM|PM))/g);
    expect(bare).toBeNull();
  });
});

describe("FEEDBACK-16 — hierarchy CSS and narrow-width no-overlap", () => {
  it("defines the section card, stronger section heading, and action divider", () => {
    expect(APP_CSS).toMatch(/\.setup-section\s*\{[^}]*border:\s*0\.5px solid var\(--t-border\)/);
    expect(APP_CSS).toMatch(/\.setup-section\s*\{[^}]*background:\s*var\(--t-surface-2\)/);
    expect(APP_CSS).toMatch(/\.setup-section__head h3\s*\{[^}]*font-weight:\s*600/);
    expect(APP_CSS).toMatch(/\.setup-section__head h3\s*\{[^}]*color:\s*var\(--t-text\)/);
    expect(APP_CSS).toMatch(/\.setup__actions\s*\{[^}]*border-top:/);
  });

  it("wraps the action bar and anchored controls at narrow widths (no horizontal overflow)", () => {
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*520px\)[\s\S]*\.setup__actions\s*\{[^}]*flex-wrap:\s*wrap/,
    );
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*520px\)[\s\S]*\.anchored-row__controls\s*\{[^}]*flex-wrap:\s*wrap/,
    );
  });
});
