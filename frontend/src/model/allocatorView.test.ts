/* allocatorView.test.ts — P6-01 relationship fixtures.

   These tests keep the current urgency-band axis intact while pinning the
   additive grouping metadata seam for P6-02/P6-03.  The red assertions are
   deliberately about semantic ordering and projection, not a new UI layout. */

import { describe, expect, it } from "vitest";
import { projectSequenceResult } from "../adapters/wire";
import { makeScenario } from "../fixtures/scenarios";
import {
  bandSpend,
  bandedRows,
  hierarchyBandedRows,
  hierarchyRows,
  includedDisplayOrder,
  localSelected,
} from "./allocatorView";
import { createStore } from "../store/createStore";
import type { AppState } from "../store/store";
import type { AssignedItem } from "./types";
import { planOverflow } from "./overflow";

type TaggedAssignedItem = AssignedItem & { tags?: string[] };

const TODAY = "2026-07-18";

function item(name: string, over: Partial<AssignedItem> & { tags?: string[] } = {}): TaggedAssignedItem {
  return {
    id: name,
    name,
    path: `50 - Operations/Projects/${name}.md`,
    source: "vault",
    types: ["project"],
    urgency: null,
    deadline: null,
    priorityScore: 0,
    blocks: 1,
    durationLabel: "30min",
    todoistId: null,
    ...over,
  };
}

function stateFor(assigned: AssignedItem[]): AppState {
  const store = createStore();
  const scenario = makeScenario("ready");
  store.dispatch({
    type: "INPUTS_LOADED",
    inputs: { ...scenario.inputs, validDate: TODAY, assigned },
    ledger: { ...scenario.ledger, today: TODAY },
  });
  return store.getState();
}

describe("P6-01 relationship-aware allocator projection", () => {
  it("orders a parent before its child and keeps an infeasible child visible", () => {
    const parent = item("Zeta parent", { blocks: 2 });
    const child = item("Alpha child", {
      blocks: 1,
      relatesTo: "[[Zeta parent]]",
      tags: ["systems"],
    });
    const fullDayWall = { start: 7 * 60 + 30, end: 24 * 60 };
    const overflow = planOverflow(
      [parent, child],
      "07:30",
      (row) => row.blocks,
      [fullDayWall],
    );

    expect(overflow.rows).toEqual([]);
    expect(overflow.infeasible.map((row) => row.id)).toEqual([
      "Zeta parent",
      "Alpha child",
    ]);

    const visible = bandedRows(stateFor([child, parent])).else;
    expect(visible.map((row) => row.id)).toEqual(["Zeta parent", "Alpha child"]);
  });

  it("keeps relationship context visible when parent and child cross urgency bands", () => {
    const parent = item("Zeta parent", { urgency: "3-high" });
    const child = item("Alpha child", { relatesTo: "[[Zeta parent]]" });
    const groups = hierarchyBandedRows(stateFor([child, parent]));

    expect(groups.high.map((row) => row.item.id)).toEqual(["Zeta parent"]);
    expect(groups.else.map((row) => row.item.id)).toEqual(["Alpha child"]);
    expect(groups.else[0]).toMatchObject({
      depth: 1,
      parentName: "Zeta parent",
      groupLabel: "Group · Zeta parent",
    });
  });

  it("keeps same-start related rows stable across projection and ordering", () => {
    const raw = [
      {
        id: "Alpha child",
        start: "14:00",
        end: "14:30",
        zone: null,
        relates_to: "[[Zeta parent]]",
        tags: ["systems"],
      },
      {
        id: "Zeta parent",
        start: "14:00",
        end: "15:00",
        zone: null,
        tags: ["systems"],
      },
      {
        id: "Systems beta",
        start: "14:00",
        end: "14:30",
        zone: null,
        tags: ["systems"],
      },
      {
        id: "Systems alpha",
        start: "14:00",
        end: "14:30",
        zone: null,
        tags: ["systems"],
      },
    ];
    const projectAndOrder = () => {
      const projected = projectSequenceResult({ sequence: raw, warnings: [] }).sequence;
      return hierarchyRows(projected.map((row) => {
        const source = raw.find((candidate) => candidate.id === row.id)!;
        return item(row.id, {
          relatesTo: source.relates_to,
          tags: source.tags,
        });
      }), TODAY);
    };

    const first = projectAndOrder();
    const second = projectAndOrder();
    expect(second).toEqual(first);
    expect(first.map((row) => row.item.id)).toEqual([
      "Zeta parent",
      "Alpha child",
      "Systems alpha",
      "Systems beta",
    ]);
    expect(first[1].depth).toBe(1);
    expect(first[1].parentName).toBe("Zeta parent");
  });

  it("counts related parent/tag rows once without synthetic group spend", () => {
    const parent = item("Zeta parent", { blocks: 2, tags: ["systems"] });
    const child = item("Alpha child", {
      blocks: 1,
      relatesTo: "[[Zeta parent]]",
      tags: ["systems"],
    });
    const sibling = item("Systems sibling", { blocks: 1, tags: ["systems"] });
    const state = stateFor([child, sibling, parent]);
    const rows = includedDisplayOrder(state);

    expect(rows).toHaveLength(3);
    expect(new Set(rows.map((row) => row.id)).size).toBe(3);
    expect(rows.reduce((sum, row) => sum + row.blocks, 0)).toBe(4);
    expect(localSelected(state)).toBe(4);
    expect(bandSpend(state, "else")).toBe(4);
  });

  it("preserves the existing flat urgency-band axis", () => {
    const groups = bandedRows(stateFor([
      item("ordinary"),
      item("high", { urgency: "3-high" }),
      item("critical", { urgency: "4-crit" }),
    ]));

    expect(groups.crit.map((row) => row.id)).toEqual(["critical"]);
    expect(groups.high.map((row) => row.id)).toEqual(["high"]);
    expect(groups.else.map((row) => row.id)).toEqual(["ordinary"]);
  });

  it("groups shared tags without adding a synthetic spend row", () => {
    const rows = hierarchyRows([
      item("Alpha", { tags: ["focus", "shared"] }),
      item("Beta", { tags: ["shared"] }),
      item("Gamma", { tags: ["other"] }),
    ], TODAY);

    expect(rows.map((row) => row.item.id)).toEqual(["Alpha", "Beta", "Gamma"]);
    expect(rows[0].groupLabel).toBe("Related · #shared");
    expect(rows[1].groupKey).toBe(rows[0].groupKey);
    expect(rows[2].groupKey).toBeNull();
  });

  it("does not group duration tags as shared relationships", () => {
    const rows = hierarchyRows([
      item("Water plants", { tags: ["🚀10min"] }),
      item("Weigh self", { tags: ["🚀 10 min"] }),
      item("Quick cleanup", { tags: ["dur10"] }),
    ], TODAY);

    expect(rows.every((row) => row.groupKey === null)).toBe(true);
    expect(rows.every((row) => row.groupLabel === null)).toBe(true);
  });

  it("uses stable source identity as the final tie-break", () => {
    const rows = hierarchyRows([
      item("Beta", { identity: "vault:z" }),
      item("Alpha", { identity: "vault:a" }),
    ], TODAY);

    expect(rows.map((row) => row.item.identity)).toEqual(["vault:a", "vault:z"]);
    expect(rows).toHaveLength(2);
  });
});
