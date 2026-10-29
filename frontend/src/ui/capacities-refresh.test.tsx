/* capacities-refresh.test.tsx — one explicit "Refresh now" control.

   Covers: the single Refresh now action (mode refresh, scope all) and the
   absence of a Rescan control, the wait countdown with read progress, Stop
   refresh while waiting, the finished-run summary, a failed run's outcome and
   warnings, the raw values behind the closed "Technical details" disclosure,
   polling only while a job is active, and no polling after unmount. */

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
  h.store.dispatch({ type: "UI", patch: { settingsPanel: "freshness" } });
  const rendered = h.ui(<CapacitiesSettingsDrawer />);
  await waitFor(() => expect(rendered.getByRole("button", { name: "Refresh now" })).toBeTruthy(), { timeout: 5000 });
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
  it("offers only Refresh now and stops the active job", async () => {
    const { h, rendered, section } = await openRefresh();

    expect(within(section).queryByRole("button", { name: /rescan/i })).toBeNull();
    expect(rendered.queryByText(/Rescan additionally re-lists structures/)).toBeNull();

    const start = vi.spyOn(h.controller, "startCapacitiesRefresh");
    fireEvent.click(within(section).getByRole("button", { name: "Refresh now" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith("refresh", "all"));
    await waitFor(() => expect(rendered.getByRole("button", { name: "Stop refresh" })).toBeTruthy());

    // Deliberate replacement of the former `toHaveBeenCalledWith("rescan", "all")`
    // assertion: the panel has no second mode to start.
    expect(start.mock.calls.every(([mode]) => mode === "refresh")).toBe(true);

    fireEvent.click(within(section).getByRole("button", { name: "Stop refresh" }));
    // The finished-run summary names the outcome in plain words.
    await waitFor(() => expect(rendered.getByText(/Stopped early/)).toBeTruthy());
    await waitFor(() =>
      expect(rendered.queryByRole("button", { name: "Stop refresh" })).toBeNull(),
    );
  });

  it("counts the wait down and offers Stop refresh while waiting", async () => {
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
      "Waiting for Capacities to allow more requests (about 40 s) · read 176 of 227",
    );
    expect(rendered.getByRole("button", { name: "Stop refresh" })).toBeTruthy();

    await vi.advanceTimersByTimeAsync(1000);
    expect(rendered.container.textContent).toContain("about 39 s");

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
    await waitFor(() => expect(rendered.queryByText(/Last refreshed /)).toBeTruthy());

    const summary = rendered.queryByText(/Last refreshed /)!.textContent ?? "";
    expect(summary).toContain("Up to date.");
    expect(summary).toContain("Read 176 items, reused 51.");
    // The plain summary is the only place the outcome appears: the headline
    // line must not repeat (or contradict) it on the next row.
    const outcomeLines = Array.from(rendered.container.querySelectorAll("p")).filter((el) =>
      el.textContent?.includes("Up to date."),
    );
    expect(outcomeLines).toHaveLength(1);
    expect(rendered.queryByRole("button", { name: "Stop refresh" })).toBeNull();
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
    await waitFor(() => expect(rendered.queryByText(/Last refreshed /)).toBeTruthy());

    expect(rendered.queryByText(/Last refreshed /)!.textContent).toContain(
      "Capacities returned no items, so nothing was updated.",
    );
    expect(rendered.getByText("Capacities run failed: provider returned 500")).toBeTruthy();
    expect(rendered.queryByRole("button", { name: "Stop refresh" })).toBeNull();
  });

  it("keeps the raw values in a Technical details disclosure closed by default", async () => {
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
    await waitFor(() => expect(rendered.queryByText(/Last refreshed /)).toBeTruthy());

    const details = rendered.container.querySelector(
      "details.settings-hub__details",
    ) as HTMLDetailsElement;
    expect(details).toBeTruthy();
    // Closed by default: the plain words are the surface, raw values are opt-in.
    expect(details.open).toBe(false);
    expect(details.querySelector("summary")!.textContent).toBe("Technical details");
    const raw = details.textContent ?? "";
    expect(raw).toContain("published");
    expect(raw).toContain("complete");
    expect(raw).toContain("227 listed · 176 read");
    // The plain summary is not hidden inside the disclosure it explains.
    expect(rendered.queryByText(/Last refreshed /)!.closest("details")).toBeNull();
  });

  it("polls while a job is active and stops after unmount", async () => {
    const h = makeHarness("ready");
    const load = vi
      .spyOn(h.controller, "loadCapacitiesRefreshStatus")
      .mockResolvedValue(runningStatus());
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "freshness" } });
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
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "freshness" } });
    h.ui(<CapacitiesSettingsDrawer />);
    await waitFor(() => expect(load.mock.calls.length).toBeGreaterThanOrEqual(1));
    const count = load.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 1400));
    expect(load.mock.calls.length).toBe(count);
  });
});
