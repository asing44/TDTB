/* Per-day habit overrides are LOCAL ONLY: they ride the existing day-setup
   save (POST /day-setup), update the local capacity preview, and roll back
   when the save is refused. No Todoist or calendar write path is involved. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { makeHarness } from "../ui/test-harness";
import { FixtureAdapter } from "../adapters/fixture";
import { daySetupToWire } from "../adapters/wire";

afterEach(() => vi.restoreAllMocks());

const ITEMS = { a: { on: false }, "habit-journal": { minutes: 60 } };

describe("controller.saveHabitOverrides", () => {
  it("saves the exact override shape through the day-setup path", async () => {
    const h = makeHarness("ready");
    const save = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    await h.controller.saveHabitOverrides({ items: ITEMS, total: null });
    expect(save).toHaveBeenCalledTimes(1);
    const sent = save.mock.calls[0][0];
    expect(daySetupToWire(sent).habits).toEqual({ items: ITEMS, total: null });
    expect(h.store.getState().daySetup.habits).toEqual({ items: ITEMS, total: null });
    expect(h.store.getState().daySetup.confirmed).toBe(true);
  });

  it("moves the capacity habits segment when a habit is toggled off", async () => {
    const h = makeHarness("ready");
    const before = h.store.getState().inputs!.capacity.habits;
    expect(before).toBe(1);
    await h.controller.saveHabitOverrides({
      items: { "habit-stretch": { on: false }, "habit-journal": { on: false } },
      total: null,
    });
    expect(h.store.getState().capacity!.habits).toBe(0);
    await h.controller.saveHabitOverrides({
      items: { "habit-journal": { minutes: 60 } },
      total: null,
    });
    expect(h.store.getState().capacity!.habits).toBe(3); // 15 + 60 min → 3 blocks
  });

  it("clearing every override returns the habits segment to default", async () => {
    const h = makeHarness("ready");
    await h.controller.saveHabitOverrides({ items: { "habit-stretch": { on: false } }, total: null });
    await h.controller.saveHabitOverrides({ items: {}, total: null });
    expect(h.store.getState().capacity!.habits).toBe(1);
    expect(daySetupToWire(h.store.getState().daySetup).habits).toEqual({ items: {}, total: null });
  });

  it("never reaches a Todoist, calendar, billed or config write", async () => {
    const h = makeHarness("ready");
    const writes = [
      vi.spyOn(FixtureAdapter.prototype, "liveCommit"),
      vi.spyOn(FixtureAdapter.prototype, "shadowCommit"),
      vi.spyOn(FixtureAdapter.prototype, "saveAnchoredStart"),
      vi.spyOn(FixtureAdapter.prototype, "autoSequence"),
    ];
    await h.controller.saveHabitOverrides({ items: ITEMS, total: null });
    for (const w of writes) expect(w).not.toHaveBeenCalled();
  });

  it("rolls back to the previous overrides and rethrows when the save is refused", async () => {
    const h = makeHarness("ready");
    await h.controller.saveHabitOverrides({ items: { "habit-stretch": { on: false } }, total: null });
    const committed = h.store.getState().daySetup.habits;
    vi.spyOn(FixtureAdapter.prototype, "saveDaySetup").mockRejectedValueOnce(new Error("refused"));
    await expect(
      h.controller.saveHabitOverrides({ items: ITEMS, total: null }),
    ).rejects.toThrow("refused");
    expect(h.store.getState().daySetup.habits).toBe(committed);
  });

  it("rolls back to no overrides when the very first save is refused", async () => {
    const h = makeHarness("ready");
    vi.spyOn(FixtureAdapter.prototype, "saveDaySetup").mockRejectedValueOnce(new Error("refused"));
    await expect(
      h.controller.saveHabitOverrides({ items: ITEMS, total: null }),
    ).rejects.toThrow("refused");
    expect(h.store.getState().daySetup.habits).toBeUndefined();
  });

  it("does not roll back a newer edit when an older save fails afterwards", async () => {
    const h = makeHarness("ready");
    const save = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    let rejectFirst!: (e: unknown) => void;
    save.mockImplementationOnce(
      () => new Promise<void>((_resolve, reject) => { rejectFirst = reject; }),
    );
    const first = h.controller.saveHabitOverrides({ items: { "habit-stretch": { on: false } }, total: null });
    const firstRejected = expect(first).rejects.toThrow("stale");
    const second = { items: { "habit-journal": { on: false } }, total: null };
    await h.controller.saveHabitOverrides(second);
    rejectFirst(new Error("stale"));
    await firstRejected;
    expect(h.store.getState().daySetup.habits).toEqual(second);
  });
});
