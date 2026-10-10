/* capacities-unknown-review.test.tsx — the unknown-candidate review surface.

   Covers: required acknowledgement for a review-reason candidate, the merge
   body carrying the stable canonical identity + acknowledgement + expected
   revision, the server-digest refresh after a save (the promotion reaches the
   store inputs, not just local UI state), a real 409 that preserves the local
   selection, and refusal of a non-canonical identity. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { CapacitiesSettingsDrawer } from "./CapacitiesSettingsDrawer";
import { makeHarness } from "./test-harness";
import { ApiError } from "../adapters/api";

const CANONICAL = "capacities:space-1:custom-project:obj-1";
const CANONICAL_ACK = "capacities:space-1:custom-project:obj-2";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function openReview() {
  const h = makeHarness("ready");
  // Populate the typed direct-intake block (and its stable candidates) through
  // the real refresh path rather than hand-injecting store state.
  await h.controller.refreshSources();
  h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
  const rendered = h.ui(<CapacitiesSettingsDrawer />);
  await waitFor(() => expect(rendered.getByText("Renew domain")).toBeTruthy(), { timeout: 5000 });
  const section = rendered.container.querySelector('[data-settings-section="unknown"]') as HTMLElement;
  return { h, rendered, section };
}

describe("CapacitiesUnknownReview", () => {
  it("requires acknowledgement before a review-reason candidate can be selected", async () => {
    const { section } = await openReview();
    const select = within(section).getByLabelText(`Select ${CANONICAL_ACK}`) as HTMLInputElement;
    const ack = within(section).getByLabelText(`Acknowledge ${CANONICAL_ACK}`) as HTMLInputElement;

    fireEvent.click(select);
    expect(select.checked).toBe(false);
    expect(section.textContent).toContain("Acknowledge this candidate before selecting it.");

    fireEvent.click(ack);
    fireEvent.click(select);
    expect(select.checked).toBe(true);
  });

  it("sends the canonical identity + acknowledgement + revision and refreshes the server digest", async () => {
    const { h, section } = await openReview();
    const save = vi.spyOn(h.controller, "saveCapacitiesSelections");

    fireEvent.click(within(section).getByLabelText(`Acknowledge ${CANONICAL_ACK}`));
    fireEvent.click(within(section).getByLabelText(`Select ${CANONICAL_ACK}`));
    fireEvent.click(within(section).getByRole("button", { name: "Save selections" }));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toEqual({
      expectedRevision: 0,
      select: [{ identity: CANONICAL_ACK, acknowledge: true }],
      deselect: [],
    });
    // The save re-reads /plan-inputs, so the promoted row reaches the store
    // digest (and therefore any later Commit body) — not just local UI state.
    await waitFor(() =>
      expect(h.store.getState().inputs!.assigned.some((i) => i.identity === CANONICAL_ACK)).toBe(true),
    );
  });

  it("preserves the local selection on a real 409 conflict", async () => {
    const { h, rendered, section } = await openReview();
    vi.spyOn(h.controller, "saveCapacitiesSelections").mockRejectedValueOnce(
      new ApiError(
        409,
        {
          code: "capacities_selections_conflict",
          message: "changed",
          expected_revision: 0,
          current_revision: 2,
        },
        "conflict",
      ),
    );

    const select = within(section).getByLabelText(`Select ${CANONICAL}`) as HTMLInputElement;
    fireEvent.click(select);
    fireEvent.click(within(section).getByRole("button", { name: "Save selections" }));

    await waitFor(() =>
      expect(rendered.getByText(/Selections changed since they were read/)).toBeTruthy(),
    );
    expect(rendered.getByText(/stored revision 2/)).toBeTruthy();
    expect(select.checked).toBe(true);
  });

  it("refuses a non-canonical identity", async () => {
    const { h, rendered, section } = await openReview();
    const inputs = h.store.getState().inputs!;
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        capacitiesIntake: {
          ...inputs.capacitiesIntake!,
          unassignedCandidates: [
            { identity: "not-canonical", name: "Bogus", reviewReasons: [], selected: false },
          ],
        },
      },
      ledger: h.store.getState().ledger!,
    });
    await waitFor(() => expect(rendered.getByText("Bogus")).toBeTruthy());
    const select = within(section).getByLabelText("Select not-canonical") as HTMLInputElement;
    expect(select.disabled).toBe(true);
    expect(section.textContent).toContain("Not a canonical identity");
  });
});
