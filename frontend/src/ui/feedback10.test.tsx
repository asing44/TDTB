/* feedback10.test.tsx — FEEDBACK-10 (2026-08-14): queue hierarchy, action
   clarity, and allocation-state legibility. Red tests for the FEEDBACK-07
   design-direction annotations A08 (duration source), A09 (over caption),
   A10 (remaining/over-capacity hierarchy), A11 (collapse state),
   A12 (tooltip affordances), A13 (More menu carries secondary actions),
   A14 (opaque layered menu), A15 (band wording).

   SUPERSEDED 2026-10-06 (cockpit feedback item 2, operator-approved): LP-01's
   would-drop badge and A09's "trim or drop" wording are gone. The cockpit
   reports actual over-allotment and the operator's own keep / exclude /
   complete decisions; it never proposes an automatic drop. The checks in the
   over-allotment describe below replace the old badge expectation with
   strictly stronger guarantees.

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
import { UNASSIGN_TRIGGER_LABEL } from "./ConfirmUnassign";
import { AllocationPie } from "./AllocationPie";
import { ActionDock } from "./ActionDock";
import { makeHarness, type Harness } from "./test-harness";
import { BANDS } from "../model/bands";
import { pieSlices } from "../model/pie";
import { effectiveBlocks, includedItems } from "../store/store";
import { budgetTotal, localSelected } from "../store/allocatorView";
import { formatBlockAmount } from "../model/time";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

function cssBlock(selector: string): string {
  const m = appCss.match(new RegExp(`\\.${selector}\\s*\\{([^}]*)\\}`));
  if (!m) throw new Error(`selector not found in app.css: .${selector}`);
  return m[1];
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
    // Scope to an urgency band: the calendar band leads the priority stack
    // and also carries .band, so an unscoped first match would silently
    // retarget this A11 test away from the band header it pins.
    const btn = container.querySelector(".queue__band .band")?.closest("button") as HTMLButtonElement;
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
});

describe("FEEDBACK-10 over-capacity hierarchy (A10)", () => {
  it("shows a strong over-capacity summary above the bands when selected exceeds capacity", () => {
    const h = makeHarness("conflict");
    const { container } = h.ui(<Queue />);
    // U11: the retired .queue__remaining line reported the budget-model
    // overage (localSelected - budgetTotal). That arithmetic now renders as
    // the rail's pinned task-room status inside the capacity card.
    const summary = container.querySelector(".rail__status-state") as HTMLElement;
    expect(summary).toBeTruthy();
    expect(summary.className).toContain("rail__status-state--over");
    expect(summary.textContent).toMatch(/\d+ blk over — reduce durations or exclude/);
    const ratio = container.querySelector(".rail__status-ratio") as HTMLElement;
    expect(ratio.textContent).toMatch(/\d+ blk selected \/ \d+ blk task room/);
  });

  it("renders no over-capacity summary on a balanced day", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    expect(container.querySelector(".rail__status-state--over")).toBeNull();
  });
});

/* FEEDBACK-10 LP-01's would-drop badge is SUPERSEDED (operator-approved,
   2026-10-06 cockpit feedback item 2). These checks replace the badge
   expectation with strictly stronger guarantees: nothing is marked for
   removal, no hypothetical acceptance instruction is shown, overbooking
   leaves inclusion exactly as the operator left it, the reported overage is
   the arithmetic the chart draws, and excluded rows stay readable. */
describe("over-allotment is reported, never converted into a proposed drop (supersedes FEEDBACK-10 LP-01)", () => {
  it("marks no candidate row and renders no would-drop badge", () => {
    const h = makeHarness("conflict");
    const { container } = h.ui(<Queue />);
    expect(container.querySelector(".qrow--flagged")).toBeNull();
    expect(container.querySelector(".qrow__drop")).toBeNull();
    expect(container.textContent).not.toMatch(/would drop/i);
    // The concept is gone from the stylesheet too, so it cannot come back as
    // a colour-only cue that a glance (or a screen reader) would miss.
    expect(appCss).not.toMatch(/qrow--flagged/);
    expect(appCss).not.toMatch(/qrow__drop/);
    // Names stay readable on every rendered row.
    const rows = Array.from(container.querySelectorAll(".qrow")) as HTMLElement[];
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      const name = row.querySelector(".qrow__name");
      expect((name?.textContent ?? "").trim().length).toBeGreaterThan(0);
    }
  });

  it("leaves inclusion untouched when the day is overbooked", () => {
    const h = makeHarness("conflict");
    const assigned = h.store.getState().inputs!.assigned.map((i) => i.id);
    expect(includedItems(h.store.getState()).map((i) => i.id)).toEqual(assigned);
    // A local duration edit pushes the day further over. That is an overage
    // to report, not a row to drop.
    h.store.dispatch({
      type: "OVERRIDE_SET",
      id: "Magic Mirror",
      override: { included: true, blocks: 12 },
    });
    const s = h.store.getState();
    expect(includedItems(s).map((i) => i.id)).toEqual(assigned);
    expect(s.overrides["Magic Mirror"]).toEqual({ included: true, blocks: 12 });

    const { container } = h.ui(<Queue />);
    const over = localSelected(s) - budgetTotal(s);
    expect(over).toBeGreaterThan(0);
    const ratio = container.querySelector(".rail__status-ratio") as HTMLElement;
    expect(ratio.textContent).toBe(
      `${formatBlockAmount(localSelected(s))} selected / ${formatBlockAmount(budgetTotal(s))} task room`,
    );
    const summary = container.querySelector(".rail__status-state") as HTMLElement;
    expect(summary.textContent).toBe(
      `${formatBlockAmount(over)} over — reduce durations or exclude`,
    );
    // Every assigned row still renders: nothing was quietly removed.
    expect(container.querySelectorAll(".qrow").length).toBeGreaterThanOrEqual(
      assigned.length,
    );
  });

  it("reports actual selected/budget/overage instead of a hypothetical acceptance", () => {
    // `ready` + extra load = confirmed setup, no sequence staged, over budget:
    // the dock state that used to offer "Accept the trim…".
    const h = makeHarness("ready", (sc) => {
      sc.inputs.assigned.push({
        id: "Deep CWEAN", name: "Deep CWEAN",
        path: null,
        source: "todoist", types: ["task"], urgency: null, deadline: null,
        priorityScore: 47, blocks: 4, durationLabel: "2hr", todoistId: "fb9DEEP",
      });
    });
    const { container } = h.ui(<ActionDock />);
    const s = h.store.getState();
    expect(includedItems(s).map((i) => i.id)).toEqual(s.inputs!.assigned.map((i) => i.id));
    const over = localSelected(s) - budgetTotal(s);
    expect(over).toBeGreaterThan(0);
    const status = container.querySelector(".dock__status") as HTMLElement;
    expect(status.textContent).toBe(
      `${formatBlockAmount(localSelected(s))} selected of ${formatBlockAmount(budgetTotal(s))} capacity - ${formatBlockAmount(over)} over.`,
    );
    expect(status.textContent).not.toMatch(/accept/i);
    expect(status.textContent).not.toMatch(/trim/i);
    expect(status.textContent).not.toMatch(/drop/i);
  });

  it("still names the actual selected blocks and capacity on a balanced day", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<ActionDock />);
    const s = h.store.getState();
    const status = container.querySelector(".dock__status") as HTMLElement;
    expect(status.textContent).toBe(
      `Setup confirmed. Sequence ${formatBlockAmount(localSelected(s))} of ${formatBlockAmount(budgetTotal(s))} when ready.`,
    );
  });

  it("excluded rows stay readable: the state is marked by name, never washed out", () => {
    const h = makeHarness("conflict");
    h.store.dispatch({
      type: "OVERRIDE_SET",
      id: "Press",
      override: { included: false, blocks: null },
    });
    const { container } = h.ui(<Queue />);
    // Planning 2a: excluded rows live in the collapsed Out today lane; expand
    // it before reading the row that is actually rendered there.
    fireEvent.click(container.querySelector(".band--drop") as HTMLButtonElement);
    const rows = Array.from(container.querySelectorAll(".qrow")) as HTMLElement[];
    const press = rows.find(
      (r) => r.className.includes("qrow--excluded") && r.textContent?.includes("Press"),
    );
    expect(press).toBeTruthy();
    expect(press!.querySelector(".qrow__name")?.textContent).toBe("Press");
    // LP-01 safety, kept: state comes from the muted name, never a blanket
    // opacity drop on the whole row.
    expect(cssBlock("qrow--excluded")).not.toMatch(/opacity\s*:/);
    expect(appCss).toMatch(/\.qrow--excluded \.qrow__name\s*\{[^}]*var\(--t-muted\)/);
  });
});

describe("FEEDBACK-10 secondary actions left the row (A13, rewritten for F7)", () => {
  /* F7 retired the per-row More menu. Its items live on the expanded row:
     Adjust time / Unschedule are the Start control (Set time / Clear), and the
     source verbs are the Source menu. */
  function expandRow(h: Harness, name: string) {
    const { container } = h.ui(<Queue />);
    const row = () =>
      Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
        (r) => r.querySelector(".qrow__name")?.textContent === name,
      )!;
    fireEvent.click(row().querySelector('button[aria-label="Expand"]')!);
    return { container, row };
  }

  it("the row no longer renders the placement verb or a More menu", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const buttons = Array.from(container.querySelectorAll("button")) as HTMLElement[];
    expect(
      buttons.some((b) => b.getAttribute("aria-label") === "Adjust time for Magic Mirror"),
    ).toBe(false);
    expect(container.querySelector('button[aria-haspopup="menu"]')).toBeNull();
    expect(container.querySelector(".row-more")).toBeNull();
  });

  it("an exact start is the expanded row's Set time and does not open the block editor", () => {
    const h = makeHarness("ready");
    const { row } = expandRow(h, "Magic Mirror");
    const setTime = Array.from(row().querySelectorAll<HTMLButtonElement>("button")).find(
      (b) => b.textContent?.trim() === "Set time",
    )!;
    fireEvent.click(setTime);
    expect(row().querySelector(".trow__editor")).not.toBeNull();
    expect(h.store.getState().ui.editorItem).toBeNull();
  });

  /* Regression guard for the F7 defect fixed in `store/controller.ts`:
     Clear stages `pin: null`, and `rowDraft.toOverrides` projects that onto
     `resetPlacement`. ROW_PIN_RESET alone only drops the pending pin and never
     touches `placements`, and `placements[id]` IS the pin (`rowSessionOf`), so
     Save after Clear used to leave the row "Pinned 11:00". Save now routes a
     cleared existing pin through `controller.releasePlacement` (ROW_PIN_RESET +
     ROW_UNPLACED, which deletes `placements[id]`). */
  it("Clear unschedules a placed row (Save releases the placement)", () => {
    const h = makeHarness("sequenced");
    const { row } = expandRow(h, "Magic Mirror");
    const btn = (t: string) =>
      Array.from(row().querySelectorAll<HTMLButtonElement>("button")).find(
        (b) => b.textContent?.trim() === t,
      )!;
    fireEvent.click(btn("Set time"));
    fireEvent.input(row().querySelector('input[type="time"]')!, { target: { value: "11:00" } });
    fireEvent.click(btn("Apply"));
    fireEvent.click(btn("Save"));
    expect(h.store.getState().placements["Magic Mirror"]).toBe("11:00");
    fireEvent.click(btn("Clear"));
    fireEvent.click(btn("Save"));
    expect(h.store.getState().placements["Magic Mirror"]).toBeUndefined();
  });

  it("source removal and Delete are Source-menu items, not row controls", () => {
    const h = makeHarness("ready");
    const { row } = expandRow(h, "Magic Mirror");
    fireEvent.click(row().querySelector(".source-menu__trigger")!);
    const labels = Array.from(
      row().querySelectorAll<HTMLElement>('[role="menuitem"]'),
    ).map((b) => b.getAttribute("aria-label"));
    expect(
      labels.some((l) => l?.startsWith("Unassign in source") || l?.startsWith("Skip occurrence")),
    ).toBe(false);
    expect(labels).toContain("Hide always");
    expect(labels).toContain("Delete source…");
  });
});

describe("FEEDBACK-10 Source menu keyboard access (A14, rewritten for F7)", () => {
  it("Arrow keys move focus between menu items; Escape closes and refocuses the trigger", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
      (r) => r.querySelector(".qrow__name")?.textContent === "Magic Mirror",
    )!;
    fireEvent.click(row.querySelector('button[aria-label="Expand"]')!);
    const trigger = row.querySelector<HTMLElement>(".source-menu__trigger")!;
    fireEvent.click(trigger);
    const items = Array.from(row.querySelectorAll<HTMLElement>('[role="menuitem"]'));
    expect(items.length).toBeGreaterThan(1);
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(items[0], { key: "ArrowDown" });
    expect(document.activeElement).toBe(items[1]);
    fireEvent.keyDown(items[1], { key: "ArrowUp" });
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(items[0], { key: "Escape" });
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

describe("FEEDBACK-10 More-menu items keep their own height (A14)", () => {
  it("the row-verb 31px cap does not apply inside the More menu", () => {
    const item = cssBlock("qrow__actions .row-more__menu .alloc-verb");
    expect(item).toMatch(/height:\s*auto/);
    expect(item).toMatch(/min-height:\s*44px/);
  });
});

describe("FEEDBACK-10 allocation overflow is explicit in the pie (A09)", () => {
  it("names the exact overage and the operator's own choices, never a proposed drop", () => {
    const h = makeHarness("conflict");
    const { container } = h.ui(<AllocationPie />);
    const s = h.store.getState();
    const selectedNow = includedItems(s).reduce(
      (sum, i) => sum + effectiveBlocks(s, i.id),
      0,
    );
    // The caption's number must be the same allocation the wedges draw.
    const slices = pieSlices(s.capacity, 0, 0, 0, selectedNow);
    const allocated = slices
      .filter((x) => x.key !== "unallocated")
      .reduce((sum, x) => sum + x.blocks, 0);
    const over = Math.max(0, allocated - (s.capacity?.total ?? 0));
    expect(over).toBeGreaterThan(0);
    const caption = container.querySelector(".pie__over-caption") as HTMLElement;
    expect(caption.textContent).toBe(
      `Over by ${formatBlockAmount(over)} - reduce durations or exclude`,
    );
    // Explicit choices, not a hypothetical automatic drop.
    expect(caption.textContent).not.toMatch(/drop/i);
    expect(caption.textContent).not.toMatch(/trim/i);
    expect(caption.getAttribute("role")).toBe("status");
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
    const caption = container.querySelector(".pie__over-caption") as HTMLElement;
    expect(caption.textContent).toMatch(/Over by \d+ blk/);
    expect(caption.textContent).toMatch(/reduce durations or exclude/i);
    expect(caption.textContent).not.toMatch(/drop/i);
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
});

describe("row action buttons describe themselves on hover", () => {
  it("the row carries no hover-only More control any more", () => {
    const { container } = makeHarness("ready").ui(<Queue />);
    expect(container.querySelector('button[aria-label^="More actions for"]')).toBeNull();
  });

  it("the collapsed row's own buttons carry their words in plain text", () => {
    // F4a/U4: Mark complete and Leave as-is were icon faces whose words lived
    // in a hover bubble. The collapsed row's Complete / Unassign in Todoist…
    // controls are labelled buttons, so the words never depend on a hover, and
    // the room self-describes its scope without a tooltip.
    const { container } = makeHarness("ready").ui(<Queue />);
    const press = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
      (r) => r.querySelector(".qrow__name")?.textContent === "Press",
    );
    const complete = press?.querySelector(".trow__btn--primary") as HTMLElement;
    expect(complete.textContent).toContain("Complete");
    expect(complete.getAttribute("title")).toBeNull();
    const skip = Array.from(press!.querySelectorAll(".trow__btn")).find((b) =>
      b.getAttribute("aria-label") === UNASSIGN_TRIGGER_LABEL,
    ) as HTMLElement;
    expect(skip).toBeTruthy();
    // U4: the label names the source the write reaches. The face is an icon,
    // so the name lives in aria-label and the tooltip.
    expect(skip.textContent).toBe("");
    expect(skip.querySelector("svg")).toBeTruthy();
    expect(skip.getAttribute("title")).toBe(UNASSIGN_TRIGGER_LABEL);
  });
});
