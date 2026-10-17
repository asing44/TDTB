/* S4 (2026-10-06 cockpit feedback item 4) — re-present capacity for
   scheduling decisions. The operator's binding correction keeps the pie and
   its readout: they are re-presented, not removed. A compact task-room status
   is pinned OUTSIDE the rail's scroll region so the scheduling answer
   (available / over) is visible at every scroll position, while the pie,
   ledger, and keys become quieter supporting evidence.

   The amount is aggregate selectable task room, never a contiguous free
   calendar window — the wording keeps those separate.

   The inspection contract (locked items 8-9: clicking a segment or legend
   item highlights it, clearing restores) is pinned by locked-contract.test.tsx
   and must stay green; the last check here proves it survives inside Rail. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { Rail } from "./Rail";
import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import { budgetTotal, localSelected } from "../store/allocatorView";
import { formatBlockAmount } from "../model/time";

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

function cssBlock(selector: string): string {
  const m = appCss.match(new RegExp(`\\.${selector}\\s*\\{([^}]*)\\}`));
  if (!m) throw new Error(`selector not found in app.css: .${selector}`);
  return m[1];
}

function railStatus(container: HTMLElement): HTMLElement {
  const status = container.querySelector(".rail__status") as HTMLElement | null;
  if (!status) throw new Error("no .rail__status in the rail");
  return status;
}

describe("S4 pinned task-room status", () => {
  it("shows selected / task room and the available amount on a balanced day", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Rail />);
    const s = h.store.getState();
    expect(localSelected(s)).toBeLessThan(budgetTotal(s));
    const status = railStatus(container);
    expect(status.textContent).toContain(
      `${formatBlockAmount(localSelected(s))} selected / ${formatBlockAmount(budgetTotal(s))} task room`,
    );
    expect(status.textContent).toContain(
      `${formatBlockAmount(budgetTotal(s) - localSelected(s))} available`,
    );
  });

  it("reports over-allotment with the reduce/exclude instruction", () => {
    const h = makeHarness("conflict");
    const { container } = h.ui(<Rail />);
    const s = h.store.getState();
    const over = localSelected(s) - budgetTotal(s);
    expect(over).toBeGreaterThan(0);
    const status = railStatus(container);
    expect(status.textContent).toContain(
      `${formatBlockAmount(over)} over — reduce durations or exclude`,
    );
    expect(status.textContent).not.toContain("available");
    expect(status.querySelector(".rail__status-state--over")).toBeTruthy();
  });

  it("holds at zero task room without claiming calendar space", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs.assigned = [];
      sc.inputs.capacity = {
        ...sc.inputs.capacity,
        total: 0,
        fixed: 0,
        anchored: 0,
        habits: 0,
        mint: 0,
        buffer: 0,
        selected: 0,
        free: 0,
        availableForSelection: 0,
        overassigned: false,
      };
    });
    const { container } = h.ui(<Rail />);
    const status = railStatus(container);
    expect(status.textContent).toContain("0 blk selected / 0 blk task room");
    expect(status.textContent).toContain("0 blk available");
    expect(status.textContent).not.toMatch(/free calendar|calendar gap|free window/i);
  });

  it("updates immediately when a row leaves today's selection", () => {
    /* F4a (2026-10-16): the row's inclusion toggle left the collapsed row —
       Skip today is the durable verb and it lands in Skipped today (see
       row-controls.test.tsx). The rail still re-derives its pinned status
       from the store the moment a row stops counting toward today, so the
       legacy local exclusion override remains the deterministic probe for
       that reactivity, without the retired duration slider. */
    const h = makeHarness("ready");
    const { container } = h.ui(
      <>
        <Rail />
        <Queue />
      </>,
    );
    const before = localSelected(h.store.getState());
    // Selection is not budget: checking a row changes nothing in the rail.
    fireEvent.click(
      container.querySelector<HTMLButtonElement>(".trow__check")!,
    );
    expect(localSelected(h.store.getState())).toBe(before);
    act(() => {
      h.store.dispatch({
        type: "OVERRIDE_SET",
        id: "Magic Mirror",
        override: { included: false, blocks: null },
      });
    });
    const s = h.store.getState();
    expect(localSelected(s)).toBeLessThan(before);
    const status = railStatus(container);
    expect(status.textContent).toContain(
      `${formatBlockAmount(localSelected(s))} selected / ${formatBlockAmount(budgetTotal(s))} task room`,
    );
    expect(status.textContent).toContain(
      `${formatBlockAmount(budgetTotal(s) - localSelected(s))} available`,
    );
  });

  it("pins the status outside the scroll region while the evidence stays inside", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Rail />);
    const rail = container.querySelector(".rail") as HTMLElement;
    const scroll = container.querySelector(".rail__scroll") as HTMLElement;
    const status = railStatus(container);
    expect(status.parentElement).toBe(rail);
    expect(scroll.contains(status)).toBe(false);
    // Supporting evidence still scrolls with the rail.
    expect(scroll.querySelector(".rail-capacity")).toBeTruthy();
    expect(scroll.querySelector(".pie")).toBeTruthy();
    expect(scroll.querySelector('[aria-label="Keyboard shortcuts"]')).toBeTruthy();
    // The readiness controls stay pinned after the scroll region.
    expect((rail.lastElementChild as HTMLElement).getAttribute("aria-label")).toBe(
      "Readiness",
    );
  });

  it("quiets the pie by no longer sticking it while keeping inspection reachable", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Rail />);
    // The chart is plain supporting evidence now; the pinned status owns the
    // always-visible slot, and it is never flex-squeezed away.
    expect(cssBlock("rail__pie .pie__chart")).not.toMatch(/position:\s*sticky/);
    expect(cssBlock("rail__status")).toMatch(/flex:\s*none/);
    // Inspection survives inside Rail: a legend button toggles the state and
    // the Clear control restores it (locked items 8-9).
    const legend = container.querySelector(
      ".rail__scroll .pie__legend-item",
    ) as HTMLElement;
    expect(legend).toBeTruthy();
    fireEvent.click(legend);
    expect(legend.getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelector(".pie__clear")).toBeTruthy();
    fireEvent.click(container.querySelector(".pie__clear") as Element);
    expect(legend.getAttribute("aria-pressed")).toBe("false");
  });
});
