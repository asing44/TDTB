/* sources-details.test — the Sources details popover (owner request: "more
   information on source refreshing"). Covers open/close (button + Escape +
   outside click), keyboard focus, aria-expanded, the three source rows, the
   skipped-objects list with and without the additive ``skipped`` field, the
   relative-age readout, and that the chip's refresh action survives the new
   details affordance. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent } from "@testing-library/preact";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

import { ReadinessStrip } from "./ReadinessStrip";
import { makeHarness } from "./test-harness";
import { fingerprintFixedInputs } from "../model/fingerprint";
import { fixedInputsOf, type Scenario } from "../fixtures/scenarios";
import type { CapacitiesIntake } from "../model/types";

const NOW = new Date("2026-10-13T15:00:00");
const INSTALLED_MS = NOW.getTime() - 20 * 60_000;

const SKIP_ID = "capacities:space-1:structure-projects:obj-1234567890";

function skippedRow(overrides: Record<string, unknown> = {}) {
  return {
    identity: SKIP_ID,
    title: "Draft launch brief",
    reason: "missing title",
    detail: "no title property",
    structureId: "custom-project",
    structureName: "Projects",
    ...overrides,
  };
}

function intake(overrides: Partial<CapacitiesIntake> = {}): CapacitiesIntake {
  return {
    mode: "direct",
    state: "degraded",
    generation: 7,
    installedAt: INSTALLED_MS / 1000,
    typeCheckTimes: {
      "custom-project": INSTALLED_MS / 1000,
      "9f8e7d6c5b4a39281706f5e4d3c2b1a0": (INSTALLED_MS - 3 * 3_600_000) / 1000,
    },
    coverage: { members: 12, evaluated: 9, malformed: 2, unreadable: 1 },
    unassignedCandidates: [],
    skipped: [skippedRow()],
    ...overrides,
  };
}

function stage(mutate?: (sc: Scenario) => void) {
  return makeHarness("ready", (sc) => {
    sc.inputs.sourceCounts = { todoist: 12, calendar: 5 };
    sc.inputs.capacitiesIntake = intake();
    mutate?.(sc);
  });
}

function openSources(h: ReturnType<typeof makeHarness>) {
  const r = h.ui(<ReadinessStrip />);
  fireEvent.click(r.getByRole("button", { name: "Source details" }));
  return r;
}

function refreshOk(h: ReturnType<typeof makeHarness>, at: string) {
  const inputs = h.store.getState().inputs!;
  act(() => {
    h.store.dispatch({
      type: "SOURCE_REFRESH_OK",
      inputs,
      ledger: h.store.getState().ledger!,
      fingerprint: fingerprintFixedInputs(fixedInputsOf(inputs)),
      anchoredSourceFingerprint: inputs.anchoredSourceFingerprint,
      planningConfigFingerprint: inputs.planningConfigFingerprint,
      at,
    });
  });
}

beforeEach(() => {
  vi.useFakeTimers({ now: NOW });
});

interface StubRect {
  top: number;
  left: number;
  width: number;
  height: number;
}

function setViewport(width: number, height: number): void {
  Object.defineProperty(window, "innerWidth", { value: width, configurable: true });
  Object.defineProperty(window, "innerHeight", { value: height, configurable: true });
}

/** Stub layout for the details trigger and the popover panel. The trigger
    object is read by reference so a test can move it before a resize event. */
function stubRects(triggerRect: StubRect, panel: { width: number; height: number }): void {
  const make = (r: StubRect) => ({
    x: r.left,
    y: r.top,
    top: r.top,
    left: r.left,
    width: r.width,
    height: r.height,
    right: r.left + r.width,
    bottom: r.top + r.height,
    toJSON: () => ({}),
  });
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
    this: Element,
  ) {
    if (this.classList?.contains("rail__sources-pop")) {
      return make({ top: 0, left: 0, width: panel.width, height: panel.height }) as DOMRect;
    }
    if (this.classList?.contains("rail__sources-toggle")) {
      return make({ ...triggerRect }) as DOMRect;
    }
    return make({ top: 0, left: 0, width: 0, height: 0 }) as DOMRect;
  });
}

describe("sources details popover", () => {
  it("opens and closes from the details button, tracking aria-expanded", () => {
    const h = stage();
    const r = h.ui(<ReadinessStrip />);
    const details = r.getByRole("button", { name: "Source details" });
    expect(details.getAttribute("aria-expanded")).toBe("false");
    expect(r.queryByRole("dialog")).toBeNull();
    fireEvent.click(details);
    expect(details.getAttribute("aria-expanded")).toBe("true");
    expect(r.getByRole("dialog")).toBeTruthy();
    fireEvent.click(details);
    expect(details.getAttribute("aria-expanded")).toBe("false");
    expect(r.queryByRole("dialog")).toBeNull();
  });

  it("clicking the chip still refreshes, while the details button does not", () => {
    const h = stage();
    const spy = vi.spyOn(h.controller, "refreshSources").mockResolvedValue(undefined as never);
    const r = h.ui(<ReadinessStrip />);
    fireEvent.click(r.getByRole("button", { name: "Refresh sources" }));
    expect(spy).toHaveBeenCalledTimes(1);
    fireEvent.click(r.getByRole("button", { name: "Source details" }));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("moves focus into the panel on open and returns it on Escape", () => {
    const h = stage();
    const r = h.ui(<ReadinessStrip />);
    const details = r.getByRole("button", { name: "Source details" });
    details.focus();
    fireEvent.click(details);
    const dialog = r.getByRole("dialog");
    expect(document.activeElement).toBe(dialog);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(r.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(details);
    expect(details.getAttribute("aria-expanded")).toBe("false");
  });

  it("closes on an outside click", () => {
    const h = stage();
    const r = openSources(h);
    expect(r.getByRole("dialog")).toBeTruthy();
    fireEvent.mouseDown(document.body);
    expect(r.queryByRole("dialog")).toBeNull();
  });

  it("shows Todoist/Calendar counts, the live-refresh label, and no-session fallback", () => {
    const h = stage();
    const r = openSources(h);
    expect(r.getByText("12 tasks · 5 events")).toBeTruthy();
    expect(r.getByText("Not refreshed in this session")).toBeTruthy();
    expect(r.getByText(/Fetched live on each refresh/)).toBeTruthy();
  });

  it("shows the client refresh time as relative age plus clock", () => {
    const h = stage();
    const r = h.ui(<ReadinessStrip />);
    refreshOk(h, "2026-10-13T14:45:00");
    fireEvent.click(r.getByRole("button", { name: "Source details" }));
    expect(r.getByText("Refreshed 15 min ago (2:45 PM)")).toBeTruthy();
  });

  it("shows the Capacities generation, coverage counts, and per-type check times", () => {
    const h = stage();
    const r = openSources(h);
    expect(r.getByText(/Cached generation 7, installed 20 min ago/)).toBeTruthy();
    expect(
      r.getByText("9 of 12 cached objects checked · 2 skipped · 1 unreadable"),
    ).toBeTruthy();
    expect(r.getByText(/Read from cache — Refresh re-reads Capacities/)).toBeTruthy();
    expect(r.getByText(/Structures last checked: .*custom-project/)).toBeTruthy();
    // A long structure id is shown short; a short key is shown verbatim.
    expect(r.getByText(/9f8e7d6c/)).toBeTruthy();
    expect(r.queryByText(/9f8e7d6c5b4a39281706f5e4d3c2b1a0/)).toBeNull();
  });

  it("renders the skipped list without raw ids and keeps identity in the tooltip", () => {
    const h = stage();
    const r = openSources(h);
    expect(r.getByText(/Draft launch brief/)).toBeTruthy();
    expect(r.getByText(/missing title/)).toBeTruthy();
    const item = r.getByText("Projects").closest("li") as HTMLElement;
    expect(item).toBeTruthy();
    // The tooltip carries the short identity; the visible row must not.
    expect(item.getAttribute("title")).toBe(SKIP_ID.slice(0, 8));
    expect(item.textContent).not.toContain(SKIP_ID.slice(0, 8));
    expect(item.textContent).not.toContain("capacities:");
  });

  it("shows labels, an edited date, and the hint under a skipped row", () => {
    const h = stage((sc) => {
      sc.inputs.capacitiesIntake = intake({
        skipped: [
          skippedRow({
            title: null,
            labels: ["P1", "Idle"],
            updatedAt: "2026-10-10T12:00:00.000Z",
            hint: "Description · Cadence:",
          }),
        ],
      });
    });
    const r = openSources(h);
    const edited = new Date("2026-10-10T12:00:00.000Z").toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
    const clues = r.getByText(`P1 · Idle · edited ${edited} · "Description · Cadence:"`);
    expect(clues).toBeTruthy();
    expect(clues.className).toContain("rail__sources-skipped-clues");
  });

  it("shows only the parts it has, and nothing extra when there are no clues", () => {
    const h = stage((sc) => {
      sc.inputs.capacitiesIntake = intake({
        skipped: [
          skippedRow({ identity: `${SKIP_ID}-l`, labels: ["Inbox"] }),
          skippedRow({ identity: `${SKIP_ID}-n` }),
        ],
      });
    });
    const r = openSources(h);
    expect(r.getByText("Inbox")).toBeTruthy();
    const rows = Array.from(
      r.container.querySelectorAll(".rail__sources-skipped-clues"),
    );
    expect(rows).toHaveLength(1);
  });

  it("keeps the panel on screen for a trigger near the bottom", () => {
    setViewport(900, 700);
    stubRects({ top: 600, left: 20, width: 24, height: 20 }, { width: 340, height: 500 });
    const r = openSources(stage());
    const panel = r.getByRole("dialog") as HTMLElement;
    const top = parseFloat(panel.style.top);
    const maxHeight = parseFloat(panel.style.maxHeight);
    expect(panel.style.left).toBe("20px");
    expect(maxHeight).toBe(500);
    expect(top).toBeGreaterThanOrEqual(8);
    expect(top + maxHeight).toBeLessThanOrEqual(700 - 8);
  });

  it("opens downward and stays on screen for a trigger near the top", () => {
    setViewport(900, 700);
    stubRects({ top: 20, left: 20, width: 24, height: 20 }, { width: 340, height: 500 });
    const r = openSources(stage());
    const panel = r.getByRole("dialog") as HTMLElement;
    const top = parseFloat(panel.style.top);
    const maxHeight = parseFloat(panel.style.maxHeight);
    expect(top).toBeGreaterThanOrEqual(40);
    expect(top).toBeGreaterThanOrEqual(8);
    expect(top + maxHeight).toBeLessThanOrEqual(700 - 8);
  });

  it("recomputes placement on resize while open", () => {
    setViewport(900, 700);
    const triggerRect = { top: 600, left: 20, width: 24, height: 20 };
    stubRects(triggerRect, { width: 340, height: 500 });
    const r = openSources(stage());
    const panel = r.getByRole("dialog") as HTMLElement;
    expect(parseFloat(panel.style.top)).toBe(94);
    triggerRect.top = 20;
    act(() => {
      window.dispatchEvent(new Event("resize"));
    });
    expect(parseFloat(panel.style.top)).toBe(46);
  });

  it("shows at most eight skipped objects, then an overflow count", () => {
    const h = stage((sc) => {
      sc.inputs.capacitiesIntake = intake({
        skipped: Array.from({ length: 11 }, (_, i) =>
          skippedRow({ identity: `${SKIP_ID}-${i}`, title: `Row ${i}`, reason: "no type" }),
        ),
      });
    });
    const r = openSources(h);
    expect(r.getByText("Row 7")).toBeTruthy();
    expect(r.queryByText("Row 8")).toBeNull();
    expect(r.getByText("and 3 more")).toBeTruthy();
  });

  it("falls back to the verbatim warnings when the server omits skipped", () => {
    const warning = "Capacities partial — 9 evaluated · 3 deferred across structures.";
    const h = stage((sc) => {
      sc.inputs.capacitiesIntake = intake({ skipped: undefined });
      sc.inputs.sourceWarnings = [warning];
    });
    const r = openSources(h);
    expect(r.getAllByText(warning).length).toBeGreaterThanOrEqual(2);
  });

  it("spells out the next action for degraded, deferred, and clean states", () => {
    const degraded = openSources(stage());
    expect(
      degraded.getByText(
        "These do not block planning but are left out of the plan. Fix or delete them in Capacities, then Refresh.",
      ),
    ).toBeTruthy();
    degraded.unmount();

    const deferred = openSources(
      stage((sc) => {
        sc.inputs.capacitiesIntake = intake({ skipped: undefined });
      }),
    );
    expect(deferred.getByText("Refresh again to evaluate the rest.")).toBeTruthy();
    deferred.unmount();

    const clean = openSources(
      stage((sc) => {
        sc.inputs.capacitiesIntake = intake({
          state: "ok",
          skipped: undefined,
          coverage: { members: 6, evaluated: 6, malformed: 0, unreadable: 0 },
        });
      }),
    );
    expect(clean.getByText("Nothing needs attention.")).toBeTruthy();
  });

  it("keeps the skipped-object action visible and advisory when the read is ok", () => {
    const h = stage((sc) => {
      sc.inputs.capacitiesIntake = intake({
        state: "ok",
        coverage: { members: 6, evaluated: 6, malformed: 1, unreadable: 0 },
      });
    });
    const r = openSources(h);
    expect(
      r.getByText(
        "These do not block planning but are left out of the plan. Fix or delete them in Capacities, then Refresh.",
      ),
    ).toBeTruthy();
  });

  it("appends the relative age to the rail readout only past ten minutes", () => {
    const h = stage();
    const r = h.ui(<ReadinessStrip />);
    const status = () => (r.container.querySelector(".rail__refresh") as HTMLElement).textContent;
    refreshOk(h, "2026-10-13T14:55:00");
    expect(status()).not.toMatch(/min ago/);
    refreshOk(h, "2026-10-13T14:45:00");
    expect(status()).toContain("15 min ago");
  });

  it("puts the date and relative age in the chip's aria-label", () => {
    const h = stage();
    const r = h.ui(<ReadinessStrip />);
    refreshOk(h, "2026-10-13T14:45:00");
    const chip = r.getByRole("button", { name: /Refresh sources/ });
    expect(chip.getAttribute("aria-label")).toMatch(/Oct 13/);
    expect(chip.getAttribute("aria-label")).toMatch(/15 min ago/);
  });
});
