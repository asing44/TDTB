/* failure-states.test — T9 rendering coverage for the degraded paths the
   state layer already models: empty day, source degradation, budget spent,
   sequence failure, stale date rollover, and partial commit. Store-level
   semantics are covered in store.test.ts; these assert the UI says the
   right thing. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { App } from "./App";
import { Queue } from "./Queue";
import { ActionDock } from "./ActionDock";
import { FooterBanners } from "./FooterBanners";
import { ReadinessStrip } from "./ReadinessStrip";
import { ApprovalDrawer } from "./ApprovalDrawer";
import { CalendarImpact } from "./CalendarImpact";
import { makeHarness } from "./test-harness";
import { fingerprintFixedInputs } from "../model/fingerprint";
import { fixedInputsOf } from "../fixtures/scenarios";
import type { PlanInputs } from "../model/types";

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

// -- Capacities partial coverage (always-visible surface) --------------------

const capacitiesBudgetWarning = (evaluated: number, deferred: number): string =>
  `Capacities partial — ${evaluated} evaluated · ${deferred} deferred across ` +
  "contributing structures. Content-read budget reached. Wait at least a " +
  "minute, then Refresh sources to continue.";

function withDeferred(inputs: PlanInputs, deferred: number): PlanInputs {
  return {
    ...inputs,
    sourceWarnings: [capacitiesBudgetWarning(20, deferred)],
    sourceHealth: "degraded",
  };
}

/** Dispatch a completed refresh whose read model was `inputs`. */
function refreshOk(
  h: ReturnType<typeof makeHarness>,
  inputs: PlanInputs,
  at = "2026-07-18T09:15:00.000Z",
): void {
  h.store.dispatch({
    type: "SOURCE_REFRESH_OK",
    inputs,
    ledger: h.store.getState().ledger!,
    fingerprint: fingerprintFixedInputs(fixedInputsOf(inputs)),
    anchoredSourceFingerprint: inputs.anchoredSourceFingerprint,
    planningConfigFingerprint: inputs.planningConfigFingerprint,
    at,
  });
}

describe("Capacities partial coverage", () => {
  it("shows the verbatim warning beside Sources on initial load, without opening anything", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs = withDeferred(sc.inputs, 51);
    });
    const { getByText, container } = h.ui(<ReadinessStrip />);
    const readiness = container.querySelector('[aria-label="Readiness"]') as HTMLElement;
    expect(readiness.textContent).toContain(capacitiesBudgetWarning(20, 51));
    expect(getByText(/Content-read budget reached/)).toBeTruthy();
    // The rail carries only the adapter's verbatim warning. The hardcoded
    // cache/window explainer was removed, so none of its copy may return.
    expect(readiness.textContent).not.toMatch(
      /cached machine-locally for 5 minutes/,
    );
    expect(readiness.textContent).not.toMatch(/provider's request window/);
  });

  it("stays visible while a refresh is loading", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs = withDeferred(sc.inputs, 51);
    });
    const { getByText } = h.ui(<ReadinessStrip />);
    act(() => {
      h.store.dispatch({ type: "SOURCE_REFRESH_START" });
    });
    expect(getByText(/51 deferred across contributing structures/)).toBeTruthy();
    expect(getByText(/Sources ⟳ refreshing…/)).toBeTruthy();
  });

  it("keeps the last read's warning when a refresh fails", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs = withDeferred(sc.inputs, 51);
    });
    const { getByText } = h.ui(<ReadinessStrip />);
    act(() => {
      h.store.dispatch({ type: "SOURCE_REFRESH_FAIL", error: "provider timeout" });
    });
    expect(getByText(/51 deferred across contributing structures/)).toBeTruthy();
    expect(
      getByText(/Refresh failed: provider timeout — showing last good data/),
    ).toBeTruthy();
  });

  it("a completed 'no changes' refresh still reports the remaining deferrals", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs = withDeferred(sc.inputs, 51);
    });
    const { container, getByText } = h.ui(<ReadinessStrip />);
    act(() => {
      refreshOk(h, h.store.getState().inputs!);
    });
    const status = container.querySelector(".rail__refresh") as HTMLElement;
    expect(status.textContent).toContain("no changes");
    expect(status.textContent).toContain("Capacities partial: 51 deferred");
    expect(getByText(/51 deferred across contributing structures/)).toBeTruthy();
  });

  it("successive refreshes show the deferral count shrinking", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs = withDeferred(sc.inputs, 51);
    });
    const { queryByText, container } = h.ui(<ReadinessStrip />);
    const warning = () =>
      (container.querySelector(".rail__partial-warning") as HTMLElement).textContent!;
    expect(warning()).toContain("51 deferred");
    act(() => {
      refreshOk(h, withDeferred(h.store.getState().inputs!, 30), "2026-07-18T09:20:00.000Z");
    });
    expect(warning()).toContain("30 deferred");
    expect(queryByText(/51 deferred/)).toBeNull();
  });

  it("never auto-continues: a partial state schedules no refresh and no retry", () => {
    vi.useFakeTimers();
    try {
      const h = makeHarness("ready", (sc) => {
        sc.inputs = withDeferred(sc.inputs, 51);
      });
      const spy = vi.spyOn(h.controller, "refreshSources");
      const { getByText } = h.ui(<ReadinessStrip />);
      expect(vi.getTimerCount()).toBe(0);
      vi.advanceTimersByTime(15 * 60 * 1000);
      expect(spy).not.toHaveBeenCalled();
      // The warning survives the wait: it is not a transient toast, and the
      // surface never self-clears or self-retries.
      expect(getByText(/51 deferred/)).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
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
    const chip = getByText(/Calls 0\/4/);
    expect(chip.className).toContain("chip--warn");
  });

});

describe("ignored calendar sources", () => {
  it("keeps an ignored-only calendar frame visible as an exclusion note", () => {
    const ignored = {
      id: "Focus timer",
      name: "Focus timer",
      kind: "calendar" as const,
      start: "10:00",
      end: "10:30",
      durationMin: 30,
      overlapAllowed: false,
      on: true,
      skipToday: false,
      calendarId: "focus",
      calendarTitle: "Session: focus",
      capacityClass: "ignored" as const,
    };
    const h = makeHarness("ready", (sc) => {
      sc.inputs.anchored = [ignored];
    });
    const { getByRole, getByText, container, queryByText } = h.ui(<CalendarImpact />);

    expect(getByRole("region", { name: "Calendar impact" })).toBeTruthy();
    expect(getByText("1 ignored calendar source excluded")).toBeTruthy();
    expect(container.querySelector(".calendar-impact__list")).toBeNull();
    expect(queryByText("Focus timer")).toBeNull();
    expect(h.store.getState().inputs?.anchored).toEqual([ignored]);
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
