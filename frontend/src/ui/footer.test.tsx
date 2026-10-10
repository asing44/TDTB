/* U7 page frame — one footer owns the back link, the blocked reason, and the
   primary action. No copy-prompt exists (R20b, retired in U5); every existing
   safety gate is preserved. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";
import { makeHarness } from "./test-harness";
import { Footer } from "./Footer";

afterEach(() => cleanup());

describe("U7 footer", () => {
  it("shows the blocked reason and no copy-prompt when sources are degraded", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs.sourceHealth = "degraded";
    });
    const r = h.ui(<Footer />);
    expect(r.getByText(/Can't sequence until sources are healthy\./)).toBeTruthy();
    expect(r.queryByText(/copy prompt/i)).toBeNull();
    expect(r.queryByRole("button", { name: /copy prompt/i })).toBeNull();
  });

  it("enables the primary action with its billed-call note when healthy and sequenceable", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Footer />);
    const primary = r
      .getByText(/^Sequence & commit \d+ blk$/)
      .closest("button") as HTMLButtonElement;
    expect(primary.disabled).toBe(false);
    expect(r.getByText(/1 billed call · 4 left today/)).toBeTruthy();
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
});
