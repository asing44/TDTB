/* capacities-refresh.test.tsx — one explicit "Refresh sources" control.

   Covers: the single Refresh sources action (mode refresh, scope all) and the
   absence of a Rescan control, the wait countdown with read progress, Cancel
   while waiting, the published run summary, a failed run's outcome and
   warnings, polling only while a job is active, and no polling after
   unmount. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { CapacitiesRefreshPanel } from "./CapacitiesRefreshPanel";
import { CapacitiesSettingsDrawer } from "./CapacitiesSettingsDrawer";
import { makeHarness } from "./test-harness";
import {
  refreshProgressText,
} from "./useCapacitiesRefreshJob";
import type {
  CapacitiesRefreshProgress,
  CapacitiesRefreshStatus,
  CapacitiesRefreshWaiting,
} from "../model/types";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
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
      progress: { listed: 0, read: 0, toRead: null, waiting: null, types: {} },
      warnings: [],
    },
    progress: { listed: 0, read: 0, toRead: null, waiting: null, types: {} },
    warnings: [],
    coverage: {},
    snapshot: { present: false, generation: 0, revision: null, installedAt: null, memberCount: 0, typeCheckTimes: {} },
  };
}

/** One status built from the running baseline, with a patched job too. */
function statusWith(patch: {
  phase?: string;
  outcome?: string | null;
  progress?: Partial<CapacitiesRefreshProgress>;
  finishedAt?: number | null;
  warnings?: string[];
}): CapacitiesRefreshStatus {
  const base = runningStatus();
  const progress: CapacitiesRefreshProgress = { ...base.progress, ...(patch.progress ?? {}) };
  const phase = patch.phase ?? base.phase;
  const outcome = patch.outcome ?? null;
  const warnings = patch.warnings ?? [];
  return {
    ...base,
    phase,
    outcome,
    progress,
    warnings,
    job: {
      ...base.job!,
      phase,
      outcome,
      finishedAt: patch.finishedAt ?? null,
      progress,
      warnings,
    },
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

describe("refreshProgressText", () => {
  it("phrases reading progress and the wait countdown", () => {
    const reading = statusWith({ progress: { read: 176, toRead: 227 } });
    expect(refreshProgressText(reading, 1_000_000)).toBe("Reading 176/227");

    const noTarget = statusWith({ progress: { read: 176, toRead: null } });
    expect(refreshProgressText(noTarget, 1_000_000)).toBe("Reading 176 read");

    const waiting = statusWith({
      progress: {
        read: 176,
        toRead: 227,
        waiting: { reason: "rateLimited", endpoint: "/objects", retryAfter: 40, resumeAt: 1_040 },
      },
    });
    expect(refreshProgressText(waiting, 1_000_000)).toBe(
      "Capacities busy, resuming in 40s · 176/227",
    );
    expect(refreshProgressText(waiting, 1_001_000)).toBe(
      "Capacities busy, resuming in 39s · 176/227",
    );
    // A passed resume time never counts into the negative.
    expect(refreshProgressText(waiting, 2_000_000)).toBe(
      "Capacities busy, resuming in 0s · 176/227",
    );

    expect(refreshProgressText(statusWith({ phase: "complete" }), 1_000_000)).toBeNull();
    expect(refreshProgressText(null, 1_000_000)).toBeNull();
  });
});

describe("CapacitiesRefreshPanel", () => {
  it("offers only Refresh sources and cancels the active job", async () => {
    const { h, rendered, section } = await openRefresh();

    expect(within(section).queryByRole("button", { name: /rescan/i })).toBeNull();
    expect(rendered.queryByText(/Rescan additionally re-lists structures/)).toBeNull();

    const start = vi.spyOn(h.controller, "startCapacitiesRefresh");
    fireEvent.click(within(section).getByRole("button", { name: "Refresh sources" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith("refresh", "all"));
    await waitFor(() => expect(rendered.getByRole("button", { name: "Cancel refresh" })).toBeTruthy());

    // Deliberate replacement of the former `toHaveBeenCalledWith("rescan", "all")`
    // assertion: the panel has no second mode to start.
    expect(start.mock.calls.every(([mode]) => mode === "refresh")).toBe(true);

    fireEvent.click(within(section).getByRole("button", { name: "Cancel refresh" }));
    // The outcome cell and the finish summary both name the outcome.
    await waitFor(() => expect(rendered.getAllByText(/Cancelled/).length).toBeGreaterThan(0));
    await waitFor(() =>
      expect(rendered.queryByRole("button", { name: "Cancel refresh" })).toBeNull(),
    );
  });

  it("counts the wait down and offers Cancel while waiting", async () => {
    vi.useFakeTimers({ now: new Date("2026-10-19T12:00:00Z") });
    const resumeAt = Math.round(Date.now() / 1000) + 40;
    const waiting: CapacitiesRefreshWaiting = {
      reason: "rateLimited",
      endpoint: "/objects",
      retryAfter: 40,
      resumeAt,
    };
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesRefreshStatus").mockResolvedValue(
      statusWith({ progress: { listed: 227, read: 176, toRead: 227, waiting } }),
    );
    const rendered = h.ui(<CapacitiesRefreshPanel active={true} />);
    await vi.advanceTimersByTimeAsync(20);

    expect(rendered.container.textContent).toContain(
      "Capacities busy, resuming in 40s · 176/227",
    );
    expect(rendered.getByRole("button", { name: "Cancel refresh" })).toBeTruthy();

    await vi.advanceTimersByTimeAsync(1000);
    expect(rendered.container.textContent).toContain("Capacities busy, resuming in 39s");

    rendered.unmount();
    await vi.advanceTimersByTimeAsync(2000);
  });

  it("states what a finished run did and when", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesRefreshStatus").mockResolvedValue(
      statusWith({
        phase: "complete",
        outcome: "published",
        finishedAt: Date.UTC(2026, 9, 19, 12, 34, 0) / 1000,
        progress: { listed: 227, read: 176, toRead: 227 },
      }),
    );
    const rendered = h.ui(<CapacitiesRefreshPanel active={true} />);
    await waitFor(() => expect(rendered.queryByText(/Finished /)).toBeTruthy());

    const summary = rendered.queryByText(/Finished /)!.textContent ?? "";
    expect(summary).toContain("Published a new complete generation.");
    expect(summary).toContain("176 read, 51 reused");
    expect(rendered.queryByRole("button", { name: "Cancel refresh" })).toBeNull();
  });

  it("shows a failed run's outcome and warnings", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesRefreshStatus").mockResolvedValue(
      statusWith({
        phase: "failed",
        outcome: "noCapacities",
        finishedAt: Date.UTC(2026, 9, 19, 12, 34, 0) / 1000,
        progress: { listed: 227, read: 12, toRead: 227 },
        warnings: ["Capacities run failed: provider returned 500"],
      }),
    );
    const rendered = h.ui(<CapacitiesRefreshPanel active={true} />);
    await waitFor(() => expect(rendered.queryByText(/Finished /)).toBeTruthy());

    expect(rendered.queryByText(/Finished /)!.textContent).toContain(
      "No Capacities rows were produced; nothing was published.",
    );
    expect(rendered.getByText("Capacities run failed: provider returned 500")).toBeTruthy();
    expect(rendered.queryByRole("button", { name: "Cancel refresh" })).toBeNull();
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
    const terminal = statusWith({ phase: "complete", outcome: "published" });
    const load = vi.spyOn(h.controller, "loadCapacitiesRefreshStatus").mockResolvedValue(terminal);
    h.store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: true } });
    h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(load.mock.calls.length).toBeGreaterThanOrEqual(1));
    const count = load.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 1400));
    expect(load.mock.calls.length).toBe(count);
  });
});
