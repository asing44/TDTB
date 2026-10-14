/* duration-exact-minutes.test.tsx — a row whose exact minutes sit below the
   30-minute block grid (a 🚀 10min tag, or a 10-minute remembered value) must
   show, edit, and step at its real length.

   Live symptom (2026-10-12, "Moccamaster cleanout", tag 🚀 10min):
   - the readout said 30min: the backend carried duration_minutes 10 but blocks
     1, and the readout and slider read blocks only. The backend now sends exact
     blocks (10/30) for tag rows; LIVE_ROW below is that post-fix wire shape;
   - the Exact-duration editor pre-filled 30;
   - "−" on a 10-minute row set the row to All day (blocks 0), which removes it
     from the plan.

   jsdom does no layout, so these assert the model, the DOM text, and the
   store, not pixels. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { BlockEditor } from "./BlockEditor";
import { makeHarness } from "./test-harness";
import { projectAssigned } from "../adapters/wire";
import { effectiveBlocks } from "../store/store";
import { HALF_BLOCK, MAX_BLOCKS, stepDownBlocks, stepUpBlocks } from "../model/allocator";

const ID = "Moccamaster cleanout";
const TEN = 10 / 30;
const FIVE = 5 / 30;

/* The live wire row, trimmed to the fields the mapper reads. */
const LIVE_ROW = {
  name: ID,
  path: "todoist://6hjc72hG3rgXwfq6",
  types: ["todoist"],
  urgency: 1,
  deadline: "2026-10-12",
  priority_score: 1.0,
  source: "todoist",
  todoist_id: "6hjc72hG3rgXwfq6",
  labels: ["🚀 10min"],
  blocks: TEN,
  duration_minutes: 10,
  duration_source: "tag:🚀 10min",
};

function stagedWithLiveRow() {
  const item = projectAssigned(LIVE_ROW as never);
  return makeHarness("ready", (sc) => {
    sc.inputs.assigned = [item, ...sc.inputs.assigned.filter((i) => i.id !== item.id)];
  });
}

function rowOf(container: HTMLElement): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find((r) =>
    r.textContent?.includes("Moccamaster"),
  );
  if (!row) throw new Error("row not rendered: Moccamaster");
  return row;
}

describe("exact minutes below the block grid", () => {
  it("the mapper keeps the tag's 10 minutes as a fractional block", () => {
    const item = projectAssigned(LIVE_ROW as never);
    expect(item.blocks).toBeCloseTo(TEN, 6);
    expect(item.durationLabel).toBe("10min");
  });

  it("the readout shows 10min, not the rounded 30min", () => {
    const h = stagedWithLiveRow();
    const { container } = h.ui(<Queue />);
    const readout = rowOf(container).querySelector(".alloc-track__value")?.textContent ?? "";
    expect(readout).toMatch(/^10min/);
    expect(readout).not.toContain("30min");
  });

  it("Exact duration pre-fills 10 minutes, not 30", () => {
    const h = stagedWithLiveRow();
    h.store.dispatch({ type: "UI", patch: { editorItem: ID, editorIntent: "duration" } });
    const { container } = h.ui(<BlockEditor />);
    const input = container.querySelector("#editor-minutes") as HTMLInputElement | null;
    expect(input?.value).toBe("10");
  });

  it("← on a 10-minute row steps to 5 minutes, never to All day", () => {
    const h = stagedWithLiveRow();
    h.controller.setOverride(ID, true, TEN);
    const { container } = h.ui(<Queue />);
    // Planning 2a (2026-10-14): the − stepper is gone; the row's ← key walks
    // the same stepDownBlocks grid it used.
    fireEvent.keyDown(rowOf(container), { key: "ArrowLeft" });
    const s = h.store.getState();
    expect(effectiveBlocks(s, ID)).toBeCloseTo(FIVE, 6);
    expect(effectiveBlocks(s, ID)).toBeGreaterThan(0);
  });

  it("← on a 15-minute row still walks to All day (the designed bottom notch)", () => {
    const h = stagedWithLiveRow();
    h.controller.setOverride(ID, true, HALF_BLOCK);
    const { container } = h.ui(<Queue />);
    fireEvent.keyDown(rowOf(container), { key: "ArrowLeft" });
    expect(effectiveBlocks(h.store.getState(), ID)).toBe(0);
    // The readout names the state the stepper's "all day" label used to
    // (the * marks today's session override).
    expect(
      rowOf(container).querySelector<HTMLElement>(".alloc-track__value")!.textContent,
    ).toBe("All day*");
    expect(
      rowOf(container).querySelector<HTMLInputElement>(".alloc-track__input")!.disabled,
    ).toBe(true);
  });
});

describe("stepUpBlocks", () => {
  it("+ adds exactly 15 minutes to an off-grid value, clamped at the top", () => {
    expect(stepUpBlocks(TEN)).toBeCloseTo(25 / 30, 6);
    expect(stepUpBlocks(MAX_BLOCKS)).toBe(MAX_BLOCKS);
  });
});

describe("stepDownBlocks", () => {
  it("walks the grid down as before: 30min → 15min → All day", () => {
    expect(stepDownBlocks(1)).toBe(HALF_BLOCK);
    expect(stepDownBlocks(HALF_BLOCK)).toBe(0);
  });

  it("a sub-15 off-grid value walks down in 5-minute steps, then to All day", () => {
    expect(stepDownBlocks(TEN)).toBeCloseTo(FIVE, 6);
    expect(stepDownBlocks(FIVE)).toBe(0);
  });

  it("a 20min value drops by 15 to 5min, not to All day", () => {
    expect(stepDownBlocks(20 / 30)).toBeCloseTo(FIVE, 6);
  });

  it("holds at zero", () => {
    expect(stepDownBlocks(0)).toBe(0);
  });
});
