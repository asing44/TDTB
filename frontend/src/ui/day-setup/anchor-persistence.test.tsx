/* Recovery of the screen-level coverage lost when the standalone
   AnchorsCard.test.tsx / anchor-actions-icons.test.ts were deleted (the card
   was replaced by the DayStrip block + AnchorPopover).

   These drive the REAL Set up day screen (App) through the popover — click an
   anchor block in the strip, then use the popover controls — and assert the
   persisted writes through the fixture adapter, exactly as the old chip tests
   asserted their callbacks. Scenarios recovered:
     (1) Count today off writes the same skip override the Skip-today chip did,
         and on again clears it;
     (2) editing Starts to a valid time persists a today-only anchored override;
     (3) Save as default persists to config and reloads the inputs;
     (4) Reset to default clears today's override;
     (5) a refused save shows the error and leaves the UI consistent;
     (6) an invalid/empty Starts persists nothing. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";
import { App } from "../App";
import { FixtureAdapter } from "../../adapters/fixture";
import { makeHarness } from "../test-harness";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function sessions(count: number) {
  const hhmm = (m: number) =>
    `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  return Array.from({ length: count }, (_, i) => {
    const start = 8 * 60 + i * 30;
    return { id: `mint:${i}`, name: `Mint ${i + 1}`, slot: "Day", start: hhmm(start), end: hhmm(start + 30) };
  });
}

/** Stage the ready scenario, move the configured calendar anchors out of the
    way, and open the Set up day screen. */
function screenHarness(count = 8) {
  const h = makeHarness("ready");
  const inputs = structuredClone(h.store.getState().inputs!);
  inputs.anchored = inputs.anchored.filter((a) => a.kind !== "calendar");
  inputs.daySetup = { ...inputs.daySetup, anchor: "07:30" };
  inputs.daySemantics = {
    ...inputs.daySemantics,
    effectiveAllotmentMinutes: 240,
    defaultAllotmentMinutes: 240,
    mintSessions: sessions(count),
  };
  h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
  h.store.dispatch({ type: "UI", patch: { screen: "setup", settingsPanel: null } });
  return h;
}

type R = ReturnType<ReturnType<typeof makeHarness>["ui"]>;
const block = (r: R, id: string) =>
  r.container.querySelector(`[data-anchor-id="${id}"]`) as HTMLElement;

/** Click the strip block and return the opened popover dialog. */
function openPopover(h: ReturnType<typeof screenHarness>, id = "Sudsing") {
  const r = h.ui(<App />);
  fireEvent.click(block(r, id));
  const dialog = r.getByRole("dialog", { name: id });
  return { r, dialog };
}

const startInput = (r: R) => r.getByLabelText("Starts") as HTMLInputElement;
const sud = (h: ReturnType<typeof screenHarness>) => h.store.getState().daySetup.anchored.Sudsing;
const configStart = (h: ReturnType<typeof screenHarness>, id = "Sudsing") =>
  h.store.getState().inputs!.anchored.find((a) => a.id === id)!.start;

describe("Set up day screen anchor persistence (popover)", () => {
  it("(1) Count today off writes the skip-today override and on again clears it", async () => {
    const h = screenHarness();
    const saveDay = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    const { r } = openPopover(h);

    const sw = r.getByRole("switch", { name: "Count today" });
    expect(sw.getAttribute("aria-checked")).toBe("true");

    fireEvent.click(sw);
    await vi.waitFor(() =>
      expect(saveDay.mock.calls.at(-1)![0].anchored.Sudsing).toEqual({
        on: false,
        skipToday: true,
        time: null,
        blocks: null,
      }),
    );
    await vi.waitFor(() =>
      expect(r.getByRole("switch", { name: "Count today" }).getAttribute("aria-checked")).toBe("false"),
    );

    fireEvent.click(r.getByRole("switch", { name: "Count today" }));
    await vi.waitFor(() =>
      expect(saveDay.mock.calls.at(-1)![0].anchored.Sudsing).toEqual({
        on: true,
        skipToday: false,
        time: null,
        blocks: null,
      }),
    );
    expect(r.getByRole("switch", { name: "Count today" }).getAttribute("aria-checked")).toBe("true");
  });

  it("(1b) REPRO: Count today on Foods Breakfast toggles off then back on through the real screen", async () => {
    const h = screenHarness();
    const saveDay = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    const { r } = openPopover(h, "Foods Breakfast");

    const sw = r.getByRole("switch", { name: "Count today" });
    expect(sw.getAttribute("aria-checked")).toBe("true");

    fireEvent.click(sw);
    await vi.waitFor(() =>
      expect(saveDay.mock.calls.at(-1)![0].anchored["Foods Breakfast"]).toEqual({
        on: false,
        skipToday: true,
        time: null,
        blocks: null,
      }),
    );
    await vi.waitFor(() =>
      expect(r.getByRole("switch", { name: "Count today" }).getAttribute("aria-checked")).toBe("false"),
    );

    const sw2 = r.getByRole("switch", { name: "Count today" });
    fireEvent.click(sw2);
    await vi.waitFor(() =>
      expect(saveDay.mock.calls.at(-1)![0].anchored["Foods Breakfast"]).toEqual({
        on: true,
        skipToday: false,
        time: null,
        blocks: null,
      }),
    );
    expect(r.getByRole("switch", { name: "Count today" }).getAttribute("aria-checked")).toBe("true");
  });

  it("(1c) an anchor outside the day frame reports the override, hints, and still toggles", async () => {
    // Drive the frame through the real Start field: Foods Breakfast starts
    // 08:30, so a 09:15 start clips it before the frame. The switch must
    // reflect the override intent (on and not skipped) rather than the
    // frame-clipped drawing state, explain the clipping, and toggle both ways.
    const h = screenHarness();
    const saveDay = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    const r = h.ui(<App />);

    const start = r.getByLabelText("Start") as HTMLInputElement;
    fireEvent.input(start, { target: { value: "09:15" } });
    expect(start.value).toBe("09:15");

    fireEvent.click(block(r, "Foods Breakfast"));
    expect(r.getByRole("dialog", { name: "Foods Breakfast" })).toBeTruthy();

    const sw = r.getByRole("switch", { name: "Count today" });
    expect(sw.getAttribute("aria-checked")).toBe("true");
    const note = r.getByRole("note");
    expect(note.textContent).toContain("Starts before your day (9:15 AM)");
    expect(sw.getAttribute("aria-describedby")).toBe(note.getAttribute("id"));

    fireEvent.click(sw);
    await vi.waitFor(() =>
      expect(saveDay.mock.calls.at(-1)![0].anchored["Foods Breakfast"]).toEqual({
        on: false,
        skipToday: true,
        time: null,
        blocks: null,
      }),
    );
    await vi.waitFor(() =>
      expect(r.getByRole("switch", { name: "Count today" }).getAttribute("aria-checked")).toBe("false"),
    );
    // The popover stays open, and the strip now shows the block as skipped.
    expect(r.getByRole("dialog", { name: "Foods Breakfast" })).toBeTruthy();
    expect(block(r, "Foods Breakfast").classList.contains("strip-anchor--skipped")).toBe(true);

    fireEvent.click(r.getByRole("switch", { name: "Count today" }));
    await vi.waitFor(() =>
      expect(saveDay.mock.calls.at(-1)![0].anchored["Foods Breakfast"]).toEqual({
        on: true,
        skipToday: false,
        time: null,
        blocks: null,
      }),
    );
    await vi.waitFor(() =>
      expect(r.getByRole("switch", { name: "Count today" }).getAttribute("aria-checked")).toBe("true"),
    );
    expect(block(r, "Foods Breakfast").classList.contains("strip-anchor--skipped")).toBe(false);
    expect(block(r, "Foods Breakfast").classList.contains("strip-anchor--not-counted")).toBe(true);
  });

  it("(2) editing Starts to a valid time persists a today-only override, not a config write", async () => {
    const h = screenHarness();
    const saveDay = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    const saveStart = vi.spyOn(FixtureAdapter.prototype, "saveAnchoredStart");
    const { r } = openPopover(h);

    fireEvent.change(startInput(r), { target: { value: "18:15" } });
    await vi.waitFor(() => expect(sud(h)?.time).toBe("18:15"));

    expect(saveDay.mock.calls.at(-1)![0].anchored.Sudsing).toEqual({
      on: true,
      skipToday: false,
      time: "18:15",
      blocks: null,
    });
    expect(saveStart).not.toHaveBeenCalled();
    expect(configStart(h)).toBe("17:45");
  });

  it("(3) Save as default persists to config and reloads the inputs", async () => {
    const h = screenHarness();
    const saveStart = vi.spyOn(FixtureAdapter.prototype, "saveAnchoredStart");
    const { r } = openPopover(h);

    fireEvent.change(startInput(r), { target: { value: "18:15" } });
    await vi.waitFor(() => expect(sud(h)?.time).toBe("18:15"));

    fireEvent.click(r.getByRole("button", { name: "Save as default" }));
    await vi.waitFor(() => expect(saveStart).toHaveBeenCalledWith("Sudsing", "18:15"));
    await vi.waitFor(() => expect(configStart(h)).toBe("18:15"));

    // The input re-syncs to the reloaded configured start, and the card says so.
    await vi.waitFor(() => expect(startInput(r).value).toBe("18:15"));
    expect(r.getByText("Saved")).toBeTruthy();
    expect(r.queryByRole("button", { name: "Save as default" })).toBeNull();
  });

  it("(4) Reset to default clears today's override and leaves config alone", async () => {
    const h = screenHarness();
    const saveDay = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    const { r } = openPopover(h);

    fireEvent.change(startInput(r), { target: { value: "18:15" } });
    await vi.waitFor(() => expect(sud(h)?.time).toBe("18:15"));

    fireEvent.click(r.getByRole("button", { name: "Reset to default" }));
    await vi.waitFor(() => expect(saveDay.mock.calls.at(-1)![0].anchored.Sudsing.time).toBeNull());
    await vi.waitFor(() => expect(sud(h)?.time ?? null).toBeNull());

    expect(configStart(h)).toBe("17:45");
    expect(r.queryByRole("button", { name: "Save as default" })).toBeNull();
  });

  it("(5) a refused save shows the error and keeps the UI honest, config untouched", async () => {
    const h = screenHarness();
    const saveStart = vi
      .spyOn(FixtureAdapter.prototype, "saveAnchoredStart")
      .mockRejectedValue(new Error("machine-local config.json overrides anchored_blocks"));
    const { r } = openPopover(h);

    fireEvent.change(startInput(r), { target: { value: "18:15" } });
    await vi.waitFor(() => expect(sud(h)?.time).toBe("18:15"));

    fireEvent.click(r.getByRole("button", { name: "Save as default" }));
    await vi.waitFor(() =>
      expect(r.getAllByRole("alert").some((el: HTMLElement) => el.textContent?.includes("overrides anchored_blocks"))).toBe(true),
    );

    // No silent success: still dirty, still offering the save, no "Saved", and
    // the config was not written.
    expect(r.queryByText("Saved")).toBeNull();
    expect(r.getByRole("button", { name: "Save as default" })).toBeTruthy();
    expect(startInput(r).value).toBe("18:15");
    expect(configStart(h)).toBe("17:45");
    expect(saveStart).toHaveBeenCalledTimes(1);
  });

  it("(6) an empty or invalid Starts persists nothing", async () => {
    const h = screenHarness();
    const saveDay = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    const saveStart = vi.spyOn(FixtureAdapter.prototype, "saveAnchoredStart");
    const { r } = openPopover(h);

    const input = startInput(r);
    fireEvent.input(input, { target: { value: "" } });
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);

    await vi.waitFor(() =>
      expect(r.getAllByRole("alert").some((el: HTMLElement) => el.textContent?.includes("Start time is required."))).toBe(true),
    );
    expect(saveDay).not.toHaveBeenCalled();
    expect(saveStart).not.toHaveBeenCalled();
    expect(sud(h)?.time ?? null).toBeNull();
  });
});
