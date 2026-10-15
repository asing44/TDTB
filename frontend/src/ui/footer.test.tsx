/* U7 page frame — one footer owns the back link, the blocked reason, and the
   primary action. The Plan-only "Copy prompt" fallback is restored (R20b) and
   the daily LLM call allowance is visible in all three footers; every existing
   safety gate is preserved. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/preact";
import { makeHarness as stageHarness, type Harness } from "./test-harness";
import { Footer } from "./Footer";
import { Ctx } from "./context";
import { createStore, type Store } from "../store/createStore";
import { Controller } from "../store/controller";
import { FixtureAdapter } from "../adapters/fixture";
import { sourceHealthOf } from "../adapters/wire";

/** Footer variants are selected by ui.screen. The app now boots on Set up day,
    so these Plan-footer tests explicitly land on the Plan screen; the
    off-Plan tests set their own screen. */
function makeHarness(
  name: Parameters<typeof stageHarness>[0],
  mutate?: Parameters<typeof stageHarness>[1],
): Harness {
  const h = stageHarness(name, mutate);
  h.store.dispatch({ type: "UI", patch: { screen: "plan", settingsPanel: null } });
  return h;
}

function renderFooterWithStore(store: Store) {
  const controller = new Controller(new FixtureAdapter("ready"), store.dispatch, store.getState);
  return render(
    <Ctx.Provider value={{ store, controller }}>
      <Footer />
    </Ctx.Provider>,
  );
}

afterEach(() => cleanup());

describe("U7 footer", () => {
  it("shows the blocked reason and keeps the Plan-only Copy prompt when sources are degraded", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs.sourceHealth = "degraded";
    });
    const r = h.ui(<Footer />);
    expect(r.getByText(/Can't sequence until sources are healthy\./)).toBeTruthy();
    // The Plan footer keeps the copy fallback available while degraded, and
    // the source-health alert itself stays truthful (it does not advertise it).
    expect(
      r.getByRole("button", { name: "Copy plan prompt for an external LLM" }),
    ).toBeTruthy();
  });

  it("enables the primary action with its billed-call note when healthy and sequenceable", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Footer />);
    const primary = r
      .getByText(/^Sequence & commit \d+ blk$/)
      .closest("button") as HTMLButtonElement;
    expect(primary.disabled).toBe(false);
    expect(r.getByText("1 billed call")).toBeTruthy();
    expect(r.container.querySelector(".footer__blocked")).toBeNull();
  });

  it("keeps the billed primary disabled while sources are degraded", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs.sourceHealth = "degraded";
    });
    const r = h.ui(<Footer />);
    const primary = r
      .getByText(/^Sequence & commit \d+ blk$/)
      .closest("button") as HTMLButtonElement;
    expect(primary.disabled).toBe(true);
  });

  it("does not block sequencing when the only warnings are skipped Capacities objects", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs.sourceWarnings = [
        "skipped Capacities object bb95d80d-573c-4aff-8faa-9dccdd2617a1 — not plannable: No title",
      ];
      sc.inputs.sourceHealth = sourceHealthOf(sc.inputs.sourceWarnings);
    });
    const r = h.ui(<Footer />);
    expect(r.queryByText(/Can't sequence until sources are healthy\./)).toBeNull();
    const primary = r
      .getByText(/^Sequence & commit \d+ blk$/)
      .closest("button") as HTMLButtonElement;
    expect(primary.disabled).toBe(false);
  });

  it("shows the ← Day setup back link on Plan tasks and opens day setup", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Footer />);
    const back = r.getByRole("button", { name: "← Day setup" });
    fireEvent.click(back);
    expect(h.store.getState().ui.screen).toBe("setup");
  });

  it("omits the back link away from Plan tasks", () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { screen: "connections" } });
    const r = h.ui(<Footer />);
    expect(r.queryByRole("button", { name: "← Day setup" })).toBeNull();
  });

  it("keeps the Plan-only Copy prompt off the Set up day and Connections footers", () => {
    for (const screen of ["setup", "connections"] as const) {
      const h = makeHarness("ready");
      h.store.dispatch({ type: "UI", patch: { screen } });
      const r = h.ui(<Footer />);
      expect(
        r.queryByRole("button", { name: "Copy plan prompt for an external LLM" }),
      ).toBeNull();
      r.unmount();
    }
  });
});

describe("Day budget allowance", () => {
  it("shows the ledger allowance on the Plan footer", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Footer />);
    expect(r.getByText("Day budget: 4/4 calls left")).toBeTruthy();
  });

  it("shows the exhausted state accurately in the warn style", () => {
    const h = makeHarness("ready", (sc) => {
      sc.ledger = { ...sc.ledger, spent: 4, remaining: 0 };
    });
    const r = h.ui(<Footer />);
    const chip = r.getByText("Day budget: 0/4 calls left");
    expect(chip.className).toContain("chip--warn");
  });

  it("shows the allowance on the Set up day footer", () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { screen: "setup" } });
    const r = h.ui(<Footer />);
    expect(r.getByText("Day budget: 4/4 calls left")).toBeTruthy();
  });

  it("shows the allowance on the Connections footer", () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { screen: "connections" } });
    const r = h.ui(<Footer />);
    expect(r.getByText("Day budget: 4/4 calls left")).toBeTruthy();
  });

  it("states unavailable rather than inventing a count when there is no ledger", () => {
    const r = renderFooterWithStore(createStore());
    expect(r.getByText("Day budget: unavailable")).toBeTruthy();
  });
});
