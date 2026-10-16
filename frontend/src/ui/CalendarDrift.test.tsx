/* CalendarDrift.test.tsx — issue asing44/TDTB#19 (part B).

   Covers the panel's read-only rendering (moved / missing / added, the quiet
   match line, and the calendar-unavailable line), its hide-on-no-commit rule,
   the Check calendar button, the window-focus throttle, the controller's
   write-free adapter contract, and the tolerant wire projection. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { CalendarDrift } from "./CalendarDrift";
import { Ctx } from "./context";
import { createStore } from "../store/createStore";
import { Controller } from "../store/controller";
import { FixtureAdapter } from "../adapters/fixture";
import { projectCalendarDrift } from "../adapters/wire";
import type { Adapter } from "../adapters/adapter";
import type { CalendarDrift as CalendarDriftReport, CommitReport } from "../model/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function report(partial: Partial<CalendarDriftReport> = {}): CalendarDriftReport {
  return {
    status: "ok",
    day: "2026-10-16",
    hasCommit: true,
    reason: null,
    moved: [],
    missing: [],
    added: [],
    checkedAt: "2026-10-16T15:05:00",
    ...partial,
  };
}

const committedReport: CommitReport = { status: "ok", surfaces: [], verifyFailures: [] };

/** Stage a report with no committed commitPhase, so the auto-check does not
    fire and the assertions isolate the render or the explicit triggers. */
function stage(reportData: CalendarDriftReport, now?: () => number) {
  const store = createStore();
  const adapter = new FixtureAdapter("ready");
  const controller = new Controller(adapter, store.dispatch, store.getState, now);
  store.dispatch({ type: "CALENDAR_DRIFT_OK", report: reportData });
  return { store, adapter, controller };
}

describe("CalendarDrift", () => {
  it("lists moved, missing, and added blocks with 12-hour times", () => {
    const { controller, store } = stage(
      report({
        moved: [
          {
            name: "Mint Afternoon · 15:00",
            id: "e1",
            committed: { start: "2026-10-16T15:00:00", end: "2026-10-16T15:30:00" },
            live: { start: "2026-10-16T16:00:00", end: "2026-10-16T16:30:00" },
          },
        ],
        missing: [
          {
            name: "Deep Work",
            id: "e2",
            committed: { start: "2026-10-16T09:00:00", end: "2026-10-16T10:00:00" },
          },
        ],
        added: [
          {
            name: "Coffee",
            id: "e3",
            live: { start: "2026-10-16T11:00:00", end: "2026-10-16T11:30:00" },
          },
        ],
      }),
    );
    const rendered = render(
      <Ctx.Provider value={{ store, controller }}>
        <CalendarDrift />
      </Ctx.Provider>,
    );
    const text = rendered.container.textContent ?? "";
    expect(text).toContain("Mint Afternoon · 15:00 moved:");
    expect(text).toContain("3:30 PM");
    expect(text).toContain("4:30 PM");
    expect(text).toContain("Deep Work missing from Calendar");
    expect(text).toContain("Coffee added:");
    expect(rendered.getByText("Moved")).toBeTruthy();
    expect(rendered.getByText("Missing")).toBeTruthy();
    expect(rendered.getByText("Added")).toBeTruthy();
  });

  it("is hidden when hasCommit is false", () => {
    const { controller, store } = stage(report({ hasCommit: false, reason: "no commit today" }));
    const rendered = render(
      <Ctx.Provider value={{ store, controller }}>
        <CalendarDrift />
      </Ctx.Provider>,
    );
    expect(rendered.container.textContent).toBe("");
    expect(rendered.queryByText("Calendar drift")).toBeNull();
  });

  it("shows one quiet match line when nothing drifted", () => {
    const { controller, store } = stage(report());
    const rendered = render(
      <Ctx.Provider value={{ store, controller }}>
        <CalendarDrift />
      </Ctx.Provider>,
    );
    expect(rendered.getByText("Calendar matches today's commit")).toBeTruthy();
  });

  it("shows the calendar-unavailable line with the Check calendar button", () => {
    const { controller, store } = stage(
      report({ status: "calendar-unavailable", reason: "consent missing" }),
    );
    const rendered = render(
      <Ctx.Provider value={{ store, controller }}>
        <CalendarDrift />
      </Ctx.Provider>,
    );
    expect(rendered.getByText("Calendar unavailable: consent missing")).toBeTruthy();
    expect(rendered.getByRole("button", { name: "Check calendar" })).toBeTruthy();
  });

  it("Check calendar calls the adapter once", async () => {
    const { adapter, controller, store } = stage(report());
    const drift = vi.spyOn(adapter, "calendarDrift").mockResolvedValue(report());
    const rendered = render(
      <Ctx.Provider value={{ store, controller }}>
        <CalendarDrift />
      </Ctx.Provider>,
    );
    fireEvent.click(rendered.getByRole("button", { name: "Check calendar" }));
    await waitFor(() => expect(drift).toHaveBeenCalledTimes(1));
  });

  it("checks once on mount when the day has a committed state", async () => {
    const store = createStore();
    const adapter = new FixtureAdapter("ready");
    const drift = vi.spyOn(adapter, "calendarDrift").mockResolvedValue(report());
    const controller = new Controller(adapter, store.dispatch, store.getState);
    store.dispatch({ type: "COMMIT_DONE", report: committedReport });
    render(
      <Ctx.Provider value={{ store, controller }}>
        <CalendarDrift />
      </Ctx.Provider>,
    );
    await waitFor(() => expect(drift).toHaveBeenCalledTimes(1));
  });

  it("throttles the window-focus trigger to one call per 30s", async () => {
    let clock = 1_000_000;
    const store = createStore();
    const adapter = new FixtureAdapter("ready");
    const drift = vi.spyOn(adapter, "calendarDrift").mockResolvedValue(report());
    const controller = new Controller(adapter, store.dispatch, store.getState, () => clock);
    store.dispatch({ type: "CALENDAR_DRIFT_OK", report: report() });
    render(
      <Ctx.Provider value={{ store, controller }}>
        <CalendarDrift />
      </Ctx.Provider>,
    );

    window.dispatchEvent(new Event("focus"));
    await waitFor(() => expect(drift).toHaveBeenCalledTimes(1));

    window.dispatchEvent(new Event("focus"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(drift).toHaveBeenCalledTimes(1);

    clock += 30_000;
    window.dispatchEvent(new Event("focus"));
    await waitFor(() => expect(drift).toHaveBeenCalledTimes(2));
  });

  it("controller drift check calls only calendarDrift, never a write method", async () => {
    const calls: string[] = [];
    const fake = new Proxy({} as Adapter, {
      get(_target, prop) {
        const name = String(prop);
        return (..._args: unknown[]) => {
          calls.push(name);
          return name === "calendarDrift" ? Promise.resolve(report()) : Promise.resolve(undefined);
        };
      },
    });
    const store = createStore();
    const controller = new Controller(fake, store.dispatch, store.getState);
    await controller.checkCalendarDrift();
    expect(calls).toEqual(["calendarDrift"]);
  });
});

describe("projectCalendarDrift", () => {
  it("tolerates a missing optional field and an unknown status", () => {
    const empty = projectCalendarDrift({});
    expect(empty.status).toBe("ok");
    expect(empty.hasCommit).toBe(false);
    expect(empty.reason).toBeNull();
    expect(empty.moved).toEqual([]);
    expect(empty.missing).toEqual([]);
    expect(empty.added).toEqual([]);

    const unknown = projectCalendarDrift({ status: "something-else", hasCommit: true });
    expect(unknown.status).toBe("ok");

    const partial = projectCalendarDrift({
      status: "ok",
      hasCommit: true,
      moved: [{ name: "X", id: "1", committed: { start: "2026-10-16T15:00:00" } }],
    });
    expect(partial.moved[0].committed).toEqual({
      start: "2026-10-16T15:00:00",
      end: null,
    });
    expect(partial.moved[0].live).toEqual({ start: null, end: null });
  });
});
