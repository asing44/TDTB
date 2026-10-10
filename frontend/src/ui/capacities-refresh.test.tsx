/* capacities-refresh.test.tsx — explicit Refresh / Rescan controls.

   Covers: the two DISTINCT actions (refresh vs rescan), cancel of an active
   job, polling only while a job is active, and no polling after unmount. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { CapacitiesSettingsDrawer } from "./CapacitiesSettingsDrawer";
import { makeHarness } from "./test-harness";
import type { CapacitiesRefreshStatus } from "../model/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function runningStatus(): CapacitiesRefreshStatus {
  return {
    configured: true,
    phase: "listing",
    outcome: null,
    mode: "refresh",
    scope: "all",
    job: {
      jobId: "job-x",
      mode: "refresh",
      scope: "all",
      phase: "listing",
      outcome: null,
      revision: 0,
      generation: 0,
      startedAt: 1,
      updatedAt: 1,
      finishedAt: null,
      progress: { listed: 0, read: 0, types: {} },
      warnings: [],
    },
    progress: { listed: 0, read: 0, types: {} },
    warnings: [],
    coverage: {},
    snapshot: { present: false, generation: 0, revision: null, installedAt: null, memberCount: 0, typeCheckTimes: {} },
  };
}

async function openRefresh() {
  const h = makeHarness("ready");
  h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
  const rendered = h.ui(<CapacitiesSettingsDrawer />);
  await waitFor(() => expect(rendered.getByRole("button", { name: "Refresh sources" })).toBeTruthy(), { timeout: 5000 });
  const section = rendered.container.querySelector('[data-settings-section="refresh"]') as HTMLElement;
  return { h, rendered, section };
}

describe("CapacitiesRefreshPanel", () => {
  it("starts the two distinct modes and cancels the active job", async () => {
    const { h, rendered, section } = await openRefresh();
    const start = vi.spyOn(h.controller, "startCapacitiesRefresh");

    fireEvent.click(within(section).getByRole("button", { name: "Refresh sources" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith("refresh", "all"));
    await waitFor(() => expect(rendered.getByRole("button", { name: "Cancel refresh" })).toBeTruthy());

    fireEvent.click(within(section).getByRole("button", { name: "Cancel refresh" }));
    await waitFor(() => expect(rendered.getByText(/Cancelled/)).toBeTruthy());
    await waitFor(() =>
      expect(rendered.queryByRole("button", { name: "Cancel refresh" })).toBeNull(),
    );

    fireEvent.click(within(section).getByRole("button", { name: "Rescan structures" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith("rescan", "all"));
  });

  it("polls while a job is active and stops after unmount", async () => {
    const h = makeHarness("ready");
    const load = vi
      .spyOn(h.controller, "loadCapacitiesRefreshStatus")
      .mockResolvedValue(runningStatus());
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    const rendered = h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(load.mock.calls.length).toBeGreaterThanOrEqual(1));

    // One poll interval (1200ms) must produce at least one more status read.
    await new Promise((resolve) => setTimeout(resolve, 1400));
    const whileMounted = load.mock.calls.length;
    expect(whileMounted).toBeGreaterThanOrEqual(2);

    rendered.unmount();
    await new Promise((resolve) => setTimeout(resolve, 1400));
    expect(load.mock.calls.length).toBe(whileMounted);
  });

  it("does not poll once the job reaches a terminal phase", async () => {
    const h = makeHarness("ready");
    const terminal: CapacitiesRefreshStatus = {
      ...runningStatus(),
      phase: "complete",
      outcome: "published",
      job: { ...runningStatus().job!, phase: "complete", outcome: "published" },
    };
    const load = vi.spyOn(h.controller, "loadCapacitiesRefreshStatus").mockResolvedValue(terminal);
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(load.mock.calls.length).toBeGreaterThanOrEqual(1));
    const count = load.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 1400));
    expect(load.mock.calls.length).toBe(count);
  });
});
