/* drawer-row-columns.test.tsx — the Not in plan drawer's rows share one
   fixed-column grid: title/meta | duration (reserved even when empty) |
   action. A row without a duration must still render the duration cell, so its
   Add to plan switch lines up with rows that show "90 min".

   jsdom does no layout, so the structure is asserted on the DOM and the grid
   template is read off the stylesheet on disk. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";

afterEach(cleanup);

describe("drawer rows reserve the duration column", () => {
  it("renders a duration cell on every row, empty or not, before the action cell", async () => {
    const h = makeHarness("ready", (sc) => {
      const pin = (id: string, blocks: number) => {
        const item = sc.inputs.assigned.find((i) => i.id === id);
        if (!item) throw new Error(`no input row ${id}`);
        item.blocks = blocks;
      };
      pin("Press", 0); // no duration -> empty reserved cell
      pin("Note Processing", 3); // 90 min
    });
    const { container } = h.ui(<Queue />);
    for (const name of ["Press", "Note Processing"]) {
      const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
        (r) => r.querySelector(".qrow__name")?.textContent === name,
      )!;
      const before = Object.keys(h.store.getState().planMembership).length;
      fireEvent.keyDown(row, { key: "e" });
      await waitFor(() =>
        expect(Object.keys(h.store.getState().planMembership).length).toBe(before + 1),
      );
    }
    const rows = Array.from(
      container.querySelectorAll<HTMLElement>("#unassigned-today li.qrow"),
    );
    expect(rows.length).toBeGreaterThanOrEqual(2);
    for (const row of rows) {
      const kids = Array.from(row.children);
      // Exactly three grid cells: body, duration, actions.
      expect(kids.map((k) => k.className.split(" ")[0])).toEqual([
        "qrow__body",
        "trow__dur",
        "trow__acts",
      ]);
    }
    const durs = rows.map((r) => r.querySelector(".trow__dur")?.textContent);
    expect(durs).toContain("");
    expect(durs).toContain("90 min");
    expect(
      rows.filter((r) => r.querySelector(".trow__dur--empty")).length,
    ).toBe(durs.filter((d) => d === "").length);
  });
});

describe("drawer-rows.css", () => {
  const css = readFileSync(resolve(process.cwd(), "src/ui/drawer-rows.css"), "utf8");

  it("uses one flexible | fixed | fixed template for local and server-held rows", () => {
    expect(css).toMatch(
      /\.qrow\.trow--skipped,[\s\S]*?\.qrow\.trow--unassigned\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+66px\s+150px/,
    );
  });

  it("right-aligns the action cell", () => {
    expect(css).toMatch(/\.trow__acts\s*\{[^}]*justify-content:\s*flex-end/);
  });
});
