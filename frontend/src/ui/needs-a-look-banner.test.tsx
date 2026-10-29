/* needs-a-look-banner.test.tsx — the ONE warn-only "items need a look" banner
   above the plan queue (owner decision: review rows warn, they never block).

   Covers: hidden with no flagged rows and with advisory-only intake warnings,
   the count + listed titles + "+M more" tail, the count matching the review
   surface's own control, "Okay to plan all (N)" reusing the review surface's
   ONE batched selection call (and the controller's own refresh), the per-row
   "Okay to plan", the persistent row chip on a flagged plan row and its
   absence on a normal row, the live region + accessible button names, and that
   neither the banner nor the chip gates the plan. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { NeedsALookBanner } from "./NeedsALookBanner";
import { CapacitiesSettingsDrawer } from "./CapacitiesSettingsDrawer";
import { Queue } from "./Queue";
import { makeHarness, type Harness } from "./test-harness";
import { ApiError } from "../adapters/api";
import { assignmentRows } from "../store/store";
import type { CapacitiesIntakeCandidate, CapacitiesSelections } from "../model/types";

const A = "capacities:space-1:custom-project:obj-1";
const B = "capacities:space-1:custom-project:obj-2";
const C = "capacities:space-1:custom-project:obj-3";

function candidate(
  identity: string,
  name: string,
  reviewReasons: string[] = [],
): CapacitiesIntakeCandidate {
  return { identity, name, reviewReasons, selected: false };
}

const FLAG_A = candidate(A, "Draft launch brief", ["status_unknown"]);
const FLAG_B = candidate(B, "Renew domain", ["rule_unknown"]);
const FLAG_C = candidate(C, "Draft invoice", ["completion_unknown"]);
const PLAIN = candidate("capacities:space-1:custom-project:obj-4", "Plain object");

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** Stage the plan inputs with the given unknown candidates and, optionally,
 *  the intake state (advisory warnings ride that state, never the banner). */
async function stage(
  h: Harness,
  candidates: CapacitiesIntakeCandidate[],
  state: "ok" | "degraded" = "ok",
) {
  await h.controller.refreshSources();
  const inputs = h.store.getState().inputs!;
  h.store.dispatch({
    type: "INPUTS_LOADED",
    inputs: {
      ...inputs,
      capacitiesIntake: {
        ...inputs.capacitiesIntake!,
        state,
        unassignedCandidates: candidates,
      },
    },
    ledger: h.store.getState().ledger!,
  });
}

function banner(container: HTMLElement): HTMLElement | null {
  return container.querySelector("[data-needs-a-look]") as HTMLElement | null;
}

function savedSelections(identities: string[], revision: number): CapacitiesSelections {
  return {
    spaceId: "space-1",
    revision,
    rulesRevision: 0,
    selections: identities.map((identity) => ({
      identity,
      acknowledged: true,
      rulesRevision: 0,
    })),
  };
}

describe("NeedsALookBanner — visibility and count", () => {
  it("stays hidden with no review-reason rows and never reads the selection record", async () => {
    const h = makeHarness("ready");
    const load = vi.spyOn(h.controller, "loadCapacitiesSelections");
    await stage(h, [PLAIN]);
    const rendered = h.ui(<NeedsALookBanner />);
    await Promise.resolve();
    expect(banner(rendered.container)).toBeNull();
    expect(load).not.toHaveBeenCalled();
  });

  it("stays hidden when the intake only carries advisory warnings", async () => {
    const h = makeHarness("ready");
    await stage(h, [PLAIN], "degraded");
    const rendered = h.ui(<NeedsALookBanner />);
    await Promise.resolve();
    expect(rendered.queryByText(/need a look/)).toBeNull();
  });

  it("shows the count, the titles and a '+M more' tail", async () => {
    const h = makeHarness("ready");
    const seven = [
      FLAG_A,
      FLAG_B,
      FLAG_C,
      candidate("capacities:space-1:custom-project:obj-5", "Row five", ["status_unknown"]),
      candidate("capacities:space-1:custom-project:obj-6", "Row six", ["status_unknown"]),
      candidate("capacities:space-1:custom-project:obj-7", "Row seven", ["status_unknown"]),
      candidate("capacities:space-1:custom-project:obj-8", "Row eight", ["status_unknown"]),
    ];
    await stage(h, seven);
    const rendered = h.ui(<NeedsALookBanner />);
    await waitFor(() => expect(banner(rendered.container)).toBeTruthy());

    const box = banner(rendered.container)!;
    expect(within(box).getByText(/7 items need a look/)).toBeTruthy();
    expect(within(box).getByText("Draft launch brief")).toBeTruthy();
    expect(within(box).getByText("Row five")).toBeTruthy();
    // Five titles are listed inline; the last two fold into the tail.
    expect(within(box).getByText("Row six")).toBeTruthy();
    expect(within(box).queryByText("Row seven")).toBeNull();
    expect(within(box).queryByText("Row eight")).toBeNull();
    expect(within(box).getByText("+2 more")).toBeTruthy();
    // Nothing on this surface blocks planning or committing.
    expect(within(box).getByText(/planning and committing\s+stay available/)).toBeTruthy();
  });

  it("reads one item in the singular", async () => {
    const h = makeHarness("ready");
    await stage(h, [FLAG_A, PLAIN]);
    const rendered = h.ui(<NeedsALookBanner />);
    await waitFor(() => expect(banner(rendered.container)).toBeTruthy());
    expect(within(banner(rendered.container)!).getByText(/1 item needs a look/)).toBeTruthy();
  });

  it("counts exactly what the review surface counts", async () => {
    const h = makeHarness("ready");
    // One flagged row is already acknowledged in the durable record, so it
    // needs no look on either surface.
    await h.adapter.saveCapacitiesSelections({
      expectedRevision: 0,
      select: [{ identity: C, acknowledge: true }],
      deselect: [],
    });
    await stage(h, [FLAG_A, FLAG_B, FLAG_C, PLAIN]);
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "freshness" } });
    const rendered = h.ui(
      <>
        <NeedsALookBanner />
        <CapacitiesSettingsDrawer />
      </>,
    );

    const section = (await waitFor(() => {
      const found = rendered.container.querySelector(
        '[data-settings-section="unknown"]',
      ) as HTMLElement | null;
      expect(found).toBeTruthy();
      return found!;
    })) as HTMLElement;
    await waitFor(() => expect(banner(rendered.container)).toBeTruthy());

    // The review surface reads its own selection record first, so its
    // batched control exists only after that read resolves.
    const reviewControl = await waitFor(() =>
      within(section).getByRole("button", {
        name: /Okay to plan all \d+ items/,
      }),
    );
    const reviewCount = Number(
      /Okay to plan all (\d+) items/.exec(reviewControl.getAttribute("aria-label") ?? "")?.[1],
    );
    const bannerCount = Number(
      /(\d+) items need a look/.exec(banner(rendered.container)!.textContent ?? "")?.[1],
    );
    expect(bannerCount).toBe(reviewCount);
    expect(bannerCount).toBe(2);
  });
});

describe("NeedsALookBanner — the one batched selection call", () => {
  it("calls the shared controller action once with acknowledge+deselect and refreshes", async () => {
    const h = makeHarness("ready");
    await stage(h, [FLAG_A, FLAG_B, PLAIN]);
    const rendered = h.ui(<NeedsALookBanner />);
    await waitFor(() => expect(banner(rendered.container)).toBeTruthy());
    const save = vi
      .spyOn(h.controller, "saveCapacitiesSelections")
      .mockResolvedValue({ selections: savedSelections([A, B], 1), refreshError: null });

    fireEvent.click(
      rendered.getByRole("button", { name: "Okay to plan all 2 items" }),
    );

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toEqual({
      expectedRevision: 0,
      select: [
        { identity: A, acknowledge: true },
        { identity: B, acknowledge: true },
      ],
      deselect: [],
    });
    // The acknowledged record hides the banner: nothing left to look at.
    await waitFor(() => expect(banner(rendered.container)).toBeNull());
  });

  it("reaches the server through the controller's own save+refresh path", async () => {
    const h = makeHarness("ready");
    await stage(h, [FLAG_A, FLAG_B]);
    const rendered = h.ui(<NeedsALookBanner />);
    await waitFor(() => expect(banner(rendered.container)).toBeTruthy());
    const post = vi.spyOn(h.adapter, "saveCapacitiesSelections");
    const refresh = vi.spyOn(h.controller, "refreshSources");

    fireEvent.click(
      rendered.getByRole("button", { name: "Okay to plan all 2 items" }),
    );

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][0].select).toEqual([
      { identity: A, acknowledge: true },
      { identity: B, acknowledge: true },
    ]);
    expect(post.mock.calls[0][0].deselect).toEqual([]);
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    // Both rows are in the plan now, so nothing is left to look at: the
    // banner hides itself instead of holding the plan hostage.
    await waitFor(() => expect(banner(rendered.container)).toBeNull());
  });

  it("plans ONE row from its own Okay to plan and leaves the other alone", async () => {
    const h = makeHarness("ready");
    await stage(h, [FLAG_A, FLAG_B]);
    const rendered = h.ui(<NeedsALookBanner />);
    await waitFor(() => expect(banner(rendered.container)).toBeTruthy());
    const save = vi
      .spyOn(h.controller, "saveCapacitiesSelections")
      .mockResolvedValue({ selections: savedSelections([A], 1), refreshError: null });

    fireEvent.click(rendered.getByRole("button", { name: "Okay to plan Draft launch brief" }));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].select).toEqual([{ identity: A, acknowledge: true }]);
    // The other row still needs a look, so the banner stays and reports the
    // save it just made.
    await waitFor(() =>
      expect(within(banner(rendered.container)!).getByText(/1 item needs a look/)).toBeTruthy(),
    );
    expect(
      within(banner(rendered.container)!).getByText(
        /Added 1 item to today's plan\./,
      ),
    ).toBeTruthy();
  });

  it("reports a 409 conflict, re-reads the record, and changes nothing", async () => {
    const h = makeHarness("ready");
    await stage(h, [FLAG_A, FLAG_B]);
    const rendered = h.ui(<NeedsALookBanner />);
    await waitFor(() => expect(banner(rendered.container)).toBeTruthy());
    const load = vi.spyOn(h.controller, "loadCapacitiesSelections");
    vi.spyOn(h.controller, "saveCapacitiesSelections").mockRejectedValue(
      new ApiError(
        409,
        {
          code: "capacities_selections_conflict",
          expected_revision: 0,
          current_revision: 3,
        },
        "conflict",
      ),
    );

    fireEvent.click(
      rendered.getByRole("button", { name: "Okay to plan all 2 items" }),
    );

    await waitFor(() =>
      expect(within(banner(rendered.container)!).getByRole("alert")).toBeTruthy(),
    );
    expect(within(banner(rendered.container)!).getByRole("alert").textContent).toContain(
      "stored revision 3",
    );
    // The mount read happened before this spy was installed, so the one
    // counted call IS the 409 re-read: a second click writes a fresh revision.
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
  });
});

describe("NeedsALook row chip and non-gating", () => {
  function flagPlanRow(h: Harness, rowId: string) {
    const inputs = h.store.getState().inputs!;
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        assigned: inputs.assigned.map((row) =>
          row.id === rowId ? { ...row, capacitiesReviewReasons: ["status_unknown"] } : row,
        ),
      },
      ledger: h.store.getState().ledger!,
    });
  }

  it("keeps one persistent Needs a look chip on a flagged plan row only", async () => {
    const h = makeHarness("ready");
    flagPlanRow(h, "Note Processing");
    const rendered = h.ui(<Queue />);
    expect(rendered.container.querySelectorAll(".chip.qrow__look")).toHaveLength(1);
    // It rides the Capacities row, never a plain Todoist row.
    const capacitiesRow = rendered.getByText("Note Processing").closest(".qrow")!;
    expect(within(capacitiesRow as HTMLElement).getByText("Needs a look")).toBeTruthy();
    const todoistRow = rendered.getByText("Magic Mirror").closest(".qrow")!;
    expect(within(todoistRow as HTMLElement).queryByText("Needs a look")).toBeNull();
  });

  it("gates nothing: the plan membership and the rows are unchanged by the banner", async () => {
    const h = makeHarness("ready");
    await stage(h, [FLAG_A, FLAG_B]);
    const before = assignmentRows(h.store.getState()).assigned.map((r) => r.id);
    const rendered = h.ui(<NeedsALookBanner />);
    await waitFor(() => expect(banner(rendered.container)).toBeTruthy());
    expect(assignmentRows(h.store.getState()).assigned.map((r) => r.id)).toEqual(before);
    // Every control on the banner is available while rows need a look.
    expect(
      (rendered.getByRole("button", { name: "Okay to plan all 2 items" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("is one polite live region with named buttons", async () => {
    const h = makeHarness("ready");
    await stage(h, [FLAG_A]);
    const rendered = h.ui(<NeedsALookBanner />);
    await waitFor(() => expect(banner(rendered.container)).toBeTruthy());
    const box = banner(rendered.container)!;
    expect(box.getAttribute("role")).toBe("status");
    expect(within(box).getByRole("button", { name: "Okay to plan Draft launch brief" })).toBeTruthy();
    expect(within(box).getByRole("button", { name: "Okay to plan all 1 item" })).toBeTruthy();
    expect(within(box).getByRole("button", { name: "Okay to plan all 1 item" }).textContent).toBe(
      "Okay to plan all (1)",
    );
  });

  it("names the plural batch button and shows Adding… while saving", async () => {
    const h = makeHarness("ready");
    await stage(h, [FLAG_A, FLAG_B]);
    const rendered = h.ui(<NeedsALookBanner />);
    await waitFor(() => expect(banner(rendered.container)).toBeTruthy());
    const box = banner(rendered.container)!;
    expect(within(box).getByRole("button", { name: "Okay to plan all 2 items" }).textContent).toBe(
      "Okay to plan all (2)",
    );
    vi.spyOn(h.controller, "saveCapacitiesSelections").mockReturnValue(new Promise(() => {}));
    fireEvent.click(within(box).getByRole("button", { name: "Okay to plan all 2 items" }));
    await waitFor(() =>
      expect(within(banner(rendered.container)!).getByText("Adding…")).toBeTruthy(),
    );
  });
});
