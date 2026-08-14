/* failure-states.test — T9 rendering coverage for the degraded paths the
   state layer already models: empty day, source degradation, budget spent,
   sequence failure, stale date rollover, and partial commit. Store-level
   semantics are covered in store.test.ts; these assert the UI says the
   right thing. */

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { App } from "./App";
import { Queue } from "./Queue";
import { ActionDock } from "./ActionDock";
import { FooterBanners } from "./FooterBanners";
import { ReadinessStrip } from "./ReadinessStrip";
import { ApprovalDrawer } from "./ApprovalDrawer";
import { makeHarness } from "./test-harness";

describe("empty day", () => {
  it("queue explains the upstream contract instead of claiming everything placed", () => {
    const { ui } = makeHarness("ready", (sc) => {
      sc.inputs.assigned = [];
    });
    const { getByText, queryByText } = ui(<Queue />);
    expect(getByText(/No assigned items today/)).toBeTruthy();
    expect(queryByText(/Everything placed/)).toBeNull();
    expect(queryByText(/Needs placement/)).toBeNull();
  });
});

describe("source degradation", () => {
  it("readiness strip flags degraded sources and alerts carry the exact warning", () => {
    const { ui } = makeHarness("conflict");
    const strip = ui(<ReadinessStrip />);
    expect(strip.getByText(/Sources degraded/)).toBeTruthy();
    strip.unmount();
    const alertsView = makeHarness("conflict").ui(<FooterBanners />);
    // T12i: the roll-up floats as pills; details open on demand.
    fireEvent.click(alertsView.container.querySelector(".alert-pill--warning") as Element);
    expect(alertsView.getByText(/Todoist read failed \(timeout\)/)).toBeTruthy();
  });
});

describe("budget spent", () => {
  it("dock drops to manual-only messaging and disables Auto sequence", () => {
    const { ui } = makeHarness("ready", (sc) => {
      sc.ledger = { ...sc.ledger, spent: 4, remaining: 0 };
    });
    const { getByText } = ui(<ActionDock />);
    expect(getByText(/Billed budget spent — manual layout stays available/)).toBeTruthy();
    const btn = getByText(/^Auto sequence \d+ blk$/).closest("button") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("readiness strip budget chip shows 0 remaining in the warn style", () => {
    const { ui } = makeHarness("ready", (sc) => {
      sc.ledger = { ...sc.ledger, spent: 4, remaining: 0 };
    });
    const { getByText } = ui(<ReadinessStrip />);
    const chip = getByText(/Budget 0\/4/);
    expect(chip.className).toContain("chip--warn");
  });
});

describe("sequence failure", () => {
  it("failed sequence surfaces the exact error and keeps the retry path", () => {
    const { ui, store } = makeHarness("ready");
    const { getByText } = ui(
      <>
        <FooterBanners />
        <ActionDock />
      </>,
    );
    act(() => {
      store.dispatch({ type: "SEQUENCE_START" });
      store.dispatch({
        type: "SEQUENCE_FAIL",
        error: "sequence failed: judgment call timed out after 90s",
        ledger: { today: "2026-07-18", spent: 2, cap: 4, remaining: 2 },
      });
    });
    // T12i: the error is behind the blocking pill; the pill itself is the
    // always-visible signal.
    fireEvent.click(getByText(/1 blocking/).closest("button") as Element);
    expect(getByText(/judgment call timed out after 90s/)).toBeTruthy();
    // Manual recovery stays available: Auto sequence again, budget permitting.
    const btn = getByText(/^Auto sequence \d+ blk$/).closest("button") as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });
});

describe("stale date rollover", () => {
  it("a new valid_date resets the cockpit to setup with no staged plan", () => {
    const { ui, store, scenario } = makeHarness("sequenced");
    const { getByText, queryByText } = ui(<App />);
    expect(queryByText(/Sequence staged and valid|Preview the exact writes/)).toBeTruthy();
    act(() => {
      const rolled = structuredClone(scenario.inputs);
      rolled.validDate = "2026-07-19";
      rolled.daySetup.confirmed = false;
      store.dispatch({
        type: "INPUTS_LOADED",
        inputs: rolled,
        ledger: { today: "2026-07-19", spent: 0, cap: 4, remaining: 4 },
      });
    });
    const s = store.getState();
    expect(s.sequence).toBeNull();
    expect(s.shadow).toBeNull();
    expect(s.overrides).toEqual({});
    expect(getByText(/19 Jul/)).toBeTruthy();
    expect(getByText(/Confirm your day frame/)).toBeTruthy();
  });
});

describe("partial commit", () => {
  it("dock warns and the drawer renders per-surface status plus verify failures", () => {
    const { ui, store } = makeHarness("commit-preview");
    const { getByText } = ui(
      <>
        <ActionDock />
        <ApprovalDrawer />
      </>,
    );
    act(() => {
      store.dispatch({ type: "ARM_LIVE" });
      store.dispatch({ type: "COMMIT_START" });
      store.dispatch({
        type: "COMMIT_DONE",
        report: {
          status: "partial",
          surfaces: [
            { system: "todoist", status: "ok", detail: "2 scheduled" },
            { system: "calendar", status: "failed", detail: "BusyCal write refused" },
            { system: "vault", status: "skipped", detail: "aborted after calendar failure" },
          ],
          verifyFailures: ["calendar: 3 expected events, 0 found"],
        },
      });
    });
    expect(getByText(/Commit did not complete cleanly/)).toBeTruthy();
    expect(getByText(/calendar — failed · BusyCal write refused/)).toBeTruthy();
    expect(getByText(/vault — skipped/)).toBeTruthy();
    expect(getByText(/3 expected events, 0 found/)).toBeTruthy();
    // T21 (2026-07-24): the run's failed todoist verify was read as clean —
    // the dock must shout, not offer a neutral "View result".
    expect(getByText("Commit incomplete — view failures")).toBeTruthy();
    expect(getByText(/1 verification failure$/)).toBeTruthy();
  });
});
