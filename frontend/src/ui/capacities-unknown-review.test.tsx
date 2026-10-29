/* capacities-unknown-review.test.tsx — the unknown-candidate review surface.

   Covers: a review-reason candidate being selectable WITHOUT a prior
   acknowledgement (owner decision 2026-10-29: review reasons are warn-only for
   planning), the merge body carrying the stable canonical identity +
   acknowledgement + expected revision, the server-digest refresh after a save
   (the promotion reaches the store inputs, not just local UI state), a real 409
   that preserves the local selection, and refusal of a non-canonical identity. */

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
  h.store.dispatch({ type: "UI", patch: { settingsPanel: "freshness" } });
  const rendered = h.ui(<CapacitiesSettingsDrawer />);
  await waitFor(() => expect(rendered.getByText("Renew domain")).toBeTruthy(), { timeout: 5000 });
  const section = rendered.container.querySelector('[data-settings-section="unknown"]') as HTMLElement;
  return { h, rendered, section };
}

describe("CapacitiesUnknownReview", () => {
  it("selects a review-reason candidate without acknowledging it first", async () => {
    const { h, section } = await openReview();
    const save = vi.spyOn(h.controller, "saveCapacitiesSelections");
    const select = within(section).getByLabelText(`Include ${CANONICAL_ACK} in my plan`) as HTMLInputElement;
    const ack = within(section).getByLabelText(`Mark ${CANONICAL_ACK} as okay`) as HTMLInputElement;

    expect(ack.checked).toBe(false);
    fireEvent.click(select);
    expect(select.checked).toBe(true);
    // Nothing here states an acknowledgement-first requirement: the include
    // control is enabled with the acknowledgement unchecked.
    expect(select.disabled).toBe(false);
    expect(section.textContent).not.toMatch(/mark .* as okay (first|before)/i);

    fireEvent.click(within(section).getByRole("button", { name: "Save choices" }));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toEqual({
      expectedRevision: 0,
      select: [{ identity: CANONICAL_ACK, acknowledge: false }],
      deselect: [],
    });
  });

  it("sends the canonical identity + acknowledgement + revision and refreshes the server digest", async () => {
    const { h, section } = await openReview();
    const save = vi.spyOn(h.controller, "saveCapacitiesSelections");

    fireEvent.click(within(section).getByLabelText(`Mark ${CANONICAL_ACK} as okay`));
    fireEvent.click(within(section).getByLabelText(`Include ${CANONICAL_ACK} in my plan`));
    fireEvent.click(within(section).getByRole("button", { name: "Save choices" }));

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

    const select = within(section).getByLabelText(`Include ${CANONICAL} in my plan`) as HTMLInputElement;
    fireEvent.click(select);
    fireEvent.click(within(section).getByRole("button", { name: "Save choices" }));

    await waitFor(() =>
      expect(rendered.getByText(/Your choices changed elsewhere since you opened this/)).toBeTruthy(),
    );
    expect(rendered.getByText(/saved version 2/)).toBeTruthy();
    expect(select.checked).toBe(true);
  });

  it("shows one plain intake sentence and keeps the raw values behind Technical details", async () => {
    const { section } = await openReview();
    const details = section.querySelector(
      "details.settings-hub__details",
    ) as HTMLDetailsElement;
    expect(details).toBeTruthy();
    // Closed by default; the visible line is the plain sentence.
    expect(details.open).toBe(false);
    expect(details.querySelector("summary")!.textContent).toBe("Technical details");
    const plain = Array.from(section.querySelectorAll("p"))
      .filter((p) => p.closest("details") === null)
      .map((p) => p.textContent ?? "")
      .join(" ");
    // The visible line is a plain sentence, never the raw intake enum.
    expect(plain).toMatch(/Capacities items are being read/);
    expect(plain).not.toMatch(/\b(degraded|refresh_required|not_configured|unavailable|ok)\b/);
    // Raw values (the enum state, generation, coverage) live in the disclosure.
    const raw = details.textContent ?? "";
    expect(raw).toContain("Generation");
    expect(raw).toContain("Coverage");
    expect(raw).toMatch(/\d+ \/ \d+ evaluated/);
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
    const select = within(section).getByLabelText("Include not-canonical in my plan") as HTMLInputElement;
    expect(select.disabled).toBe(true);
    expect(section.textContent).toContain("This item's id is not valid, so it can't be chosen.");
  });
});
