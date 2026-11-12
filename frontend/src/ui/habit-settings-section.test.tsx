/* habit-settings-section.test.tsx — the standing per-habit plan in Settings.
   Rows come from the Grit inventory (configured, unconfigured, missing,
   unverified). Every change autosaves through one serialised revision chain;
   a 409 locks the rows until a reload; saved minutes stay editable while Grit
   is unavailable; only a missing row can be deleted, with an Undo. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { HabitSettingsSection, weekdaySummary } from "./HabitSettingsSection";
import { makeHarness } from "./test-harness";
import type { HabitSettings } from "../model/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const SLOW = { timeout: 4000 };

async function mount(prepare?: (h: ReturnType<typeof makeHarness>) => void) {
  const h = makeHarness("ready");
  prepare?.(h);
  const rendered = h.ui(<HabitSettingsSection active />);
  await waitFor(() => rendered.getByRole("list", { name: "Habits from Grit" }), SLOW);
  return { h, rendered };
}

const row = (r: { container: HTMLElement }, name: string) =>
  r.container.querySelector(`li[data-habit="${name}"]`) as HTMLElement;
const minutes = (r: { container: HTMLElement }, name: string) =>
  row(r, name).querySelector("input") as HTMLInputElement;

/** Type, let the field's state settle (as a real keystroke would), then leave it. */
async function edit(input: HTMLInputElement, value: string) {
  fireEvent.input(input, { target: { value } });
  await new Promise((r) => setTimeout(r, 0));
  fireEvent.blur(input);
}

describe("HabitSettingsSection rows", () => {
  it("renders configured, unconfigured and missing rows from the Grit inventory", async () => {
    const { rendered } = await mount();
    expect(row(rendered, "Stretch").getAttribute("data-status")).toBe("configured");
    expect(minutes(rendered, "Stretch").value).toBe("10");
    expect(row(rendered, "Walk the dog").getAttribute("data-status")).toBe("unconfigured");
    expect(minutes(rendered, "Walk the dog").value).toBe("");
    expect(minutes(rendered, "Walk the dog").placeholder).toBe("15");
    expect(row(rendered, "Journal (evening)").getAttribute("data-status")).toBe("missing");
    expect(rendered.getByText("Grit connected")).toBeTruthy();
    // Only the missing row can be deleted; new habits come from Grit.
    expect(rendered.getAllByRole("button", { name: /^Delete .* from settings$/ })).toHaveLength(1);
    expect(rendered.queryByRole("button", { name: /^Add/ })).toBeNull();
    // Autosave: no Save button on this section.
    expect(rendered.queryByRole("button", { name: /^Save/ })).toBeNull();
  });

  it("shows saved habits as unverified, still editable, when Grit is unavailable", async () => {
    const { h, rendered } = await mount((x) => x.adapter.setHabitSettingsMode("unavailable"));
    expect(rendered.getByRole("alert").textContent).toMatch(/Can’t read Grit/);
    expect(rendered.getByText("Grit unavailable")).toBeTruthy();
    expect(row(rendered, "Stretch").getAttribute("data-status")).toBe("unverified");
    expect(rendered.queryAllByRole("button", { name: /^Delete / })).toHaveLength(0);

    const save = vi.spyOn(h.controller, "saveHabitSettings");
    const input = minutes(rendered, "Stretch");
    expect(input.disabled).toBe(false);
    await edit(input, "12");
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(rendered.getByText("Saved ✓")).toBeTruthy(), SLOW);
    expect(save.mock.calls[0][0].habits.find((x) => x.name === "Stretch")?.minutes).toBe(12);
  });

  it("shows the empty state when there is nothing saved and nothing in Grit", async () => {
    const empty: HabitSettings = {
      version: 1,
      revision: 0,
      persisted: false,
      habits: [],
      inventory: [],
      inventoryStatus: "ok",
      gritReadAt: null,
      warnings: [],
    };
    const { rendered } = await (async () => {
      const h = makeHarness("ready");
      vi.spyOn(h.controller, "loadHabitSettings").mockResolvedValue(empty);
      const r = h.ui(<HabitSettingsSection active />);
      await waitFor(() => r.getByText("No habits found in Grit"));
      return { rendered: r };
    })();
    expect(rendered.container.querySelector("ul.hb__list")).toBeNull();
    expect(rendered.getAllByRole("button", { name: "Refresh from Grit" }).length).toBeGreaterThan(0);
  });

  it("summarises weekdays in plain language", () => {
    expect(weekdaySummary(null)).toBe("Every day");
    expect(weekdaySummary([0, 1, 2, 3, 4])).toBe("Weekdays");
    expect(weekdaySummary([5, 6])).toBe("Weekends");
    expect(weekdaySummary([1, 4])).toBe("Tue, Fri");
  });
});

describe("HabitSettingsSection autosave", () => {
  it("serialises quick edits: each save carries the revision the last one returned", async () => {
    const { h, rendered } = await mount();
    const save = vi.spyOn(h.controller, "saveHabitSettings");
    await edit(minutes(rendered, "Stretch"), "11");
    await edit(minutes(rendered, "Read"), "25");
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2), SLOW);
    expect(save.mock.calls.map((c) => c[0].expectedRevision)).toEqual([14, 15]);
    // Each draft is a full replacement; the second keeps the first edit.
    const second = save.mock.calls[1][0].habits;
    expect(second.find((x) => x.name === "Stretch")?.minutes).toBe(11);
    expect(second.find((x) => x.name === "Read")?.minutes).toBe(25);
    expect(second).toHaveLength(5);
    await waitFor(() => expect(rendered.getAllByText("Saved ✓").length).toBe(2), SLOW);
  });

  it("saves an unconfigured habit's first minutes and flips it to configured", async () => {
    const { h, rendered } = await mount();
    const save = vi.spyOn(h.controller, "saveHabitSettings");
    await edit(minutes(rendered, "Walk the dog"), "20");
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].habits.find((x) => x.name === "Walk the dog")).toEqual({
      name: "Walk the dog",
      minutes: 20,
      weekdays: null,
    });
    await waitFor(
      () => expect(row(rendered, "Walk the dog").getAttribute("data-status")).toBe("configured"),
      SLOW,
    );
  });

  it("saves a weekday press on its own and rejects out-of-range minutes without saving", async () => {
    const { h, rendered } = await mount();
    const save = vi.spyOn(h.controller, "saveHabitSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Choose days for Stretch" }));
    fireEvent.click(rendered.getByRole("button", { name: "Sunday" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].habits.find((x) => x.name === "Stretch")?.weekdays).toEqual([
      0, 1, 2, 3, 4, 5,
    ]);

    await edit(minutes(rendered, "Read"), "999");
    expect(rendered.getAllByRole("alert").some((a) => /whole minutes from 0 to 480/.test(a.textContent ?? ""))).toBe(true);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("rolls the row back and says so when a save is rejected", async () => {
    const { h, rendered } = await mount();
    vi.spyOn(h.controller, "saveHabitSettings").mockResolvedValue({
      ok: false,
      reason: "invalid",
      message: "Habit settings payload is invalid.",
      index: 0,
      field: "minutes",
    });
    await edit(minutes(rendered, "Stretch"), "30");
    await waitFor(() => expect(rendered.getByText("Not saved — check the value")).toBeTruthy());
    expect(minutes(rendered, "Stretch").value).toBe("10");
  });

  it("on a 409 shows the reload callout, locks the rows, and Reload clears it", async () => {
    const { h, rendered } = await mount();
    h.adapter.setHabitSettingsMode("conflict");
    await edit(minutes(rendered, "Stretch"), "30");
    const callout = await waitFor(() => rendered.getByText("Changed elsewhere."), SLOW);
    expect(callout).toBeTruthy();
    expect(rendered.getByText("Not saved")).toBeTruthy();
    expect(minutes(rendered, "Read").disabled).toBe(true);

    fireEvent.click(rendered.getByRole("button", { name: "Reload latest" }));
    await waitFor(() => expect(rendered.queryByText("Changed elsewhere.")).toBeNull(), SLOW);
    await waitFor(() => expect(minutes(rendered, "Read").disabled).toBe(false), SLOW);
    expect(minutes(rendered, "Stretch").value).toBe("10");
  });
});

describe("HabitSettingsSection missing rows", () => {
  it("deletes a missing row immediately and Undo puts it back", async () => {
    const { h, rendered } = await mount();
    const save = vi.spyOn(h.controller, "saveHabitSettings");
    fireEvent.click(rendered.getByRole("button", { name: "Delete Journal (evening) from settings" }));

    expect(rendered.getByText("Deleted Journal (evening).")).toBeTruthy();
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].habits.map((x) => x.name)).not.toContain("Journal (evening)");
    expect(row(rendered, "Journal (evening)")).toBeNull();

    fireEvent.click(rendered.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2), SLOW);
    expect(save.mock.calls[1][0].habits.map((x) => x.name)).toContain("Journal (evening)");
    expect(save.mock.calls[1][0].habits.find((x) => x.name === "Journal (evening)")?.minutes).toBe(15);
    await waitFor(() => expect(row(rendered, "Journal (evening)")).toBeTruthy());
    expect(rendered.queryByText("Deleted Journal (evening).")).toBeNull();
  });
});
