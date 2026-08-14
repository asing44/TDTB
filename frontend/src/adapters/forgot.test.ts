/* forgot.test.ts — allocator-rewrite T6: the forgot-strip lists crossing the
   adapter boundary. Locked decision 8 deliberately narrows the "suggested
   never crosses into component state" constraint to admit exactly these two
   derived, capped lists — so these tests pin BOTH halves: that they cross,
   and that the ranked pool still does not. */

import { describe, expect, it } from "vitest";
import planInputs from "./contract-fixtures/plan-inputs.json";
import { FORGOT_LIST_CAP, projectForgotList, projectPlanInputs } from "./wire";

describe("projectForgotList", () => {
  it("maps the server shape verbatim", () => {
    expect(
      projectForgotList([
        { name: "Roof", path: "p/Roof.md", reason: "due today and unassigned" },
      ]),
    ).toEqual([
      { name: "Roof", path: "p/Roof.md", reason: "due today and unassigned" },
    ]);
  });

  it("degrades to empty for anything non-array", () => {
    for (const bad of [undefined, null, {}, "nope", 7]) {
      expect(projectForgotList(bad)).toEqual([]);
    }
  });

  it("skips malformed entries instead of throwing", () => {
    expect(
      projectForgotList([null, "x", { name: "Ok", reason: "r" }]),
    ).toEqual([{ name: "Ok", path: null, reason: "r" }]);
  });

  it("drops nameless rows — there'd be nothing to act on", () => {
    expect(projectForgotList([{ path: "p.md", reason: "r" }])).toEqual([]);
  });

  it("empty path normalizes to null", () => {
    expect(projectForgotList([{ name: "A", path: "", reason: "r" }])[0].path)
      .toBeNull();
  });

  it("re-applies the server cap so a stale payload can't flood the strip", () => {
    const rows = Array.from({ length: 40 }, (_, i) => ({
      name: `I${i}`, path: null, reason: "r",
    }));
    expect(projectForgotList(rows)).toHaveLength(FORGOT_LIST_CAP);
  });

  it("re-applies the 140-char reason bound", () => {
    const [row] = projectForgotList([{ name: "A", reason: "z".repeat(500) }]);
    expect(row.reason).toHaveLength(140);
  });
});

describe("projectPlanInputs — forgot-strip fields", () => {
  it("both lists are always present, even when the digest omits them", () => {
    const inputs = projectPlanInputs({ digest: {} });
    expect(inputs.unassignedCandidates).toEqual([]);
    expect(inputs.staleAssigned).toEqual([]);
  });

  it("carries the contract fixture's stale_assigned across", () => {
    const inputs = projectPlanInputs(planInputs as never);
    expect(inputs.staleAssigned.length).toBeGreaterThan(0);
    expect(inputs.staleAssigned[0].reason).toMatch(/has passed/);
  });

  it("the ranked pool still never crosses — only the two derived lists do", () => {
    const inputs = projectPlanInputs({
      digest: {
        assigned: [],
        suggested: [{ name: "PoolItem", path: "p.md" }],
        unassigned_candidates: [{ name: "Cand", path: "c.md", reason: "r" }],
        stale_assigned: [],
      },
    });
    expect(JSON.stringify(inputs)).not.toContain("PoolItem");
    expect(inputs.unassignedCandidates[0].name).toBe("Cand");
  });
});

/* Regression: /capacity-preview nests segments, /plan-inputs flattens them.
   Only `mint` used to read both, so every other segment came back undefined
   after a duration change — invisible until T7's live readout computed from
   them and produced NaN. */
import capacityPreview from "./contract-fixtures/capacity-preview.json";
import { projectCapacity } from "./wire";

describe("projectCapacity — both wire shapes", () => {
  it("reads the NESTED segments of /capacity-preview", () => {
    const cap = projectCapacity(capacityPreview as never);
    for (const key of ["fixed", "anchored", "habits", "mint", "selected", "buffer"] as const) {
      expect(Number.isNaN(cap[key]), `${key} is NaN`).toBe(false);
    }
    expect(cap.selected).toBeGreaterThan(0);
  });

  it("reads the FLAT segments of /plan-inputs", () => {
    const cap = projectCapacity((planInputs as never as Record<string, never>)["capacity"]);
    expect(Number.isNaN(cap.anchored)).toBe(false);
    expect(cap.anchored).toBeGreaterThan(0);
  });

  it("a missing segment lands as 0, never undefined or NaN", () => {
    const cap = projectCapacity({});
    for (const key of ["total", "fixed", "anchored", "habits", "mint", "selected",
                       "buffer", "free", "availableForSelection"] as const) {
      expect(cap[key]).toBe(0);
    }
  });
});
