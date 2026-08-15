/* feedback10.test.tsx — FEEDBACK-10 (2026-08-14): queue hierarchy, action
   clarity, and allocation-state legibility. Red tests for the FEEDBACK-07
   design-direction annotations A08 (duration source), A09 (over caption),
   A10 (remaining/over-capacity hierarchy), A11 (collapse state),
   A12 (tooltip affordances), A13 (More menu carries secondary actions),
   A14 (opaque layered menu), A15 (band wording).

   jsdom does no real style resolution, so the CSS-level guards read app.css
   off disk the same way the a11y/responsive suites read tokens.css/app.css.

   Note: Array.from(...).find(...) chained calls hit a tsgo inference quirk,
   so list queries assign to a variable first, mirroring the codebase style. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { AllocationPie } from "./AllocationPie";
import { makeHarness, type Harness } from "./test-harness";
import { BANDS } from "../model/bands";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

function cssBlock(selector: string): string {
  const m = appCss.match(new RegExp(`\\.${selector}\\s*\\{([^}]*)\\}`));
  if (!m) throw new Error(`selector not found in app.css: .${selector}`);
  return m[1];
}

function menuTriggers(container: Element, name: string): HTMLElement {
  const triggers = Array.from(
    container.querySelectorAll('button[aria-haspopup="menu"]'),
  ) as HTMLElement[];
  const trigger = triggers.find((b) =>
    b.getAttribute("aria-label")?.includes(name),
  );
  if (!trigger) throw new Error(`no More trigger for ${name}`);
  return trigger;
}

function menuItems(container: Element): HTMLElement[] {
  return Array.from(container.querySelectorAll('[role="menuitem"]')) as HTMLElement[];
}

describe("FEEDBACK-10 critical-band wording (A15)", () => {
  it("band notes are concise and non-alarmist while preserving urgency", () => {
    const byKey = new Map(BANDS.map((b) => [b.key, b]));
    expect(byKey.get("crit")!.note).toBe("do today");
    expect(byKey.get("high")!.note).toBe("soon");
    expect(byKey.get("else")!.note).toBe("when room");
    for (const b of BANDS) {
      expect(b.note).not.toMatch(/failed|alarm|disaster|slipped/i);
    }
  });
});

describe("FEEDBACK-10 collapsed state is unmistakable (A11)", () => {
  it("collapsed band says so in words and announces the hidden count", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const btn = container.querySelector(".band")?.closest("button") as HTMLButtonElement;
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    // AT: the label names the state and the hidden count.
    expect(btn.getAttribute("aria-label")).toMatch(/collapsed/);
    expect(btn.getAttribute("aria-label")).toMatch(/hidden/);
    // Sighted: a visible state word, not only a chevron.
    expect(btn.textContent).toMatch(/collapsed/i);
    expect(btn.textContent).toMatch(/hidden/i);
    // Expanding reverses both.
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    expect(btn.getAttribute("aria-label")).toMatch(/expanded/);
    expect(btn.getAttribute("aria-label")).not.toMatch(/hidden/);
  });

  it("the Dropped today header announces collapsed state the same way", () => {
    const h = makeHarness("sequenced", (sc) => {
      sc.inputs.droppedToday = [
        { identity: "p1", name: "Press", droppedAt: "2026-08-13T08:00:00Z" },
      ];
    });
    const { container } = h.ui(<Queue />);
    const btn = container.querySelector(".band--drop")?.closest("button") as HTMLButtonElement;
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(btn.getAttribute("aria-label")).toMatch(/collapsed/);
    expect(btn.getAttribute("aria-label")).toMatch(/1 row hidden/);
    expect(btn.textContent).toMatch(/collapsed/i);
  });
});

describe("FEEDBACK-10 over-capacity hierarchy (A10)", () => {
  it("shows a strong over-capacity summary above the bands when selected exceeds capacity", () => {
    const h = makeHarness("conflict");
    const { container } = h.ui(<Queue />);
    const summary = container.querySelector(".queue__remaining") as HTMLElement;
    expect(summary).toBeTruthy();
    expect(summary.textContent).toMatch(/blk selected of \d+ capacity/);
    expect(summary.textContent).toMatch(/\d+ over/);
    expect(summary.className).toContain("queue__remaining--over");
  });

  it("renders no over-capacity summary on a balanced day", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    expect(container.querySelector(".queue__remaining")).toBeNull();
  });
});

describe("FEEDBACK-10 flagged rows keep a readable hierarchy (LP-01)", () => {
  it("would-drop rows carry an explicit badge and keep their name readable", () => {
    const h = makeHarness("conflict");
    const { container } = h.ui(<Queue />);
    const flagged = Array.from(container.querySelectorAll(".qrow--flagged")) as HTMLElement[];
    expect(flagged.length).toBeGreaterThan(0);
    for (const row of flagged) {
      expect(row.querySelector(".qrow__drop")?.textContent).toBe("would drop");
      const name = row.querySelector(".qrow__name");
      expect(name?.textContent?.trim().length).toBeGreaterThan(0);
    }
  });

  it("flagged and excluded rows never wash out via whole-row opacity", () => {
    // LP-01: gray washout made over-capacity rows hard to read. The state
    // must come from hierarchy (tint + badge + muted-but-readable text), not
    // a blanket opacity drop on the whole row.
    expect(cssBlock("qrow--flagged")).not.toMatch(/opacity\s*:/);
    expect(cssBlock("qrow--excluded")).not.toMatch(/opacity\s*:/);
  });
});

describe("FEEDBACK-10 More menu carries secondary actions (A13)", () => {
  function openMore(h: Harness, name: string) {
    const { container } = h.ui(<Queue />);
    const trigger = menuTriggers(container, name);
    fireEvent.click(trigger);
    return { container, trigger };
  }

  it("the row no longer renders the placement verb directly", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const buttons = Array.from(container.querySelectorAll("button")) as HTMLElement[];
    const direct = buttons.some(
      (b) => b.getAttribute("aria-label") === "Place Magic Mirror at a specific time",
    );
    expect(direct).toBe(false);
  });

  it("Place at a specific time lives in the More menu and opens the exact editor", () => {
    const h = makeHarness("ready");
    const { container } = openMore(h, "Magic Mirror");
    const item = menuItems(container).find(
      (b) => b.getAttribute("aria-label") === "Place Magic Mirror at a specific time",
    );
    expect(item).toBeTruthy();
    fireEvent.click(item as Element);
    expect(h.store.getState().ui.editorItem).toBe("Magic Mirror");
    expect(h.store.getState().ui.editorIntent).toBe("place");
  });

  it("Unschedule lives in the More menu for scheduled rows and releases placement", () => {
    const h = makeHarness("sequenced");
    const { container } = openMore(h, "Magic Mirror");
    const item = menuItems(container).find(
      (b) => b.getAttribute("aria-label") === "Unschedule Magic Mirror",
    );
    expect(item).toBeTruthy();
    fireEvent.click(item as Element);
    expect(
      h.store
        .getState()
        .sequence?.some((r) => r.id === "Magic Mirror" && r.kind === "work"),
    ).toBe(false);
  });

  it("Unassign and Delete stay in the More menu (frozen verb model)", () => {
    const h = makeHarness("ready");
    const { container } = openMore(h, "Magic Mirror");
    const labels = menuItems(container).map((b) => b.getAttribute("aria-label"));
    expect(labels.some((l) => l?.startsWith("Unassign"))).toBe(true);
    expect(labels.some((l) => l?.startsWith("Delete permanently"))).toBe(true);
  });
});

describe("FEEDBACK-10 More menu keyboard access (A14)", () => {
  it("Arrow keys move focus between menu items; Escape closes and refocuses the trigger", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const trigger = menuTriggers(container, "Magic Mirror");
    fireEvent.click(trigger);
    const items = menuItems(container);
    expect(items.length).toBeGreaterThan(0);
    items[0].focus();
    fireEvent.keyDown(items[0], { key: "ArrowDown" });
    expect(document.activeElement).toBe(items[1]);
    fireEvent.keyDown(items[1], { key: "ArrowUp" });
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(trigger);
  });
});

describe("FEEDBACK-10 More menu is an opaque, layered surface (A14)", () => {
  it("solid surface + border + elevation above the table in both themes", () => {
    const menu = cssBlock("row-more__menu");
    expect(menu).toMatch(/background:\s*var\(--t-surface\)/);
    expect(menu).toMatch(/border:\s*0\.5px\s+solid\s+var\(--t-border\)/);
    expect(menu).toMatch(/z-index:\s*\d+/);
  });
});

describe("FEEDBACK-10 icon actions expose hover/focus tooltips (A12)", () => {
  it("hover shows the tooltip, mouse leave hides it", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const btn = container.querySelector(
      'button[aria-label="Exclude Press today"]',
    ) as HTMLElement;
    expect(container.querySelector('[role="tooltip"]')).toBeNull();
    fireEvent.mouseEnter(btn);
    expect(container.querySelector('[role="tooltip"]')?.textContent).toBe(
      "Exclude today",
    );
    fireEvent.mouseLeave(btn);
    expect(container.querySelector('[role="tooltip"]')).toBeNull();
  });

  it("keyboard focus shows the tooltip — no title-only dependence", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const btn = container.querySelector(
      'button[aria-label="Exclude Press today"]',
    ) as HTMLElement;
    fireEvent.focus(btn);
    expect(container.querySelector('[role="tooltip"]')?.textContent).toBe(
      "Exclude today",
    );
    fireEvent.blur(btn);
    expect(container.querySelector('[role="tooltip"]')).toBeNull();
  });

  it("the exact-duration icon exposes its label on focus too", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const btn = container.querySelector(
      'button[aria-label="Exact duration for Magic Mirror"]',
    ) as HTMLElement;
    fireEvent.focus(btn);
    expect(container.querySelector('[role="tooltip"]')?.textContent).toBe(
      "Exact duration",
    );
  });
});

describe("FEEDBACK-10 remembered duration is visible (A08)", () => {
  it("a default row names its duration source as 'source'", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const slider = container.querySelector(
      'input[aria-label="Note Processing duration in 15-minute steps"]',
    ) as HTMLInputElement;
    expect(slider.getAttribute("aria-valuetext")).toMatch(/\(source\)$/);
    const chip = slider
      .closest(".qrow")!
      .querySelector(".qrow__src-tag") as HTMLElement;
    expect(chip.textContent).toBe("source");
  });

  it("an overridden row names its duration source as 'memory'", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const slider = container.querySelector(
      'input[aria-label="Note Processing duration in 15-minute steps"]',
    ) as HTMLInputElement;
    fireEvent.input(slider, { target: { value: "2" } });
    const refreshed = container.querySelector(
      'input[aria-label="Note Processing duration in 15-minute steps"]',
    ) as HTMLInputElement;
    expect(refreshed.getAttribute("aria-valuetext")).toMatch(/\(memory\)$/);
    const chip = refreshed
      .closest(".qrow")!
      .querySelector(".qrow__src-tag") as HTMLElement;
    expect(chip.textContent).toBe("memory");
    expect(chip.className).toContain("qrow__src-tag--memory");
  });
});

describe("FEEDBACK-10 allocation overflow is explicit in the pie (A09)", () => {
  it("renders an over-capacity caption when allocation exceeds the day", () => {
    const h = makeHarness("conflict");
    const { container } = h.ui(<AllocationPie />);
    const caption = container.querySelector(".pie__over-caption");
    expect(caption?.textContent).toMatch(/Over by \d+ blk/);
  });

  it("renders no over caption on a balanced day", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<AllocationPie />);
    expect(container.querySelector(".pie__over-caption")).toBeNull();
  });
});

describe("PI-CHART-02: exhausted day renders the pie with explicit overage", () => {
  it("renders slices and a zero-capacity overage without clock examples", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs.capacity = {
        ...sc.inputs.capacity, total: 0, free: 0, overassigned: true,
      };
    });
    const { container } = h.ui(<AllocationPie />);
    const svg = container.querySelector("svg.pie__svg");
    expect(svg).not.toBeNull();
    const paths = container.querySelectorAll("path.pie__slice");
    expect(paths.length).toBeGreaterThan(0);
    const readout = container.querySelector(".pie__readout");
    expect(readout?.textContent).toMatch(/0 blk capacity/);
    expect(readout?.textContent).toMatch(/over/i);
    // Zero-capacity state is named in blocks, never as 24-hour clock examples.
    expect(readout?.textContent).not.toMatch(/\d{1,2}:\d{2}/);
    const caption = container.querySelector(".pie__over-caption");
    expect(caption?.textContent).toMatch(/Over by \d+ blk/);
  });

  it("keeps the pie absent when an exhausted day has no allocations", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs.assigned = [];
      sc.inputs.capacity = {
        ...sc.inputs.capacity,
        total: 0, fixed: 0, anchored: 0, habits: 0, mint: 0, selected: 0,
        buffer: 0, free: 0, overassigned: false,
      };
    });
    const { container } = h.ui(<AllocationPie />);
    expect(container.querySelector("svg.pie__svg")).toBeNull();
  });
});

describe("FEEDBACK-12 queue scheduled rows render 12-hour", () => {
  it("a scheduled row's start reads as 12-hour with AM/PM", () => {
    const h = makeHarness("sequenced");
    const { container } = h.ui(<Queue />);
    const rows = Array.from(container.querySelectorAll(".qrow")) as HTMLElement[];
    const aws = rows.find((el) => el.textContent?.includes("Review AWS module 4"));
    expect(aws?.textContent).toMatch(/· 12:30 PM/);
    const mirror = rows.find((el) => el.textContent?.includes("Magic Mirror"));
    expect(mirror?.textContent).toMatch(/· 10:45 AM/);
  });

  it("a recurring row's scheduledStart reads as 12-hour with AM/PM", () => {
    const h = makeHarness("ready");
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.assigned.push({
      id: "LOOTS", name: "LOOTS", path: null, source: "todoist",
      types: ["todoist"], urgency: null, deadline: inputs.validDate,
      priorityScore: 1, blocks: 1, durationLabel: "30min",
      todoistId: "loots-1", isRecurring: true, scheduledStart: "12:30",
      labels: [],
    });
    h.store.dispatch({
      type: "INPUTS_LOADED", inputs,
      ledger: { today: inputs.validDate, spent: 0, cap: 5, remaining: 5 },
    });
    const { container } = h.ui(<Queue />);
    const rows = Array.from(container.querySelectorAll(".qrow")) as HTMLElement[];
    const loots = rows.find((el) => el.textContent?.includes("LOOTS"));
    expect(loots?.textContent).toMatch(/· fixed · recurring · 12:30 PM/);
  });

  it("a dropped row's label reads as a 12-hour time", () => {
    const h = makeHarness("sequenced", (sc) => {
      sc.inputs.droppedToday = [
        { identity: "p1", name: "Press", droppedAt: "2026-08-13T08:00:00Z" },
      ];
    });
    const { container } = h.ui(<Queue />);
    const dropped = container.querySelector(".queue__dropped-at");
    expect(dropped?.textContent).toMatch(/dropped \d{1,2}(:\d{2})? (AM|PM)/);
  });
});
