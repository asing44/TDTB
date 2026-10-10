/* capacities-rules.test.tsx — the per-type inclusion-rule editor.

   Covers: the recursive DSL shapes actually sent (exists/truthy with no
   values), a real 409 conflict that PRESERVES the local draft and reports
   both revisions, an invalid draft and a missing-schema save that never fake
   an activation, and an editable nullable fallback. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { CapacitiesSettingsDrawer } from "./CapacitiesSettingsDrawer";
import { makeHarness } from "./test-harness";
import { ApiError } from "../adapters/api";
import { FixtureAdapter, fixtureRuleProblem } from "../adapters/fixture";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function openRules() {
  const h = makeHarness("ready");
  h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
  const rendered = h.ui(<CapacitiesSettingsDrawer />);
  await waitFor(
    () => expect(rendered.container.querySelector('[data-structure="custom-project"]')).toBeTruthy(),
    { timeout: 5000 },
  );
  const row = rendered.container.querySelector('[data-structure="custom-project"]') as HTMLElement;
  return { h, rendered, row };
}

describe("CapacitiesRulesEditor", () => {
  it("sends exists / truthy leaf shapes without a values array", async () => {
    const { h, rendered } = await openRules();
    const rowEl = () =>
      rendered.container.querySelector('[data-structure="custom-project"]') as HTMLElement;
    const save = vi.spyOn(h.controller, "saveCapacitiesRule");

    fireEvent.change(within(rowEl()).getByLabelText("Operator"), { target: { value: "exists" } });
    await waitFor(() => expect(within(rowEl()).queryByLabelText("Values")).toBeNull());
    fireEvent.click(within(rowEl()).getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toMatchObject({
      structureId: "custom-project",
      rule: { all: [{ prop: "title-prop", op: "exists" }] },
      expectedRevision: 0,
    });
    expect(JSON.stringify(save.mock.calls[0][0].rule)).not.toContain("values");
    // Wait for the save to settle (notice + re-based row) before editing again.
    await waitFor(() => expect(rendered.getByText(/Rule for custom-project is active/)).toBeTruthy());

    fireEvent.change(within(rowEl()).getByLabelText("Operator"), { target: { value: "truthy" } });
    await waitFor(() => expect(within(rowEl()).queryByLabelText("Values")).toBeNull());
    fireEvent.click(within(rowEl()).getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(save.mock.calls[1][0].rule).toEqual({ all: [{ prop: "title-prop", op: "truthy" }] });
  });

  it("preserves the local draft on a real 409 conflict and reports both revisions", async () => {
    const { h, rendered, row } = await openRules();
    vi.spyOn(h.controller, "saveCapacitiesRule").mockRejectedValueOnce(
      new ApiError(
        409,
        {
          code: "capacities_rules_conflict",
          message: "changed",
          expected_revision: 0,
          current_revision: 3,
        },
        "conflict",
      ),
    );

    const values = within(row).getByLabelText("Values") as HTMLInputElement;
    fireEvent.input(values, { target: { value: "on-hold" } });
    await waitFor(() => expect(values.value).toBe("on-hold"));
    fireEvent.click(within(row).getByRole("button", { name: "Save rule" }));

    await waitFor(() =>
      expect(rendered.getByText(/Rules changed since they were read/)).toBeTruthy(),
    );
    expect(rendered.getByText(/stored revision 3/)).toBeTruthy();
    expect(rendered.getByText(/expected 0/)).toBeTruthy();
    // The unsaved draft is still the user's, not the server's older value.
    expect((within(row).getByLabelText("Values") as HTMLInputElement).value).toBe("on-hold");
    expect(within(row).getByText("Unsaved draft")).toBeTruthy();
  });

  it("shows a draft notice (no activation) for a type with no published schema", async () => {
    const { rendered } = await openRules();
    const orphan = rendered.container.querySelector(
      '[data-structure="0d194525-c5a1-4af5-bb62-202b83006b5e"]',
    ) as HTMLElement;
    expect(within(orphan).getByText(/no published schema/)).toBeTruthy();
    fireEvent.click(within(orphan).getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(rendered.getByText(/Saved as a draft/)).toBeTruthy());
    expect(rendered.getByText(/previous active rule is unchanged/)).toBeTruthy();
  });

  it("clears the fallback with an empty field", async () => {
    const { h, rendered } = await openRules();
    const rowEl = () =>
      rendered.container.querySelector('[data-structure="custom-project"]') as HTMLElement;
    const save = vi.spyOn(h.controller, "saveCapacitiesRule");

    const fallback = within(rowEl()).getByLabelText(/Fallback minutes/) as HTMLInputElement;
    fireEvent.input(fallback, { target: { value: "45" } });
    await waitFor(() => expect(fallback.value).toBe("45"));
    fireEvent.click(within(rowEl()).getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].fallbackMinutes).toBe(45);
    await waitFor(() => expect(rendered.getByText(/Rule for custom-project is active/)).toBeTruthy());

    const fallbackAfter = within(rowEl()).getByLabelText(/Fallback minutes/) as HTMLInputElement;
    fireEvent.input(fallbackAfter, { target: { value: "" } });
    await waitFor(() => expect(fallbackAfter.value).toBe(""));
    fireEvent.click(within(rowEl()).getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(save.mock.calls[1][0].fallbackMinutes).toBeNull();
  });
});

describe("fixture rule save parity", () => {
  it("stores an invalid rule as a draft and preserves the prior active rule", async () => {
    const adapter = new FixtureAdapter("ready");
    const caps = (await adapter.loadCapacitiesRules()).capabilities;
    // A numeric-only operator on a title-kind property is invalid.
    const invalid = { prop: "title-prop", op: "gt", values: [1] };
    expect(fixtureRuleProblem(invalid, { "title-prop": "title" }, caps)).toContain("requires a numeric property");

    const first = await adapter.saveCapacitiesRule({
      structureId: "custom-project",
      rule: { prop: "title-prop", op: "exists" },
      fallbackMinutes: null,
      expectedRevision: 0,
    });
    expect(first.save.valid).toBe(true);
    expect(first.save.active).toEqual({ prop: "title-prop", op: "exists" });

    const second = await adapter.saveCapacitiesRule({
      structureId: "custom-project",
      rule: invalid,
      fallbackMinutes: null,
      expectedRevision: first.revision,
    });
    expect(second.save.valid).toBe(false);
    // The invalid rule is the draft; the active rule is unchanged.
    expect(second.save.draft).toEqual(invalid);
    expect(second.save.active).toEqual({ prop: "title-prop", op: "exists" });
  });

  it("never activates a rule for a structure with no published schema", async () => {
    const adapter = new FixtureAdapter("ready");
    const saved = await adapter.saveCapacitiesRule({
      structureId: "0d194525-c5a1-4af5-bb62-202b83006b5e",
      rule: { all: [] },
      fallbackMinutes: null,
      expectedRevision: 0,
    });
    expect(saved.save.valid).toBe(false);
    expect(saved.save.reason).toContain("no discovered schema");
    expect(saved.save.active).toBeNull();
    expect(saved.save.draft).toEqual({ all: [] });
  });

  it("rejects a stale revision rather than overwriting", async () => {
    const adapter = new FixtureAdapter("ready");
    await adapter.saveCapacitiesRule({
      structureId: "custom-project",
      rule: { all: [] },
      fallbackMinutes: null,
      expectedRevision: 0,
    });
    await expect(
      adapter.saveCapacitiesRule({
        structureId: "custom-project",
        rule: { all: [] },
        fallbackMinutes: null,
        expectedRevision: 0,
      }),
    ).rejects.toThrow(/changed since they were read/);
  });
});
