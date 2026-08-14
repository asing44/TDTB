import { describe, expect, it, vi } from "vitest";
import { createStore } from "./createStore";
import { Controller } from "./controller";
import { FixtureAdapter } from "../adapters/fixture";
import { canLiveCommit, defectsResolved, dockState, effectiveAnchoredBlocks } from "./store";
import { calendarWalls, mintWalls } from "../model/overflow";
import { ApiError } from "../adapters/api";
import type { ScenarioName } from "../fixtures/scenarios";
import type { ShadowDiff } from "../model/types";

function toMin(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

function harness(scenario: ScenarioName) {
  const store = createStore();
  const adapter = new FixtureAdapter(scenario);
  const controller = new Controller(adapter, store.dispatch, store.getState);
  return { store, adapter, controller };
}

describe("controller happy path (ready scenario)", () => {
  it("load → setup → sequence → shadow → arm → live commit → verified", async () => {
    const { store, controller } = harness("ready");
    await controller.load();
    let s = store.getState();
    expect(s.loaded).toBe(true);
    expect(s.validDate).toBe("2026-07-18");

    await controller.saveDaySetup({ ...s.daySetup, confirmed: true });
    expect(dockState(store.getState())).toBe("sequence");

    await controller.autoSequence();
    store.dispatch({ type: "ACCEPT_DEFECTS" }); // fixture warnings are designed acceptable defects (LD 24)
    s = store.getState();
    expect(s.seqPhase).toBe("valid");
    expect(s.fingerprint).not.toBeNull();
    expect(s.ledger!.spent).toBe(1);
    expect(s.sequence!.some((r) => r.kind === "zone")).toBe(true);

    await controller.shadowPreview();
    s = store.getState();
    expect(s.shadowPhase).toBe("current");
    expect(canLiveCommit(s)).toBe(false); // not armed yet — second gate

    controller.armLive();
    expect(canLiveCommit(store.getState())).toBe(true);

    await controller.requestLiveCommit();
    s = store.getState();
    expect(s.commitPhase).toBe("done");
    expect(s.commitReport!.verifyFailures).toEqual([]);
    expect(dockState(s)).toBe("verified");
  }, 15000);
});

describe("Day Setup save is free (T12 qualification, 2026-07-26)", () => {
  it("persists and refreshes capacity WITHOUT spending a billed call", async () => {
    const { store, adapter, controller } = harness("ready");
    await controller.load();
    const save = vi.spyOn(adapter, "saveDaySetup");
    const capacity = vi.spyOn(adapter, "capacityPreview");
    const sequence = vi.spyOn(adapter, "autoSequence");

    await controller.saveDaySetup({
      ...store.getState().daySetup,
      confirmed: true,
      dayPreset: "Workday",
      workAllotmentMinutes: 0,
    });

    expect(save).toHaveBeenCalledTimes(1);
    expect(capacity).toHaveBeenCalledTimes(1);
    // The whole point: confirming the day frame must not reach the paid path.
    // T18g wired save -> autoSequence, which spent the call before the
    // allocator surface had been seen at all.
    expect(sequence).not.toHaveBeenCalled();
    expect(save.mock.invocationCallOrder[0]).toBeLessThan(capacity.mock.invocationCallOrder[0]);
    expect(store.getState().daySetup.workAllotmentMinutes).toBe(0);
  }, 15000);

  it("uses the post-save planning fingerprint for the paid request", async () => {
    const { store, adapter, controller } = harness("ready");
    await controller.load();
    const fixed = await adapter.readFixedInputs();
    vi.spyOn(adapter, "readFixedInputs").mockResolvedValue({
      ...fixed,
      planningConfigFingerprint: "post-save-planning-fingerprint",
    });
    const sequence = vi.spyOn(adapter, "autoSequence");

    await controller.saveDaySetup({
      ...store.getState().daySetup,
      confirmed: true,
      dayPreset: "Weekend",
      workAllotmentMinutes: 0,
    });
    await controller.autoSequence();

    expect(sequence.mock.calls[0][0].planningConfigFingerprint).toBe(
      "post-save-planning-fingerprint",
    );
    expect(store.getState().planningConfigFingerprint).toBe(
      "post-save-planning-fingerprint",
    );
  }, 15000);

  it("omitted setup semantics preserve the current dated overrides", async () => {
    const { store, controller } = harness("ready");
    await controller.load();
    await controller.saveDaySetup({
      ...store.getState().daySetup,
      confirmed: true,
      dayPreset: "Weekend",
      workAllotmentMinutes: 0,
    });
    const next = { ...store.getState().daySetup };
    delete next.dayPreset;
    delete next.workAllotmentMinutes;
    await controller.saveDaySetup(next);
    expect(store.getState().daySetup.dayPreset).toBe("Weekend");
    expect(store.getState().daySetup.workAllotmentMinutes).toBe(0);
  });

  it("keeps the previous plan visible and stale when regeneration fails", async () => {
    const { store, adapter, controller } = harness("ready");
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();
    const previous = store.getState().sequence;
    vi.spyOn(adapter, "autoSequence").mockRejectedValueOnce(new Error("judgment unavailable"));

    await controller.saveDaySetup({
      ...store.getState().daySetup,
      workAllotmentMinutes: 180,
    });
    await controller.autoSequence();

    expect(store.getState().sequence).toBe(previous);
    expect(store.getState().seqPhase).toBe("failed");
    expect(store.getState().seqError).toContain("judgment unavailable");
  }, 15000);

  it("never sequences during load, source refresh, or ordinary setup save", async () => {
    const { store, adapter, controller } = harness("ready");
    const sequence = vi.spyOn(adapter, "autoSequence");
    await controller.load();
    await controller.refreshSources();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    expect(sequence).not.toHaveBeenCalled();
  });
});

describe("T18g placement pins", () => {
  it("pins exact manual edits, sends them to regeneration, and Reset placement releases", async () => {
    const { store, adapter, controller } = harness("ready");
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();

    const validate = vi.spyOn(adapter, "validateSequence");
    controller.moveRow("Magic Mirror", "11:00");
    expect(store.getState().pendingPinnedRows).toEqual([
      expect.objectContaining({ id: "Magic Mirror", start: "11:00", end: "12:30" }),
    ]);
    expect(store.getState().pinnedRows).toEqual([]);
    await controller.revalidate();
    expect(validate.mock.calls.at(-1)?.[1].pinnedRows).toEqual([]);

    const sequence = vi.spyOn(adapter, "autoSequence");
    await controller.autoSequence();
    expect(sequence.mock.calls[0][0].pinnedRows).toEqual([
      expect.objectContaining({ id: "Magic Mirror", start: "11:00", end: "12:30" }),
    ]);

    controller.resetPlacement("Magic Mirror");
    expect(store.getState().pendingPinnedRows).toEqual([]);
    expect(store.getState().pinnedRows).toEqual([
      expect.objectContaining({ id: "Magic Mirror", start: "11:00", end: "12:30" }),
    ]);
    expect(store.getState().seqPhase).toBe("dirty");
    expect(store.getState().sequence?.some((row) => row.id === "Magic Mirror")).toBe(true);
  }, 20000);
});

describe("sequence excludes overridden items", () => {
  it("an excluded item never reaches the staged sequence", async () => {
    const { store, controller } = harness("ready");
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    controller.setOverride("Press", false, null);
    await controller.autoSequence();
    store.dispatch({ type: "ACCEPT_DEFECTS" }); // fixture warnings are designed acceptable defects (LD 24)
    const s = store.getState();
    expect(s.sequence!.some((r) => r.id === "Press")).toBe(false);
  }, 15000);
});

describe("today-only shaping reaches commit payloads (T6)", () => {
  it("shadowCommit and liveCommit receive the shaped context", async () => {
    const { store, adapter, controller } = harness("ready");
    const shadowSpy = vi.spyOn(adapter, "shadowCommit");
    const liveSpy = vi.spyOn(adapter, "liveCommit");
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    controller.setOverride("Press", false, null); // excluded today
    controller.setOverride("Magic Mirror", true, 4); // duration override
    await controller.autoSequence();
    store.dispatch({ type: "ACCEPT_DEFECTS" }); // fixture warnings are designed acceptable defects (LD 24)
    await controller.shadowPreview();
    controller.armLive();
    await controller.requestLiveCommit();

    for (const spy of [shadowSpy, liveSpy]) {
      const ctx = spy.mock.calls[0][1];
      expect(ctx.included.some((i) => i.id === "Press")).toBe(false);
      expect(ctx.included.find((i) => i.id === "Magic Mirror")!.blocks).toBe(4);
    }
  }, 15000);
});

describe("T12 anchored edits", () => {
  it("saves blocks through /day-setup, refreshes capacity, and dirties a staged plan", async () => {
    const { store, adapter, controller } = harness("ready");
    const save = vi.spyOn(adapter, "saveDaySetup");
    const capacity = vi.spyOn(adapter, "capacityPreview");
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();
    store.dispatch({ type: "ACCEPT_DEFECTS" }); // fixture warnings are designed acceptable defects (LD 24)
    save.mockClear();
    capacity.mockClear();
    await controller.saveAnchoredOverride("Foods Dinner", {
      on: true, skipToday: false, time: "18:30", blocks: 1,
    });
    expect(save).toHaveBeenCalled();
    expect(save.mock.calls[0][0].anchored["Foods Dinner"].blocks).toBe(1);
    expect(capacity).toHaveBeenCalled();
    expect(store.getState().daySetup.anchored["Foods Dinner"].time).toBe("18:30");
    expect(store.getState().seqPhase).toBe("dirty");
  });

  it("sanitizes Calendar overrides to plan participation + local accounting projection (FEEDBACK-09)", async () => {
    // LD19 stands: time edits never reach a calendar row. What crosses is the
    // per-day not-attending flag and the local accounting duration (blocks) —
    // a projection that changes counted capacity today only, never the event.
    const { store, adapter, controller } = harness("ready");
    await controller.load();
    const save = vi.spyOn(adapter, "saveDaySetup");
    await controller.saveAnchoredOverride("Trinoor Standup", {
      on: true, skipToday: true, time: "10:00", blocks: 2,
    });
    expect(save).toHaveBeenCalledTimes(1);
    const sent = save.mock.calls[0][0];
    expect(sent.anchored["Trinoor Standup"]).toEqual({
      on: true, skipToday: true, time: null, blocks: 2,
    });
    expect(store.getState().daySetup.anchored["Trinoor Standup"]).toEqual({
      on: true, skipToday: true, time: null, blocks: 2,
    });
  });

  it("a Calendar override without blocks stays byte-identical to the T28 wire shape", async () => {
    const { adapter, controller } = harness("ready");
    await controller.load();
    const save = vi.spyOn(adapter, "saveDaySetup");
    await controller.saveAnchoredOverride("Trinoor Standup", {
      on: true, skipToday: true, time: null,
    });
    const sent = save.mock.calls[0][0];
    expect(sent.anchored["Trinoor Standup"]).toEqual({
      on: true, skipToday: true, time: null,
    });
  });

  it("restore keeps an explicit attending entry so it beats stale server rows (T28)", async () => {
    const { adapter, controller } = harness("ready");
    await controller.load();
    const save = vi.spyOn(adapter, "saveDaySetup");
    await controller.saveAnchoredOverride("Trinoor Standup", {
      on: true, skipToday: true, time: null,
    });
    await controller.saveAnchoredOverride("Trinoor Standup", {
      on: true, skipToday: false, time: null,
    });
    const sent = save.mock.calls[1][0];
    expect(sent.anchored["Trinoor Standup"]).toEqual({
      on: true, skipToday: false, time: null,
    });
  });
});

describe("drift invalidation (locked decision 17)", () => {
  it("calendar drift between sequence and shadow invalidates the plan", async () => {
    const { store, adapter, controller } = harness("ready");
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();
    store.dispatch({ type: "ACCEPT_DEFECTS" }); // fixture warnings are designed acceptable defects (LD 24)
    adapter.simulateDrift();
    await controller.shadowPreview();
    const s = store.getState();
    expect(s.shadowPhase).toBe("none");
    expect(s.shadow).toBeNull();
    expect(s.seqPhase).toBe("dirty");
    expect(s.fingerprint).toBeNull();
  }, 15000);

  it("drift between shadow and live commit aborts the commit", async () => {
    const { store, adapter, controller } = harness("ready");
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();
    store.dispatch({ type: "ACCEPT_DEFECTS" }); // fixture warnings are designed acceptable defects (LD 24)
    await controller.shadowPreview();
    controller.armLive();
    adapter.simulateDrift();
    await controller.requestLiveCommit();
    const s = store.getState();
    expect(s.commitPhase).toBe("idle"); // aborted, nothing written
    expect(s.commitReport).toBeNull();
    expect(s.shadowPhase).toBe("none"); // plan invalidated
  }, 15000);

  it("a fixed-source read failure blocks shadow instead of passing unchanged", async () => {
    const { store, adapter, controller } = harness("ready");
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();
    store.dispatch({ type: "ACCEPT_DEFECTS" }); // fixture warnings are designed acceptable defects (LD 24)
    adapter.simulateSourceFailure();
    await controller.shadowPreview();
    const s = store.getState();
    expect(s.shadowPhase).toBe("none");
    expect(s.shadow).toBeNull();
  }, 15000);

  it("raw anchored drift hidden by the same effective override invalidates the plan", async () => {
    const { store, adapter, controller } = harness("ready");
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();
    store.dispatch({ type: "ACCEPT_DEFECTS" }); // fixture warnings are designed acceptable defects (LD 24)
    expect(store.getState().anchoredSourceFingerprint).toBeTruthy();
    adapter.simulateAnchoredSourceDrift();
    await controller.shadowPreview();
    const s = store.getState();
    expect(s.seqPhase).toBe("dirty");
    expect(s.fingerprint).toBeNull();
    expect(s.anchoredSourceFingerprint).toBeNull();
  }, 15000);
});

describe("budget exhaustion", () => {
  it("sequence failure surfaces the error and the manual path stays open", async () => {
    const { store, controller } = harness("conflict");
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();
    store.dispatch({ type: "ACCEPT_DEFECTS" }); // fixture warnings are designed acceptable defects (LD 24)
    const s = store.getState();
    expect(s.seqPhase).toBe("failed");
    expect(s.seqError).toContain("overlaps Sudsing");
    // Manual placement still works after a failed auto sequence:
    controller.placeRow("Note Processing", "10:00");
    expect(store.getState().sequence!.some((r) => r.id === "Note Processing")).toBe(true);
  }, 15000);
});

describe("T8 live-commit failure handling (mocked — no real writes)", () => {
  async function armedHarness() {
    const h = harness("ready");
    await h.controller.load();
    await h.controller.saveDaySetup({ ...h.store.getState().daySetup, confirmed: true });
    await h.controller.autoSequence();
    h.store.dispatch({ type: "ACCEPT_DEFECTS" });
    await h.controller.shadowPreview();
    h.controller.armLive();
    expect(canLiveCommit(h.store.getState())).toBe(true);
    return h;
  }

  it("409 single-flight rejects cleanly: nothing written, plan intact, re-armable", async () => {
    const { store, adapter, controller } = await armedHarness();
    vi.spyOn(adapter, "liveCommit").mockRejectedValue(
      new ApiError(409, null, "live commit already in flight — retry after it returns"),
    );
    await controller.requestLiveCommit();
    const s = store.getState();
    expect(s.commitPhase).toBe("idle");
    expect(s.commitReport).toBeNull();
    expect(s.commitError).toMatch(/in flight/);
    expect(s.liveArmed).toBe(false);
    expect(s.sequence).not.toBeNull();
    expect(s.shadowPhase).toBe("current"); // preview intact — arm again to retry
    controller.armLive();
    expect(canLiveCommit(store.getState())).toBe(true);
  });

  it("422 plan refusal rejects cleanly with the server's detail", async () => {
    const { store, controller, adapter } = await armedHarness();
    vi.spyOn(adapter, "liveCommit").mockRejectedValue(
      new ApiError(422, null, "plan refused: cannot plan 1 item(s)"),
    );
    await controller.requestLiveCommit();
    const s = store.getState();
    expect(s.commitPhase).toBe("idle");
    expect(s.commitError).toMatch(/plan refused/);
    expect(s.liveArmed).toBe(false);
  });

  it("a mid-write network failure is a FAILED report — writes are unknown, never idle", async () => {
    const { store, controller, adapter } = await armedHarness();
    vi.spyOn(adapter, "liveCommit").mockRejectedValue(new Error("fetch failed"));
    await controller.requestLiveCommit();
    const s = store.getState();
    expect(s.commitPhase).toBe("failed");
    expect(s.commitReport?.verifyFailures).toContain("fetch failed");
    expect(s.commitError).toBeNull();
  });

  it("a partial report lands as phase partial with per-surface statuses", async () => {
    const { store, controller, adapter } = await armedHarness();
    vi.spyOn(adapter, "liveCommit").mockResolvedValue({
      status: "partial",
      surfaces: [
        { system: "todoist", status: "failed", detail: "no client" },
        { system: "vault", status: "ok", detail: null },
      ],
      verifyFailures: ["todoist: create failed"],
    });
    await controller.requestLiveCommit();
    const s = store.getState();
    expect(s.commitPhase).toBe("partial");
    expect(s.commitReport?.surfaces).toHaveLength(2);
    expect(dockState(s)).toBe("partial");
  });

  it("a new COMMIT_START clears the prior rejection banner", async () => {
    const { store, controller, adapter } = await armedHarness();
    vi.spyOn(adapter, "liveCommit")
      .mockRejectedValueOnce(new ApiError(409, null, "in flight"))
      .mockResolvedValueOnce({ status: "ok", surfaces: [], verifyFailures: [] });
    await controller.requestLiveCommit();
    expect(store.getState().commitError).toBe("in flight");
    controller.armLive();
    await controller.requestLiveCommit();
    const s = store.getState();
    expect(s.commitError).toBeNull();
    expect(s.commitPhase).toBe("done");
  });
});

describe("T8 shadow blockers gate arm at the STATE level", () => {
  async function sequencedHarness() {
    const h = harness("ready");
    await h.controller.load();
    await h.controller.saveDaySetup({ ...h.store.getState().daySetup, confirmed: true });
    await h.controller.autoSequence();
    h.store.dispatch({ type: "ACCEPT_DEFECTS" });
    return h;
  }
  const blockedShadow = (patch: Partial<ShadowDiff>): ShadowDiff => ({
    entries: [],
    unavailableSurfaces: [],
    counts: { "would-create": 0, "would-update": 0, "no-op": 0, conflict: 0, unavailable: 0 },
    ...patch,
  });

  it("a conflict entry in the current shadow refuses ARM_LIVE and canLiveCommit", async () => {
    const { store, controller, adapter } = await sequencedHarness();
    vi.spyOn(adapter, "shadowCommit").mockResolvedValue(
      blockedShadow({
        entries: [{
          step: "C", system: "vault", action: "set-flag", name: "Sample",
          idOrPath: "50 - Operations/Projects/Sample.md", time: null,
          durationMin: 0, classification: "conflict", detail: {},
        }],
      }),
    );
    await controller.shadowPreview();
    expect(store.getState().shadowPhase).toBe("current");
    controller.armLive();
    expect(store.getState().liveArmed).toBe(false);
    expect(canLiveCommit(store.getState())).toBe(false);
  });

  it("an unavailable surface refuses ARM_LIVE too", async () => {
    const { store, controller, adapter } = await sequencedHarness();
    vi.spyOn(adapter, "shadowCommit").mockResolvedValue(
      blockedShadow({ unavailableSurfaces: ["calendar"] }),
    );
    await controller.shadowPreview();
    controller.armLive();
    expect(store.getState().liveArmed).toBe(false);
    expect(canLiveCommit(store.getState())).toBe(false);
  });
});

describe("out-of-order response guard", () => {
  it("a slow stale /validate-sequence response never overwrites a newer one", async () => {
    const { store, adapter, controller } = harness("sequenced");
    await controller.load();
    // First call resolves LAST (stale), second resolves first (current).
    const real = adapter.validateSequence.bind(adapter);
    let call = 0;
    vi.spyOn(adapter, "validateSequence").mockImplementation(async (rows, ctx) => {
      const n = ++call;
      const v = await real(rows, ctx);
      if (n === 1) {
        await new Promise((r) => setTimeout(r, 60));
        return { ...v, ok: false, hardErrors: ["stale response — must be dropped"] };
      }
      return v;
    });
    const p1 = controller.revalidate();
    const p2 = controller.revalidate();
    await Promise.all([p1, p2]);
    const s = store.getState();
    expect(s.validation?.hardErrors ?? []).not.toContain("stale response — must be dropped");
  });
});

describe("T13a drop placement validation (locked decision 23)", () => {
  it("an invalid exact placement surfaces a hard error with no billed call", async () => {
    const { store, controller } = harness("ready");
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    const spentBefore = store.getState().ledger!.spent;
    // 07:50 lands inside the Morning Routine wall (07:45 + 80m, non-permeable)
    controller.placeRow("Note Processing", "07:50");
    await vi.waitFor(() => {
      const v = store.getState().validation;
      expect(v).not.toBeNull();
      expect(v!.ok).toBe(false);
    });
    const s = store.getState();
    expect(s.validation!.hardErrors.some((e) => e.includes("'Note Processing'"))).toBe(true);
    expect(s.seqPhase).toBe("dirty");
    expect(s.ledger!.spent).toBe(spentBefore);
  }, 15000);
});

describe("manual layout fingerprint adoption", () => {
  it("a never-sequenced manual plan pins fixed inputs at first shadow", async () => {
    const { store, controller } = harness("ready");
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    controller.placeRow("Note Processing", "10:00");
    await new Promise((r) => setTimeout(r, 200)); // let revalidation land
    expect(store.getState().validation?.ok).toBe(true);
    await controller.shadowPreview();
    const s = store.getState();
    expect(s.shadowPhase).toBe("current");
    expect(s.fingerprint).not.toBeNull();
  }, 15000);
});

describe("T12 qualification: foreign pin reconciliation", () => {
  /* Pins persist to localStorage (store/persist.ts) and rehydrate across
     reloads, so a pin outlives its item's presence in the pool. The server
     then hard-rejects the ENTIRE request with "foreign pinned row" — a block
     with no UI-recoverable path, since the row it names is gone from the
     queue and no pin-clearing control exists. Reconcile before sending. */
  it("drops a rehydrated pin whose item is absent from today's inputs", async () => {
    const { store, adapter, controller } = harness("ready");
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });

    // persist.ts SESSION_RESTORED seeds pendingPinnedRows straight from the
    // blob with no check against the freshly loaded pool. This is the exact
    // 2026-07-26 shape: pins for LOOTS / M2.5 survived a reload after both
    // rows had aged out, and every /sequence attempt 422'd.
    store.dispatch({
      type: "SESSION_RESTORED",
      overrides: {},
      placements: {},
      sequence: null,
      fingerprint: null,
      anchoredSourceFingerprint: null,
      planningConfigFingerprint: null,
      overlapGrants: [],
      pinnedRows: [],
      pendingPinnedRows: [
        { id: "LOOTS", start: "22:00", end: "22:30", zone: "any" },
        { id: "M2.5", start: "22:30", end: "23:00", zone: "any" },
      ],
    } as never);

    const sequence = vi.spyOn(adapter, "autoSequence");
    await controller.autoSequence();
    expect(sequence.mock.calls[0][0].pinnedRows).toEqual([]);
  }, 20000);

  it("keeps a pin whose item is still included", async () => {
    const { store, adapter, controller } = harness("ready");
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();

    controller.moveRow("Magic Mirror", "11:00");
    const sequence = vi.spyOn(adapter, "autoSequence");
    await controller.autoSequence();
    expect(sequence.mock.calls[0][0].pinnedRows).toEqual([
      expect.objectContaining({ id: "Magic Mirror", start: "11:00" }),
    ]);
  }, 20000);
});

describe("FEEDBACK-01 chronological sequence ordering", () => {
  /* Root cause (2026-08-12): autoSequence appended anchor-based overflow rows
     AFTER server-validated rows without a final sort. On a late-anchored,
     overbooked day the overflow starts at 17:15 while the plan already
     reaches 23:15, so the merged sequence was descending and the server
     rejected it — "sequence not in chronological start order at 'Log hours'
     ('17:15' < preceding '23:15')". The final merged sequence must be
     sorted by start time so what the UI stages is exactly what validation
     accepts. */
  it("sorts overflow after server rows — Log hours at 17:15 precedes the 23:15 row", async () => {
    const { store, adapter, controller } = harness("ready");
    vi.spyOn(adapter, "loadPlanInputs").mockResolvedValue({
      ...structuredClone(adapter.scenario.inputs),
      // Late day anchor: overflow rows are laid out from 17:15, and the
      // proposal still carries a 23:15 row — the reported descending shape.
      time: { ...adapter.scenario.inputs.time, anchor: "17:15" },
      assigned: [
        {
          id: "Log hours", name: "Log hours", path: null, source: "vault",
          types: [], urgency: null, deadline: null, priorityScore: 0,
          blocks: 1, durationLabel: "30min", todoistId: null,
        },
        ...adapter.scenario.inputs.assigned,
      ],
    });
    vi.spyOn(adapter, "autoSequence").mockResolvedValue({
      sequence: [
        { id: "Magic Mirror", start: "09:45", end: "11:15", zone: null, kind: "work" },
        { id: "Pick up prescription", start: "23:15", end: "23:45", zone: null, kind: "work" },
      ],
      warnings: [],
      overlapGrants: [],
    });
    await controller.load();
    await controller.autoSequence();

    const s = store.getState();
    const seq = s.sequence!;
    // Every server row AND every overflow row survives the merge — the sort
    // must reorder, never drop.
    expect(seq.map((r) => r.id).sort()).toEqual(
      [
        "Log hours", "Magic Mirror", "Pick up prescription",
        "Rowe's T-shirt Redesign 2026", "Press", "Note Processing",
        "Entryway Design", "Review AWS module 4",
      ].sort(),
    );
    expect(s.overflowIds).toContain("Log hours");

    const logHours = seq.find((r) => r.id === "Log hours")!;
    const pickup = seq.find((r) => r.id === "Pick up prescription")!;
    expect(logHours.start).toBe("17:15");
    expect(pickup.start).toBe("23:15");
    // The final sequence is chronologically ordered by start time.
    for (let i = 1; i < seq.length; i++) {
      expect(seq[i - 1].start <= seq[i].start).toBe(true);
    }
    expect(seq.indexOf(logHours)).toBeLessThan(seq.indexOf(pickup));
  }, 15000);
});

describe("FEEDBACK-02 overflow wall avoidance (controller wiring)", () => {
  it("lays overflow rows around non-permeable calendar walls, never through them", async () => {
    const { store, adapter, controller } = harness("ready");
    // The canned proposal places only Press, dropping the other six included
    // rows — enough overflow work that a wall-blind layout would run straight
    // through the ready scenario's 09:15-09:45 Trinoor Standup wall.
    vi.spyOn(adapter, "autoSequence").mockResolvedValue({
      sequence: [
        { id: "Press", start: "10:00", end: "11:15", zone: null, kind: "work" },
      ],
      warnings: [],
      overlapGrants: [],
    });
    await controller.load();
    await controller.saveDaySetup({
      ...store.getState().daySetup,
      confirmed: true,
    });
    await controller.autoSequence();

    const s = store.getState();
    const overflow = (s.sequence ?? []).filter((r) => s.overflowIds.includes(r.id));
    expect(overflow.length).toBeGreaterThan(0);
    const walls = calendarWalls(effectiveAnchoredBlocks(s));
    expect(walls.length).toBeGreaterThan(0);
    for (const r of overflow) {
      const rs = toMin(r.start);
      const re = toMin(r.end);
      for (const w of walls) {
        expect(
          rs >= w.end || re <= w.start,
          `${r.id} (${r.start}-${r.end}) must not overlap calendar wall ${w.start}-${w.end}`,
        ).toBe(true);
      }
    }
  }, 15000);
});

describe("FEEDBACK-03 explicit overflow infeasibility (controller wiring)", () => {
  /* FEEDBACK-03 (2026-08-14): overflow rows are placed only into verified
     free gaps — never over non-permeable walls or immutable pinned rows — and
     a row no gap can hold produces an explicit infeasibility diagnostic that
     names the row and the free capacity, instead of silent dropping. The
     diagnostics ride in the sequence warnings so they render and gate
     shadow/commit behind LD24 acceptance like any other defect. */
  it("reports infeasible overflow rows as explicit warnings naming rows and capacity", async () => {
    const { store, adapter, controller } = harness("ready");
    // One non-permeable calendar wall from the anchor to end of day: no
    // overflow row can fit, so every dropped row must be reported, never
    // staged, and never silently omitted.
    vi.spyOn(adapter, "loadPlanInputs").mockResolvedValue({
      ...structuredClone(adapter.scenario.inputs),
      anchored: [
        ...adapter.scenario.inputs.anchored,
        {
          id: "Full-day wall", name: "Full-day wall", kind: "calendar",
          start: "07:30", end: "23:59", durationMin: 0, overlapAllowed: false,
          on: true, skipToday: false, capacityClass: "fixed",
        },
      ],
    });
    vi.spyOn(adapter, "autoSequence").mockResolvedValue({
      sequence: [
        { id: "Press", start: "10:00", end: "11:15", zone: null, kind: "work" },
      ],
      warnings: [],
      overlapGrants: [],
    });
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();

    const s = store.getState();
    const warnings = s.validation?.warnings ?? [];
    expect(warnings.some((w) => w.includes("overflow infeasible"))).toBe(true);
    // the diagnostics name affected rows
    expect(warnings.some((w) => w.includes("Magic Mirror"))).toBe(true);
    expect(warnings.some((w) => w.includes("Note Processing"))).toBe(true);
    // and the available capacity
    expect(warnings.some((w) => /block/.test(w))).toBe(true);
    // nothing was staged over the wall — the infeasible rows are not placed
    expect(s.sequence!.filter((r) => s.overflowIds.includes(r.id))).toEqual([]);
    // infeasibility is a real defect: it gates shadow/commit until accepted
    expect(defectsResolved(s)).toBe(false);
  }, 15000);

  it("overflow avoids immutable pinned rows returned by the server", async () => {
    const { store, adapter, controller } = harness("ready");
    // The canned proposal places only Magic Mirror (as a pinned immutable row)
    // and Press, dropping the other included rows — enough overflow that a
    // pin-blind layout would run straight through the 07:30-09:00 pin.
    vi.spyOn(adapter, "autoSequence").mockResolvedValue({
      sequence: [
        { id: "Magic Mirror", start: "07:30", end: "09:00", zone: null, kind: "work" },
        { id: "Press", start: "10:00", end: "11:15", zone: null, kind: "work" },
      ],
      warnings: [],
      overlapGrants: [],
      pinnedRows: [
        { id: "Magic Mirror", start: "07:30", end: "09:00", zone: null, kind: "work" },
      ],
    });
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();

    const s = store.getState();
    const overflow = (s.sequence ?? []).filter((r) => s.overflowIds.includes(r.id));
    expect(overflow.length).toBeGreaterThan(0);
    const pinStart = toMin("07:30");
    for (const r of overflow) {
      const rs = toMin(r.start);
      const re = toMin(r.end);
      expect(
        rs >= 9 * 60 || re <= pinStart,
        `${r.id} (${r.start}-${r.end}) must not overlap the pinned 07:30-09:00 row`,
      ).toBe(true);
      for (const w of calendarWalls(effectiveAnchoredBlocks(s))) {
        expect(
          rs >= w.end || re <= w.start,
          `${r.id} (${r.start}-${r.end}) must not overlap calendar wall ${w.start}-${w.end}`,
        ).toBe(true);
      }
    }
  }, 15000);
});

describe("FEEDBACK-25 overflow avoids Mint walls (controller wiring)", () => {
  it("never lays a dropped row over a selected Mint session interval", async () => {
    const { store, adapter, controller } = harness("ready");
    // The canned proposal places a selected Mint session at 07:30-08:00 (the
    // frame anchor) plus Press, dropping the other included rows — enough
    // overflow that a Mint-blind layout would run straight through it.
    vi.spyOn(adapter, "autoSequence").mockResolvedValue({
      sequence: [
        {
          id: "Mint Morning · 07:30", start: "07:30", end: "08:00",
          zone: "work_hours", kind: "work",
          wire: { source: "schedulable", mint_session: true },
        },
        { id: "Press", start: "19:00", end: "20:15", zone: null, kind: "work" },
      ],
      warnings: [],
      overlapGrants: [],
    });
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();

    const s = store.getState();
    const overflow = (s.sequence ?? []).filter((r) => s.overflowIds.includes(r.id));
    expect(overflow.length).toBeGreaterThan(0);
    const walls = mintWalls(
      (s.sequence ?? []).filter((r) => r.id.startsWith("Mint ")),
    );
    expect(walls).toEqual([{ start: 7 * 60 + 30, end: 8 * 60 }]);
    for (const r of overflow) {
      const rs = toMin(r.start);
      const re = toMin(r.end);
      for (const w of walls) {
        expect(
          rs >= w.end || re <= w.start,
          `${r.id} (${r.start}-${r.end}) must not overlap Mint wall ${w.start}-${w.end}`,
        ).toBe(true);
      }
    }
  }, 15000);
});
