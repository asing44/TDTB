/* U12 — Set up day screen (R20a). Recomposed to the reference A composition:
   a two-column body with the Day frame card + drag strip, an Anchors card, and
   a Morning prompts card, plus the shared footer's save state, Undo, and
   Continue. Behavioral coverage only — jsdom cannot judge layout (KTD13). */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent } from "@testing-library/preact";
import { App } from "./App";
import { FixtureAdapter } from "../adapters/fixture";
import { makeHarness } from "./test-harness";

afterEach(cleanup);

function sessions(count: number) {
  const hhmm = (m: number) =>
    `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  return Array.from({ length: count }, (_, i) => {
    const start = 8 * 60 + i * 30;
    return {
      id: `mint:${i}`,
      name: `Mint ${i + 1}`,
      slot: "Day",
      start: hhmm(start),
      end: hhmm(start + 30),
    };
  });
}

function screenHarness(
  name: "ready" | "fresh" = "ready",
  count = 8,
  opts: {
    frameStart?: string;
    mint?: ReturnType<typeof sessions>;
    pending?: { date: string; id: string; idea: string } | null;
  } = {},
) {
  const h = makeHarness(name);
  const inputs = structuredClone(h.store.getState().inputs!);
  inputs.anchored = inputs.anchored.filter((a) => a.kind !== "calendar");
  inputs.daySetup = { ...inputs.daySetup, anchor: "07:30" };
  inputs.daySemantics = {
    ...inputs.daySemantics,
    effectiveAllotmentMinutes: 240,
    defaultAllotmentMinutes: 240,
    mintSessions: opts.mint ?? sessions(count),
  };
  inputs.promptOptins = { optins: {}, revision: 0 };
  if (opts.pending !== undefined) {
    inputs.microAdventure = { ...inputs.microAdventure, pendingConfirm: opts.pending };
  }
  h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
  if (opts.frameStart) {
    h.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: { ...h.store.getState().daySetup, anchor: opts.frameStart },
    });
  }
  h.store.dispatch({ type: "UI", patch: { screen: "setup", settingsPanel: null } });
  return h;
}

const slotOf = (r: ReturnType<ReturnType<typeof makeHarness>["ui"]>, id: string) =>
  r.container.querySelector(`[data-slot-id="${id}"]`) as HTMLElement;

describe("U12 Set up day composition", () => {
  it("renders the Day frame, Anchors, and Morning prompts regions plus the strip", () => {
    const h = screenHarness();
    const r = h.ui(<App />);
    expect(r.getByRole("heading", { name: "Day frame" })).toBeTruthy();
    expect(r.getByRole("heading", { name: "Anchors" })).toBeTruthy();
    expect(r.getByRole("heading", { name: "Morning prompts" })).toBeTruthy();
    expect(r.getByText("Drag on the strip to pick Mint sessions or move anchors")).toBeTruthy();
    expect(r.getByTestId("day-strip")).toBeTruthy();
    expect(r.getByRole("button", { name: /Continue to tasks/ })).toBeTruthy();
  });

  it("keeps every prompt textarea inside the Morning prompts card and ships no copy-prompt", () => {
    const h = screenHarness();
    const r = h.ui(<App />);
    const prompts = r.container.querySelector('[data-settings-section="prompts"]') as HTMLElement;
    expect(prompts).toBeTruthy();
    const textareas = Array.from((r.container as HTMLElement).querySelectorAll("textarea"));
    expect(textareas.length).toBeGreaterThan(0);
    for (const ta of textareas) {
      expect(ta.closest('[data-settings-section="prompts"]')).toBe(prompts);
    }
    expect(r.queryByText(/copy prompt/i)).toBeNull();
    expect(r.getByLabelText("Intention")).toBeTruthy();
  });

  it("drags across Mint slots to select them and updates the summary", () => {
    const h = screenHarness();
    const r = h.ui(<App />);
    expect(r.getByText(/Mint 8 of 8 sessions/)).toBeTruthy();
    fireEvent.mouseDown(slotOf(r, "mint:0"));
    fireEvent.mouseEnter(slotOf(r, "mint:2"));
    fireEvent.mouseUp(slotOf(r, "mint:2"));
    expect(r.getByText(/Mint 3 of 8 sessions/)).toBeTruthy();
    expect(slotOf(r, "mint:0").getAttribute("data-state")).toBe("selected");
    expect(slotOf(r, "mint:2").getAttribute("data-state")).toBe("selected");
    expect(slotOf(r, "mint:3").getAttribute("data-state")).toBe("available");
  });

  it("does not select Wall-blocked slots during a drag", () => {
    const h = screenHarness();
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.anchored = [
      ...inputs.anchored,
      {
        id: "OPPD meter read",
        name: "OPPD meter read",
        kind: "calendar",
        start: "09:00",
        end: "09:30",
        durationMin: 30,
        overlapAllowed: false,
        on: true,
        skipToday: false,
        calendarId: "oppd",
        calendarTitle: "OPPD",
        capacityClass: "fixed",
      },
    ];
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
    const r = h.ui(<App />);
    expect(slotOf(r, "mint:2").getAttribute("data-state")).toBe("blocked");
    fireEvent.mouseDown(slotOf(r, "mint:0"));
    fireEvent.mouseEnter(slotOf(r, "mint:3"));
    fireEvent.mouseUp(slotOf(r, "mint:3"));
    expect(slotOf(r, "mint:2").getAttribute("data-state")).toBe("blocked");
    expect(r.getByText(/Mint 3 of 8 sessions/)).toBeTruthy();
  });

  it("gives the strip a keyboard equivalent (arrows move focus, Space toggles)", () => {
    const h = screenHarness();
    const r = h.ui(<App />);
    slotOf(r, "mint:0").focus();
    fireEvent.keyDown(slotOf(r, "mint:0"), { key: "ArrowRight" });
    expect(document.activeElement).toBe(slotOf(r, "mint:1"));
    fireEvent.click(slotOf(r, "mint:1"));
    expect(slotOf(r, "mint:1").getAttribute("data-state")).toBe("available");
    expect(r.getByText(/Mint 7 of 8 sessions/)).toBeTruthy();
  });

  it("opens the anchored editor from a strip block", () => {
    const h = screenHarness();
    const r = h.ui(<App />);
    const block = r.container.querySelector('[data-anchor-id="Sudsing"]') as HTMLElement;
    expect(block).toBeTruthy();
    fireEvent.click(block);
    expect(h.store.getState().ui.editorAnchor).toBe("Sudsing");
  });

  it("keeps out-of-frame anchors off the strip and opens the editor from their chip", () => {
    const h = screenHarness("ready", 8, { frameStart: "13:30" });
    const r = h.ui(<App />);
    expect(r.container.querySelector('[data-anchor-id="Morning Routine"]')).toBeNull();
    expect(r.container.querySelector('[data-anchor-id="Sudsing"]')).toBeTruthy();
    fireEvent.click(r.getByRole("button", { name: /Morning Routine.*outside frame/ }));
    expect(h.store.getState().ui.editorAnchor).toBe("Morning Routine");
  });

  it("clips a Mint block that straddles the frame start and counts the hidden ones", () => {
    const mint = [
      { id: "mint:early", name: "Early", slot: "Day", start: "13:00", end: "13:30" },
      { id: "mint:edge", name: "Edge", slot: "Day", start: "13:15", end: "13:45" },
      { id: "mint:late", name: "Late", slot: "Day", start: "14:00", end: "14:30" },
    ];
    const h = screenHarness("ready", 8, { frameStart: "13:30", mint });
    const r = h.ui(<App />);
    expect(slotOf(r, "mint:early")).toBeNull();
    expect(slotOf(r, "mint:edge").style.left).toBe("0%");
    expect(parseFloat(slotOf(r, "mint:edge").style.width)).toBeCloseTo((15 / 570) * 100, 1);
    expect(r.getByText("1 Mint block outside the frame")).toBeTruthy();
  });

  it("answers a loose end from the banner above the frame and clears it", async () => {
    const h = screenHarness("ready", 8, {
      pending: { date: "2026-07-11", id: "ma03", idea: "Ride bike somewhere" },
    });
    const spy = vi.spyOn(FixtureAdapter.prototype, "resolveLooseEnd");
    const r = h.ui(<App />);
    expect(r.getByText("Loose end")).toBeTruthy();
    expect(r.getByText("11 Jul · Ride bike somewhere")).toBeTruthy();
    // The inline pending text in the prompts card is replaced by the banner.
    expect(r.queryByText(/is unconfirmed/)).toBeNull();
    fireEvent.click(r.getByRole("button", { name: "Dismiss" }));
    expect(spy).toHaveBeenCalledWith({ date: "2026-07-11", id: "ma03" }, false);
    await vi.waitFor(() => expect(r.queryByText("Loose end")).toBeNull());
    spy.mockRestore();
  });

  it("records Did it as done for the same loose end", async () => {
    const h = screenHarness("ready", 8, {
      pending: { date: "2026-07-11", id: "ma03", idea: "Ride bike somewhere" },
    });
    const spy = vi.spyOn(FixtureAdapter.prototype, "resolveLooseEnd");
    const r = h.ui(<App />);
    fireEvent.click(r.getByRole("button", { name: "Did it" }));
    expect(spy).toHaveBeenCalledWith({ date: "2026-07-11", id: "ma03" }, true);
    await vi.waitFor(() => expect(r.queryByText("Loose end")).toBeNull());
    spy.mockRestore();
  });

  it("lists skipped and out-of-frame anchors under Not counted today", () => {
    const h = screenHarness();
    const before = h.store.getState().daySetup;
    h.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: {
        ...before,
        anchor: "18:00",
        anchored: {
          ...before.anchored,
          Sudsing: { on: false, skipToday: true, time: null, blocks: null },
        },
      },
    });
    const r = h.ui(<App />);
    expect(r.getByText(/Not counted today:/)).toBeTruthy();
    expect(r.getByText(/Sudsing.*skipped/)).toBeTruthy();
    expect(r.getAllByText(/outside frame/).length).toBeGreaterThan(0);
  });

  it("changes Mint availability when the preset changes and Reset restores defaults", () => {
    const h = screenHarness();
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.daySemantics = {
      ...inputs.daySemantics,
      availablePresets: [
        { name: "Workday", days: ["Mon-Fri"], enabledZones: ["Mint"], workAllotmentMinutes: 240 },
        { name: "Light", days: ["Sat-Sun"], enabledZones: [], workAllotmentMinutes: 60 },
      ],
      selectedPreset: { name: "Workday", days: ["Mon-Fri"], enabledZones: ["Mint"], workAllotmentMinutes: 240 },
    };
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
    const r = h.ui(<App />);
    expect(r.getByText(/Mint 8 of 8 sessions/)).toBeTruthy();

    fireEvent.change(r.getByLabelText("Preset"), { target: { value: "Light" } });
    expect(r.getByText(/Mint 2 of 8 sessions/)).toBeTruthy();

    fireEvent.mouseDown(slotOf(r, "mint:0"));
    fireEvent.mouseEnter(slotOf(r, "mint:1"));
    fireEvent.mouseUp(slotOf(r, "mint:1"));
    expect(r.getByText(/Mint 2 of 8 sessions/)).toBeTruthy();

    fireEvent.click(r.getByText("Reset to config"));
    expect(r.getByText(/Mint 2 of 8 sessions/)).toBeTruthy();
  });
});

describe("U12 footer and prompt persistence", () => {
  it("persists prompt opt-ins through the U5 controller", async () => {
    const h = screenHarness();
    const save = vi.spyOn(h.controller, "savePromptOptins");
    const r = h.ui(<App />);
    await act(async () => {
      fireEvent.click(r.getByLabelText(/For Meegy — create a Todoist task/));
    });
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ megan_nicety: true }));
  });

  it("Continue confirms the day frame and advances the stepper to Plan tasks", async () => {
    const h = screenHarness("fresh");
    const r = h.ui(<App />);
    expect(h.store.getState().daySetup.confirmed).toBe(false);
    fireEvent.click(r.getByRole("button", { name: /Continue to tasks/ }));
    await vi.waitFor(() => {
      expect(h.store.getState().ui.screen).toBe("plan");
    });
    expect(h.store.getState().daySetup.confirmed).toBe(true);
  });

  it("Undo restores the uncommitted day-frame edits", () => {
    const h = screenHarness();
    const r = h.ui(<App />);
    fireEvent.mouseDown(slotOf(r, "mint:0"));
    fireEvent.mouseEnter(slotOf(r, "mint:1"));
    fireEvent.mouseUp(slotOf(r, "mint:1"));
    expect(r.getByText(/Mint 2 of 8 sessions/)).toBeTruthy();
    fireEvent.click(r.getByRole("button", { name: "Undo" }));
    expect(r.getByText(/Mint 8 of 8 sessions/)).toBeTruthy();
  });
});
