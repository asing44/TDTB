/* capacities-unknown-ack-all.test.tsx — the ONE "Acknowledge all" control in
   the unknown-candidate review surface.

   Covers: no control below two unacknowledged review rows, the count in the
   label, ONE batched selection request carrying ``acknowledge: true`` for
   exactly the listed unacknowledged review rows (never a non-review row, never
   an already-acknowledged one, never a non-canonical one), the control
   disabled while the request is in flight, the list re-reading after success so
   the control clears, a failed batch reporting one short inline error while the
   per-row controls stay usable, and the per-row controls still working
   unchanged. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { CapacitiesSettingsDrawer } from "./CapacitiesSettingsDrawer";
import { makeHarness } from "./test-harness";
import { ApiError } from "../adapters/api";
import type { CapacitiesIntakeCandidate, CapacitiesSelections } from "../model/types";

const A = "capacities:space-1:custom-project:obj-1";
const B = "capacities:space-1:custom-project:obj-2";
const C = "capacities:space-1:custom-project:obj-3";

function candidate(
  identity: string,
  name: string,
  reviewReasons: string[],
): CapacitiesIntakeCandidate {
  return { identity, name, reviewReasons, selected: false };
}

const REVIEW_A = candidate(A, "Draft launch brief", ["status_unknown"]);
const REVIEW_B = candidate(B, "Renew domain", ["status_unknown"]);
const REVIEW_C = candidate(C, "Draft invoice", ["status_unknown"]);
const PLAIN = candidate("capacities:space-1:custom-project:obj-4", "Plain object", []);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function openReview(candidates: CapacitiesIntakeCandidate[]) {
  const h = makeHarness("ready");
  await h.controller.refreshSources();
  const inputs = h.store.getState().inputs!;
  h.store.dispatch({
    type: "INPUTS_LOADED",
    inputs: {
      ...inputs,
      capacitiesIntake: { ...inputs.capacitiesIntake!, unassignedCandidates: candidates },
    },
    ledger: h.store.getState().ledger!,
  });
  h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
  const rendered = h.ui(<CapacitiesSettingsDrawer />);
  await waitFor(() => expect(rendered.getByText(candidates[0].name)).toBeTruthy(), {
    timeout: 5000,
  });
  const section = rendered.container.querySelector(
    '[data-settings-section="unknown"]',
  ) as HTMLElement;
  return { h, rendered, section };
}

function ackAllButton(section: HTMLElement, count: number): HTMLButtonElement {
  return within(section).getByRole("button", {
    name: `Acknowledge all ${count} review rows`,
  }) as HTMLButtonElement;
}

describe("Capacities unknown review — acknowledge all", () => {
  it("offers no control with zero or one unacknowledged review rows", async () => {
    const none = await openReview([PLAIN]);
    expect(within(none.section).queryByText(/Acknowledge all/)).toBeNull();
    cleanup();

    const one = await openReview([REVIEW_A, PLAIN]);
    expect(within(one.section).queryByText(/Acknowledge all/)).toBeNull();
  });

  it("counts only unacknowledged review rows and acknowledges them in ONE request", async () => {
    const { h, section } = await openReview([REVIEW_A, REVIEW_B, PLAIN]);
    // The save re-earns plan inputs; stubbing that refresh keeps THIS file
    // about the acknowledgement call, not the fixture's input regeneration.
    vi.spyOn(h.controller, "refreshSources").mockResolvedValue(undefined);
    const save = vi.spyOn(h.controller, "saveCapacitiesSelections");

    const button = ackAllButton(section, 2);
    expect(button.textContent).toContain("Acknowledge all (2)");
    expect(button.disabled).toBe(false);

    fireEvent.click(button);

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toEqual({
      expectedRevision: 0,
      select: [
        { identity: A, acknowledge: true },
        { identity: B, acknowledge: true },
      ],
      deselect: [],
    });

    // Success re-reads the saved record exactly like the per-row save does, so
    // both rows read as acknowledged and the control clears.
    await waitFor(() =>
      expect(within(section).getByText(/Acknowledged 2 review rows/)).toBeTruthy(),
    );
    expect(within(section).queryByText(/Acknowledge all/)).toBeNull();
    expect(
      (within(section).getByLabelText(`Acknowledge ${A}`) as HTMLInputElement).checked,
    ).toBe(true);
    expect(
      (within(section).getByLabelText(`Acknowledge ${B}`) as HTMLInputElement).checked,
    ).toBe(true);
  });

  it("excludes an already-acknowledged row and leaves the per-row control working", async () => {
    const h = makeHarness("ready");
    await h.adapter.saveCapacitiesSelections({
      expectedRevision: 0,
      select: [{ identity: C, acknowledge: true }],
      deselect: [],
    });
    await h.controller.refreshSources();
    const inputs = h.store.getState().inputs!;
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        capacitiesIntake: {
          ...inputs.capacitiesIntake!,
          unassignedCandidates: [REVIEW_A, REVIEW_B, REVIEW_C],
        },
      },
      ledger: h.store.getState().ledger!,
    });
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(rendered.getByText("Draft launch brief")).toBeTruthy(), {
      timeout: 5000,
    });
    const section = rendered.container.querySelector(
      '[data-settings-section="unknown"]',
    ) as HTMLElement;
    const save = vi.spyOn(h.controller, "saveCapacitiesSelections");

    const button = ackAllButton(section, 2);
    fireEvent.click(button);

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toEqual({
      expectedRevision: 1,
      select: [
        { identity: A, acknowledge: true },
        { identity: B, acknowledge: true },
      ],
      deselect: [],
    });

    const ackC = within(section).getByLabelText(`Acknowledge ${C}`) as HTMLInputElement;
    expect(ackC.checked).toBe(true);
    fireEvent.click(ackC);
    expect(ackC.checked).toBe(false);
  });

  it("skips a review row with a non-canonical identity", async () => {
    const bogus = candidate("not-canonical", "Bogus", ["status_unknown"]);
    const { h, section } = await openReview([REVIEW_A, REVIEW_B, bogus]);
    const save = vi.spyOn(h.controller, "saveCapacitiesSelections");

    fireEvent.click(ackAllButton(section, 2));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].select).toEqual([
      { identity: A, acknowledge: true },
      { identity: B, acknowledge: true },
    ]);
    expect(
      (within(section).getByLabelText("Acknowledge not-canonical") as HTMLInputElement)
        .checked,
    ).toBe(false);
  });

  it("disables the control while the request is in flight", async () => {
    const { h, section } = await openReview([REVIEW_A, REVIEW_B]);
    let release: (value: {
      selections: CapacitiesSelections;
      refreshError: string | null;
    }) => void = () => {};
    const pending = new Promise<{
      selections: CapacitiesSelections;
      refreshError: string | null;
    }>((resolve) => {
      release = resolve;
    });
    vi.spyOn(h.controller, "saveCapacitiesSelections").mockReturnValue(pending);

    const button = ackAllButton(section, 2);
    fireEvent.click(button);

    await waitFor(() => expect(button.disabled).toBe(true));
    expect(button.textContent).toContain("Acknowledging");

    release({
      selections: {
        spaceId: "space-1",
        revision: 1,
        rulesRevision: 0,
        selections: [
          { identity: A, acknowledged: true, rulesRevision: 0 },
          { identity: B, acknowledged: true, rulesRevision: 0 },
        ],
      },
      refreshError: null,
    });

    await waitFor(() =>
      expect(within(section).getByText(/Acknowledged 2 review rows/)).toBeTruthy(),
    );
    expect(within(section).queryByText(/Acknowledge all/)).toBeNull();
  });

  it("reports one short inline error and keeps the rows usable when the batch fails", async () => {
    const { h, section } = await openReview([REVIEW_A, REVIEW_B]);
    vi.spyOn(h.controller, "saveCapacitiesSelections").mockRejectedValueOnce(
      new ApiError(
        409,
        {
          code: "not_cached",
          message: "not a current cached candidate.",
        },
        "not a current cached candidate.",
      ),
    );

    fireEvent.click(ackAllButton(section, 2));

    await waitFor(() =>
      expect(within(section).getByRole("alert").textContent).toContain(
        "not a current cached candidate",
      ),
    );

    // Nothing was acknowledged: the count is unchanged and both per-row
    // controls still respond.
    const ackA = within(section).getByLabelText(`Acknowledge ${A}`) as HTMLInputElement;
    const ackB = within(section).getByLabelText(`Acknowledge ${B}`) as HTMLInputElement;
    expect(ackA.checked).toBe(false);
    expect(ackB.checked).toBe(false);
    expect(ackAllButton(section, 2).disabled).toBe(false);

    fireEvent.click(ackA);
    expect(ackA.checked).toBe(true);
    expect(ackB.checked).toBe(false);
  });
});
