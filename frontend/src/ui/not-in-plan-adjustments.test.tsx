/* not-in-plan-adjustments.test.tsx — a LOCAL row in the "Not in plan" drawer
   carries the same adjustment controls as a plan row: the inline duration
   slider with its provenance badge, the May-overlap-Mint toggle, and the
   Expand chevron that mounts RowExpanded. Only the green confirm check is
   absent, because a row that is not in the plan has nothing to complete today.

   The drawer row is the same row object: `overrides`, `mintOverlapOptIns` and
   `ui.expandedId` are keyed by the item id, so every edit works while the row
   is out of the plan and survives the row being added back.

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";

import { Queue } from "./Queue";
import { makeHarness, type Harness } from "./test-harness";

afterEach(cleanup);

/** A sourced row that starts inside the plan and belongs to no commitment
    tier: 30 minutes, no deadline, no urgency. */
const ROW = "Entryway Design";

/** The drawer section, by its live id. */
function drawer(container: Element): HTMLElement {
  const section = container.querySelector<HTMLElement>("#unassigned-today");
  if (!section) throw new Error("no Not in plan drawer");
  return section;
}

/** Any row by its name, wherever it currently renders. */
function rowFor(root: Element, name: string): HTMLElement {
  const row = Array.from(root.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

/** The drawer's own row for `name` — the state under test here. */
function drawerRow(container: Element, name: string): HTMLElement {
  return rowFor(drawer(container), name);
}

/** Move a row out of today's plan the way the operator does: the E key. The
    milestone is the drawer's own membership write, not a synthetic state. */
async function removeFromPlan(h: Harness, container: Element, name: string) {
  fireEvent.keyDown(rowFor(container, name), { key: "e" });
  await waitFor(() =>
    expect(Object.keys(h.store.getState().planMembership).length).toBe(1),
  );
  await waitFor(() => expect(drawerRow(container, name)).not.toBeNull());
}

const durationCell = (row: Element) =>
  row.querySelector<HTMLElement>(".trow__dur--slider")!;
const range = (row: Element) =>
  row.querySelector<HTMLInputElement>('input[aria-label="Duration slider"]')!;
const readout = (row: Element) => row.querySelector(".dslider__label")?.textContent;
/** The duration's provenance chip: "Today" once a local edit has landed. */
const badge = (row: Element) =>
  row.querySelector(".dux-badge")?.getAttribute("data-state");
const mint = (row: Element) =>
  row.querySelector<HTMLButtonElement>('button[class*="trow__mint"]')!;
const expandBtn = (row: Element) =>
  row.querySelector<HTMLButtonElement>('button[aria-label="Expand"]')!;

describe("a not-in-plan row carries the plan row's adjustments", () => {
  it("renders the duration slider, the Mint toggle and the chevron", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    await removeFromPlan(h, container, ROW);
    const row = drawerRow(container, ROW);

    expect(durationCell(row)).not.toBeNull();
    // The slider is the same control a plan row carries: same notches, same
    // readout, same provenance badge beside it.
    expect([range(row).min, range(row).max, range(row).step]).toEqual(["0", "240", "15"]);
    expect(readout(row)).toBe("30min");
    expect(badge(row)).toBe("source");
    expect(mint(row).textContent).toBe("Mint");
    expect(expandBtn(row)).not.toBeNull();
    expect(row.querySelector('[role="switch"]')).not.toBeNull();
  });

  it("applies a slider move off-plan and badges it Today", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    await removeFromPlan(h, container, ROW);

    fireEvent.input(range(drawerRow(container, ROW)), { target: { value: "45" } });

    // Applied, not staged, and keyed exactly like a plan row's edit.
    expect(h.store.getState().overrides[ROW]?.blocks).toBe(1.5);
    const row = drawerRow(container, ROW);
    expect(readout(row)).toBe("45min");
    expect(badge(row)).toBe("today");
    // The edit is a duration change, never a membership change.
    expect(Object.values(h.store.getState().planMembership)).toEqual([false]);
    expect(drawerRow(container, ROW)).not.toBeNull();
  });

  it("toggles May overlap Mint off-plan", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    await removeFromPlan(h, container, ROW);
    expect(mint(drawerRow(container, ROW)).getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(mint(drawerRow(container, ROW)));

    await waitFor(() =>
      expect(h.store.getState().mintOverlapOptIns[ROW]).toBe(true),
    );
    expect(mint(drawerRow(container, ROW)).getAttribute("aria-pressed")).toBe("true");
  });

  it("expands into RowExpanded through the one shared expandedId", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    await removeFromPlan(h, container, ROW);
    expect(drawerRow(container, ROW).querySelector(".trow__exp")).toBeNull();

    fireEvent.click(expandBtn(drawerRow(container, ROW)));

    expect(h.store.getState().ui.expandedId).toBe(ROW);
    const row = drawerRow(container, ROW);
    expect(row.querySelector(".trow__exp")).not.toBeNull();
    expect(row.querySelector('button[aria-label="Collapse"]')).not.toBeNull();
    // RowExpanded is really mounted: its exact-minutes field is inside.
    expect(row.querySelector('input[aria-label="Exact minutes"]')).not.toBeNull();

    fireEvent.click(row.querySelector('button[aria-label="Collapse"]')!);
    expect(h.store.getState().ui.expandedId).toBeNull();
    expect(drawerRow(container, ROW).querySelector(".trow__exp")).toBeNull();
  });

  it("carries no green confirm check", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    await removeFromPlan(h, container, ROW);
    const row = drawerRow(container, ROW);
    expect(row.querySelector('[aria-label="Complete"]')).toBeNull();
    expect(row.querySelector(".trow__btn--primary")).toBeNull();
    expect(drawer(container).querySelector('[aria-label="Complete"]')).toBeNull();
  });

  it("keeps Add to plan working, and keeps the override it made", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    await removeFromPlan(h, container, ROW);
    fireEvent.input(range(drawerRow(container, ROW)), { target: { value: "45" } });
    expect(h.store.getState().overrides[ROW]?.blocks).toBe(1.5);

    fireEvent.click(
      drawerRow(container, ROW).querySelector<HTMLInputElement>('[role="switch"]')!,
    );

    // The row is a plan member again and has left the drawer ...
    await waitFor(() => expect(h.store.getState().planMembership).toEqual({}));
    await waitFor(() =>
      expect(container.querySelector("#unassigned-today")).toBeNull(),
    );
    // ... and the off-plan edit is the plan row's duration.
    expect(h.store.getState().overrides[ROW]?.blocks).toBe(1.5);
    const back = rowFor(container, ROW);
    expect(readout(back)).toBe("45min");
    expect(badge(back)).toBe("today");
  });
});

describe("drawer-rows grid for an adjust-capable local row", () => {
  const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

  it("gives the local row a slider column and a content-sized action cell", () => {
    expect(appCss).toMatch(
      /\.skipped-today__list > \.qrow\.trow--unassigned\.trow--dslider\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+var\(--trow-dur-w\)\s+auto/,
    );
  });

  it("lays the row's actions out on one line so the switch cannot wrap", () => {
    expect(appCss).toMatch(
      /\.skipped-today__list > \.qrow\.trow--unassigned\.trow--dslider > \.trow__acts\s*\{[^}]*display:\s*flex[^}]*flex-wrap:\s*nowrap/,
    );
  });
});
