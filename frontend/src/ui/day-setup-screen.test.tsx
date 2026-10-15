/* U12 — Set up day screen (R20a). Recomposed to the reference A composition:
   a two-column body with the Day frame card + drag strip, an Anchors card, and
   a Morning prompts card, plus the shared footer's save state, Undo, and
   Continue. Behavioral coverage only — jsdom cannot judge layout (KTD13). */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { App } from "./App";
import { FixtureAdapter } from "../adapters/fixture";
import { makeHarness } from "./test-harness";
import type { AnchoredBlock, AnchoredOverride } from "../model/types";

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
  it("renders the Your day card and the Morning prompts card plus the strip", () => {
    const h = screenHarness();
    const r = h.ui(<App />);
    expect(r.getByRole("heading", { name: "Your day" })).toBeTruthy();
    expect(r.getByRole("heading", { name: "Morning prompts" })).toBeTruthy();
    expect(r.queryByRole("heading", { name: "Anchors" })).toBeNull();    expect(
      r.getByText("Click an anchor to edit it. Drag to move it or pick Mint sessions."),
    ).toBeTruthy();
    expect(r.getByTestId("day-strip")).toBeTruthy();
    expect(r.getByRole("button", { name: /Continue to tasks/ })).toBeTruthy();
  });

  it("keeps a duplicate calendar event whose counted copy is the work copy", () => {
    // Live case: the same 15:00-15:30 event lists an ignored '🟡 Trinoor' copy
    // first and a counted work copy from 'Calendar' second. The strip must show
    // the work copy in Mint yellow, not drop the event.
    const h = screenHarness();
    const inputs = structuredClone(h.store.getState().inputs!);
    const oppd = {
      name: "OPPD-Payment Risk Weekly Working Session",
      kind: "calendar" as const,
      start: "15:00",
      end: "15:30",
      durationMin: 30,
      overlapAllowed: false,
      on: true,
      skipToday: false,
    };
    inputs.anchored = [
      ...inputs.anchored,
      { ...oppd, id: "OPPD (Trinoor)", calendarId: "trinoor", calendarTitle: "🟡 Trinoor", capacityClass: "ignored" },
      { ...oppd, id: "OPPD (Calendar)", calendarId: "calendar", calendarTitle: "Calendar", capacityClass: "work" },
    ];
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
    const r = h.ui(<App />);
    const el = r.getByTestId("day-strip").querySelector('[aria-label^="OPPD-Payment Risk"]') as HTMLElement;
    expect(el).toBeTruthy();
    expect(el.classList.contains("strip-cal--work")).toBe(true);
  });

  it("keeps every prompt textarea inside the Morning prompts card, with the Plan-only copy prompt off this screen", () => {
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

  it("opens the anchor popover from a strip block and the editor from Edit items", () => {
    const h = screenHarness();
    const r = h.ui(<App />);
    const block = r.container.querySelector('[data-anchor-id="Sudsing"]') as HTMLElement;
    expect(block).toBeTruthy();
    fireEvent.click(block);
    expect(h.store.getState().ui.editorAnchor).toBeNull();
    expect(r.getByRole("dialog", { name: "Sudsing" })).toBeTruthy();
    fireEvent.click(r.getByRole("button", { name: "Edit items" }));
    expect(h.store.getState().ui.editorAnchor).toBe("Sudsing");
  });

  it("draws in-frame anchors but hides one fully elapsed before the frame, behind a note", () => {
    const h = screenHarness("ready", 8, { frameStart: "13:30" });
    const r = h.ui(<App />);
    // Morning Routine 07:45 with 3 counted blocks (90 min) is fully elapsed
    // before the frame: no stub at the frame edge, a note points at All blocks.
    expect(r.container.querySelector('[data-anchor-id="Morning Routine"]')).toBeNull();
    expect(r.getByRole("button", { name: /hidden today/ })).toBeTruthy();
    const sudsing = r.container.querySelector('[data-anchor-id="Sudsing"]') as HTMLElement;
    expect(sudsing).toBeTruthy();
    expect(sudsing.classList.contains("strip-anchor--not-counted")).toBe(false);
  });

  it("keeps a started-but-unfinished config anchor live at the frame edge, counted and movable", () => {
    const h = screenHarness("ready", 8, { frameStart: "15:45" });
    const daySetup = h.store.getState().daySetup;
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.daySetup = daySetup;
    // Showering 15:10 for 80 min => 3 counted blocks (90 min) ending 16:40.
    inputs.anchored = [
      {
        id: "Showering",
        name: "Showering",
        kind: "hard",
        start: "15:10",
        end: "16:30",
        durationMin: 80,
        overlapAllowed: false,
        on: true,
        skipToday: false,
      },
    ];
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
    const r = h.ui(<App />);
    const sh = r.container.querySelector('[data-anchor-id="Showering"]') as HTMLElement;
    expect(sh).toBeTruthy();
    // The ruler is 15:45-23:45 (480 min); the counted span runs to 16:40, so
    // the strip clips the block to the 55 minutes left in the frame.
    expect(parseFloat(sh.style.left)).toBeCloseTo(0, 2);
    expect(parseFloat(sh.style.width)).toBeCloseTo((55 / 480) * 100, 1);
    expect(sh.classList.contains("strip-anchor--not-counted")).toBe(false);
    expect(sh.classList.contains("strip-anchor--movable")).toBe(true);
    // No outside-frame reason or "isn't counted" hint: the switch is on.
    fireEvent.click(sh);
    expect(r.getByRole("switch", { name: "Count today" }).getAttribute("aria-checked")).toBe("true");
    expect(r.queryByRole("note")).toBeNull();
  });

  it("re-includes an explicitly off elapsed anchor from All blocks and draws it counted", async () => {
    const h = screenHarness("ready", 8, { frameStart: "13:30" });
    const before = h.store.getState().daySetup;
    h.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: {
        ...before,
        anchored: {
          ...before.anchored,
          "Morning Routine": { on: false, skipToday: true, time: null, blocks: null },
        },
      },
    });
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const r = h.ui(<App />);
    const mr = () => r.container.querySelector('[data-anchor-id="Morning Routine"]') as HTMLElement | null;
    // Morning Routine 07:45 + 90 min ends 09:15, before the 13:30 frame; an
    // explicit off keeps it hidden. The "hidden today" note opens All blocks.
    expect(mr()).toBeNull();
    fireEvent.click(r.getByRole("button", { name: /hidden today/ }));
    const sw = () => r.getByRole("switch", { name: "Count Morning Routine today" });
    expect(sw().getAttribute("aria-checked")).toBe("false");
    fireEvent.click(sw());
    await vi.waitFor(() => expect(sw().getAttribute("aria-checked")).toBe("true"));
    expect(save).toHaveBeenLastCalledWith(
      "Morning Routine",
      expect.objectContaining({ on: true, skipToday: false }),
    );
    // Explicitly on, it is re-placed from the frame start as a live, counted,
    // movable block instead of staying hidden at its config start.
    const block = mr();
    expect(block).toBeTruthy();
    expect(parseFloat(block!.style.left)).toBeCloseTo(0, 1);
    expect(block!.classList.contains("strip-anchor--movable")).toBe(true);
    const row = within(r.getByRole("dialog", { name: "All blocks" }) as HTMLElement)
      .getByText("Morning Routine")
      .closest(".all-blocks__row") as HTMLElement;
    expect((row.querySelector(".all-blocks__status") as HTMLElement).textContent).toBe("counted");
  });

  it("selects an anchor block when it is clicked", () => {
    const h = screenHarness();
    const r = h.ui(<App />);
    const block = r.container.querySelector('[data-anchor-id="Sudsing"]') as HTMLElement;
    fireEvent.click(block);
    expect(block.classList.contains("strip-anchor--selected")).toBe(true);
    expect(r.getByRole("dialog", { name: "Sudsing" })).toBeTruthy();
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
    // The ruler is the day frame (13:30-23:45), so the clipped block starts at
    // the frame edge and keeps its 15 clipped minutes.
    expect(parseFloat(slotOf(r, "mint:edge").style.left)).toBeCloseTo(0, 2);
    expect(parseFloat(slotOf(r, "mint:edge").style.width)).toBeCloseTo((15 / 615) * 100, 1);
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

  it("offers Edit prompts and + Add prompt on the Morning prompts card", () => {
    const h = screenHarness("ready");
    const r = h.ui(<App />);
    const edit = r.getByRole("button", { name: "Edit prompts" });
    expect(edit.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(edit);
    expect(edit.getAttribute("aria-pressed")).toBe("true");
    expect(r.getByRole("button", { name: "+ Add prompt" })).toBeTruthy();
  });

  it("hides a skipped anchor from the strip and keeps it reachable via All blocks", () => {
    const h = screenHarness();
    const before = h.store.getState().daySetup;
    h.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: {
        ...before,
        anchored: {
          ...before.anchored,
          Sudsing: { on: false, skipToday: true, time: null, blocks: null },
        },
      },
    });
    const r = h.ui(<App />);
    // A skipped anchor is not drawn; the note keeps it reachable via All blocks.
    expect(r.container.querySelector('[data-anchor-id="Sudsing"]')).toBeNull();
    fireEvent.click(r.getByRole("button", { name: /hidden today/ }));
    expect(
      r.getByRole("switch", { name: "Count Sudsing today" }).getAttribute("aria-checked"),
    ).toBe("false");
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

describe("Planning 2a Morning prompts", () => {
  it("renders a four-card grid under the header with an Edit prompts link and no old export UI", () => {
    const h = screenHarness();
    const r = h.ui(<App />);
    const section = r.container.querySelector(
      '[data-settings-section="prompts"]',
    ) as HTMLElement;
    expect(section).toBeTruthy();
    expect(r.getByRole("heading", { name: "Morning prompts" })).toBeTruthy();
    expect(r.getByRole("button", { name: "Edit prompts" })).toBeTruthy();
    const grid = section.querySelector(".prompts-grid") as HTMLElement;
    expect(grid).toBeTruthy();
    const cards = Array.from(grid.querySelectorAll(".prompt-card"));
    expect(cards.length).toBe(4);
    expect(cards[0].textContent).toContain("Live micro-adventure");
    for (const label of ["Intention", "For Meegy", "Stoic"]) {
      const field = r.getByLabelText(label);
      expect(field.closest(".prompt-card")).toBeTruthy();
    }
    // The old mono routing tags and per-prompt export UI are gone.
    expect(r.queryByText("text → daily note")).toBeNull();
    expect(r.queryByText("pool → Live block")).toBeNull();
    expect(r.queryByText("Prompt exports")).toBeNull();
    expect(r.queryByRole("checkbox")).toBeNull();
  });

  it("shows the single export line and + Add prompt under the grid", () => {
    const h = screenHarness();
    const r = h.ui(<App />);
    expect(
      r.getByText("Filled prompts are added to your Todoist Inbox when you commit."),
    ).toBeTruthy();
    expect(r.getByRole("button", { name: "+ Add prompt" })).toBeTruthy();
  });

  it("shows the Live meta line whose Use my own idea link replaces the Custom idea field", () => {
    const h = screenHarness();
    const r = h.ui(<App />);
    const meta = r.container.querySelector(".live-micro__meta") as HTMLElement;
    expect(meta.textContent).toContain("Nature");
    expect(meta.textContent).toContain("streak 3");
    expect(r.getByRole("button", { name: "Use my own idea" })).toBeTruthy();
    expect(r.queryByLabelText("Custom idea")).toBeNull();
    fireEvent.click(r.getByRole("button", { name: "Use my own idea" }));
    expect(r.getByLabelText("Custom idea")).toBeTruthy();
  });

  it("persists a browser-defined prompt answer through the captures path", async () => {
    const h = screenHarness();
    const save = vi.spyOn(h.controller, "savePromptDrafts");
    const r = h.ui(<App />);
    fireEvent.click(r.getByRole("button", { name: "+ Add prompt" }));
    fireEvent.input(r.getByLabelText("New prompt question"), {
      target: { value: "Gratitude" },
    });
    fireEvent.click(r.getByRole("button", { name: "Add" }));
    fireEvent.input(r.getByLabelText("Gratitude"), { target: { value: "the rain" } });
    await vi.waitFor(() => {
      expect(save.mock.calls.some(([patch]) => Object.values(patch).includes("the rain"))).toBe(
        true,
      );
    });
    expect((r.getByLabelText("Gratitude") as HTMLElement).closest(".prompt-card")).toBeTruthy();
  });
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

describe("default Mint fill and the Mint blocks slider", () => {
  // Helper: the screen harness with a configured allotment smaller than the
  // eligible day, so a cap-based default and a fill-based default disagree.
  function smallAllotmentHarness(minutes: number, count = 8) {
    const h = screenHarness("ready", count);
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.daySemantics = {
      ...inputs.daySemantics,
      effectiveAllotmentMinutes: minutes,
      defaultAllotmentMinutes: minutes,
    };
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
    return h;
  }

  it("fills every eligible session from the anchor, not the configured allotment", () => {
    const h = smallAllotmentHarness(60);
    const r = h.ui(<App />);
    expect(r.getByText(/Mint 8 of 8 sessions/)).toBeTruthy();
  });

  it("the Mint blocks slider reduces the count within the eligible sessions", () => {
    const h = screenHarness("ready", 8);
    const r = h.ui(<App />);
    const slider = r.getByLabelText("Mint blocks") as HTMLInputElement;
    expect(slider.max).toBe("8");
    fireEvent.input(slider, { target: { value: "3" } });
    expect(r.getByText(/Mint 3 of 8 sessions/)).toBeTruthy();
  });

  it("Reset to config restores the fill after the slider reduced it", () => {
    const h = screenHarness("ready", 8);
    const r = h.ui(<App />);
    fireEvent.input(r.getByLabelText("Mint blocks"), { target: { value: "3" } });
    expect(r.getByText(/Mint 3 of 8 sessions/)).toBeTruthy();
    fireEvent.click(r.getByText("Reset to config"));
    expect(r.getByText(/Mint 8 of 8 sessions/)).toBeTruthy();
  });

  it("a deliberate reduction below the eight-block default still warns", () => {
    const h = screenHarness("ready", 8);
    const r = h.ui(<App />);
    fireEvent.input(r.getByLabelText("Mint blocks"), { target: { value: "3" } });
    expect(r.getByText(/below the 8-block/)).toBeTruthy();
  });

  it("a day with fewer than eight eligible sessions does not warn", () => {
    const h = screenHarness("ready", 5);
    const r = h.ui(<App />);
    expect(r.getByText(/Mint 5 of 5 sessions/)).toBeTruthy();
    expect(r.queryByText(/8-block/)).toBeNull();
  });
});

describe("anchor move and save to config", () => {
  const pointer = { pointerId: 1, button: 0, pointerType: "mouse" } as const;
  const strip = (r: ReturnType<ReturnType<typeof makeHarness>["ui"]>, id: string) =>
    r.container.querySelector(`[data-anchor-id="${id}"]`) as HTMLElement;

  // Ruler 07:30-23:45 drawn 975px wide: 1px per minute.
  function stubLane() {
    return vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      x: 0, y: 0, left: 0, top: 0, right: 975, bottom: 96, width: 975, height: 96,
      toJSON: () => ({}),
    });
  }
  function drag(el: HTMLElement, px: number) {
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 500 + px });
    fireEvent.pointerUp(el, { ...pointer, clientX: 500 + px });
  }
  const savedTime = (h: ReturnType<typeof screenHarness>, id: string) =>
    h.store.getState().daySetup.anchored[id]?.time;

  afterEach(() => vi.restoreAllMocks());

  it("dragging an anchor saves today's override through the existing path and updates the strip", async () => {
    stubLane();
    const h = screenHarness();
    const saveDay = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    const r = h.ui(<App />);
    drag(strip(r, "Sudsing"), 30); // 5:45 PM -> 6:15 PM
    // the click a browser fires right after the drop must not open the editor
    fireEvent.click(strip(r, "Sudsing"));
    expect(h.store.getState().ui.editorAnchor).toBeNull();
    await vi.waitFor(() => expect(savedTime(h, "Sudsing")).toBe("18:15"));
    expect(saveDay).toHaveBeenCalled();
    expect(saveDay.mock.calls.at(-1)![0].anchored.Sudsing.time).toBe("18:15");
    expect(strip(r, "Sudsing").getAttribute("aria-label")).toContain("6:15 PM");
  });

  it("moves with the arrow keys and the editor still opens from a click", async () => {
    stubLane();
    const h = screenHarness();
    const r = h.ui(<App />);
    fireEvent.keyDown(strip(r, "Sudsing"), { key: "ArrowRight" });
    await vi.waitFor(() => expect(savedTime(h, "Sudsing")).toBe("18:00"));
    fireEvent.keyDown(strip(r, "Sudsing"), { key: "ArrowLeft", shiftKey: true });
    await vi.waitFor(() => expect(savedTime(h, "Sudsing")).toBe("17:00"));
    fireEvent.click(strip(r, "Sudsing"));
    // the click selects (the popover opens); the editor is reached from it
    expect(h.store.getState().ui.editorAnchor).toBeNull();
    fireEvent.click(r.getByRole("button", { name: "Edit items" }));
    expect(h.store.getState().ui.editorAnchor).toBe("Sudsing");
  });

  it("saves the moved anchor only once the server confirms, never on drag alone", async () => {
    stubLane();
    const h = screenHarness();
    const save = vi.spyOn(FixtureAdapter.prototype, "saveAnchoredStart");
    const r = h.ui(<App />);
    drag(strip(r, "Sudsing"), 30);
    await vi.waitFor(() => expect(savedTime(h, "Sudsing")).toBe("18:15"));
    expect(save).not.toHaveBeenCalled();
  });
});

describe("All blocks modal (Set up day)", () => {
  const hard = (
    id: string,
    start: string,
    end: string,
    durationMin: number,
  ): AnchoredBlock => ({
    id,
    name: id,
    kind: "hard",
    start,
    end,
    durationMin,
    overlapAllowed: false,
    on: true,
    skipToday: false,
  });
  const cal = (
    id: string,
    start: string,
    end: string,
    durationMin: number,
    capacityClass: "work" | "fixed",
  ): AnchoredBlock => ({
    id,
    name: id,
    kind: "calendar",
    start,
    end,
    durationMin,
    overlapAllowed: false,
    on: true,
    skipToday: false,
    calendarId: id,
    calendarTitle: capacityClass === "work" ? "Trinoor" : "Personal",
    capacityClass,
  });

  /** Stage extra anchors/calendar rows plus a frame start and today-only
      overrides onto the ready scenario the screen harness already built. */
  function stageAllBlocks(
    h: ReturnType<typeof screenHarness>,
    anchors: AnchoredBlock[] = [],
    overrides: Record<string, AnchoredOverride> = {},
    frameStart = "07:30",
  ) {
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.anchored = [...inputs.anchored, ...anchors];
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
    const daySetup = h.store.getState().daySetup;
    h.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: {
        ...daySetup,
        anchor: frameStart,
        anchored: { ...daySetup.anchored, ...overrides },
      },
    });
  }

  const openAllBlocks = (r: ReturnType<ReturnType<typeof makeHarness>["ui"]>) =>
    fireEvent.click(r.getByRole("button", { name: /^All blocks/ }));

  const chipOf = (dialog: HTMLElement, name: string) => {
    const row = within(dialog).getByText(name).closest(".all-blocks__row") as HTMLElement;
    return (row.querySelector(".all-blocks__status") as HTMLElement).textContent;
  };

  it("lists every anchor and calendar row, including the off-frame ones the strip drops", () => {
    const h = screenHarness();
    stageAllBlocks(
      h,
      [
        cal("Trinoor Standup", "09:15", "09:45", 30, "work"),
        cal("PHEP sync (Vlad)", "14:45", "15:30", 45, "fixed"),
        hard("Early Task", "06:00", "06:30", 30),
        hard("Late Task", "23:50", "00:20", 30),
      ],
      { "Morning Routine": { on: false, skipToday: true, time: null, blocks: null } },
      "13:30",
    );
    const r = h.ui(<App />);
    // The strip drops the after-frame anchor and the out-of-frame calendar
    // event entirely; they must still be reachable in the modal.
    const strip = r.getByTestId("day-strip");
    expect(strip.querySelector('[data-anchor-id="Late Task"]')).toBeNull();
    expect(strip.textContent).not.toContain("Late Task");
    expect(strip.textContent).not.toContain("Trinoor Standup");

    openAllBlocks(r);
    const dialog = r.getByRole("dialog", { name: "All blocks" });
    const anchors = within(dialog).getByRole("region", { name: "Anchors" });
    const calendar = within(dialog).getByRole("region", { name: "Calendar events" });
    for (const name of ["Morning Routine", "Early Task", "Late Task", "Sudsing"]) {
      expect(within(anchors).getByText(name)).toBeTruthy();
    }
    for (const name of ["Trinoor Standup", "PHEP sync (Vlad)"]) {
      expect(within(calendar).getByText(name)).toBeTruthy();
    }
    // The Count-today switch is off for the explicitly skipped Morning Routine
    // and the two elapsed config anchors (Early Task, Foods Breakfast) the
    // server also defaults off.
    expect(r.getByRole("button", { name: /^All blocks/ }).textContent).toContain("3 off");
  });

  it("renders the elapsed, outside-frame, after-day-end, skipped, and counted status chips", () => {
    const h = screenHarness();
    stageAllBlocks(
      h,
      [hard("Early Task", "06:00", "06:30", 30), hard("Late Task", "23:50", "00:20", 30), hard("Night Owl", "23:30", "00:30", 60)],
      { "Morning Routine": { on: false, skipToday: true, time: null, blocks: null } },
      "13:30",
    );
    const r = h.ui(<App />);
    openAllBlocks(r);
    const dialog = r.getByRole("dialog", { name: "All blocks" }) as HTMLElement;
    expect(chipOf(dialog, "Early Task")).toBe("elapsed");
    expect(chipOf(dialog, "Late Task")).toBe("outside frame");
    expect(chipOf(dialog, "Night Owl")).toBe("after day end");
    expect(chipOf(dialog, "Morning Routine")).toBe("skipped");
    expect(chipOf(dialog, "Sudsing")).toBe("counted");
  });

  it("reports a window anchor whose window closed before the frame as elapsed and keeps an edge-closing one live", () => {
    const h = screenHarness("ready", 8, { frameStart: "20:30" });
    const r = h.ui(<App />);
    // Foods Breakfast (window 08:30-13:00) and Live (12:00-20:00) are long
    // closed at the 20:30 frame and come off the strip; Foods Dinner
    // (18:00-20:30) ends exactly at the frame, so it stays live.
    expect(r.container.querySelector('[data-anchor-id="Foods Breakfast"]')).toBeNull();
    expect(r.container.querySelector('[data-anchor-id="Live"]')).toBeNull();
    expect(r.container.querySelector('[data-anchor-id="Foods Dinner"]')).toBeTruthy();
    expect(r.getByRole("button", { name: /hidden today/ })).toBeTruthy();
    openAllBlocks(r);
    const dialog = r.getByRole("dialog", { name: "All blocks" }) as HTMLElement;
    expect(chipOf(dialog, "Foods Breakfast")).toBe("elapsed");
    expect(chipOf(dialog, "Live")).toBe("elapsed");
    expect(chipOf(dialog, "Foods Dinner")).toBe("counted");
  });

  it("keeps a window anchor with no parseable end live", () => {
    const h = screenHarness("ready", 8, { frameStart: "20:30" });
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.anchored = [
      ...inputs.anchored,
      {
        id: "Open Window",
        name: "Open Window",
        kind: "window",
        start: "08:30",
        end: null,
        durationMin: 30,
        overlapAllowed: false,
        on: true,
        skipToday: false,
      },
    ];
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
    const r = h.ui(<App />);
    expect(r.container.querySelector('[data-anchor-id="Open Window"]')).toBeTruthy();
    openAllBlocks(r);
    const dialog = r.getByRole("dialog", { name: "All blocks" }) as HTMLElement;
    expect(chipOf(dialog, "Open Window")).toBe("counted");
  });

  it("toggling a config anchor saves the today-only override keyed by id", async () => {
    const h = screenHarness();
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const r = h.ui(<App />);
    openAllBlocks(r);
    const sw = r.getByRole("switch", { name: "Count Sudsing today" });
    expect(sw.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(sw);
    await vi.waitFor(() => {
      expect(save).toHaveBeenCalledWith(
        "Sudsing",
        expect.objectContaining({ on: false, skipToday: true }),
      );
    });
    await vi.waitFor(() =>
      expect(
        r.getByRole("switch", { name: "Count Sudsing today" }).getAttribute("aria-checked"),
      ).toBe("false"),
    );
    fireEvent.click(r.getByRole("switch", { name: "Count Sudsing today" }));
    await vi.waitFor(() => {
      expect(save).toHaveBeenLastCalledWith(
        "Sudsing",
        expect.objectContaining({ on: true, skipToday: false }),
      );
    });
  });

  it("toggling a calendar event sends a participation-only override with no time or blocks", async () => {
    const h = screenHarness();
    stageAllBlocks(h, [cal("Trinoor Standup", "09:15", "09:45", 30, "work")]);
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const r = h.ui(<App />);
    openAllBlocks(r);
    const sw = r.getByRole("switch", { name: "Count Trinoor Standup today" });
    expect(sw.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(sw);
    await vi.waitFor(() => {
      // Exact-shape match: no time or blocks key may ride along.
      expect(save).toHaveBeenCalledWith("Trinoor Standup", { on: true, skipToday: true, time: null });
    });
  });

  it("Escape closes the modal and returns focus to the trigger; Done also closes", () => {
    const h = screenHarness();
    const r = h.ui(<App />);
    const trigger = r.getByRole("button", { name: /^All blocks/ }) as HTMLElement;
    trigger.focus();
    openAllBlocks(r);
    const dialog = r.getByRole("dialog", { name: "All blocks" }) as HTMLElement;
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(r.queryByRole("dialog", { name: "All blocks" })).toBeNull();
    expect(document.activeElement).toBe(trigger);

    openAllBlocks(r);
    fireEvent.click(r.getByRole("button", { name: "Done" }));
    expect(r.queryByRole("dialog", { name: "All blocks" })).toBeNull();
  });

  it("names every Count-today switch and exposes the switch role and aria-checked", () => {
    const h = screenHarness();
    stageAllBlocks(h, [cal("Trinoor Standup", "09:15", "09:45", 30, "work")]);
    const r = h.ui(<App />);
    openAllBlocks(r);
    const dialog = r.getByRole("dialog", { name: "All blocks" });
    const switches = within(dialog).getAllByRole("switch");
    expect(switches.length).toBeGreaterThan(0);
    for (const sw of switches) {
      expect(sw.getAttribute("aria-checked")).toBe("true");
      expect(sw.getAttribute("aria-label")).toMatch(/^Count .+ today$/);
    }
  });

  it("Reset all to config clears config anchors only, never calendar rows", async () => {
    const h = screenHarness();
    stageAllBlocks(h, [cal("Trinoor Standup", "09:15", "09:45", 30, "work")]);
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const r = h.ui(<App />);
    openAllBlocks(r);
    fireEvent.click(r.getByRole("button", { name: "Reset all to config" }));
    await vi.waitFor(() => {
      expect(save).toHaveBeenCalledWith("Sudsing", {
        on: true,
        skipToday: false,
        time: null,
        blocks: null,
      });
    });
    expect(save.mock.calls.some(([id]) => id === "Trinoor Standup")).toBe(false);
  });
});

describe("elapsed anchors default off and are assignable", () => {
  // Ruler 13:30-23:45 drawn 615px wide: 1px per minute.
  const stubLane = () =>
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      x: 0, y: 0, left: 0, top: 0, right: 615, bottom: 96, width: 615, height: 96,
      toJSON: () => ({}),
    });
  const pointer = { pointerId: 1, button: 0, pointerType: "mouse" } as const;
  const drag = (el: HTMLElement, px: number) => {
    fireEvent.pointerDown(el, { ...pointer, clientX: 500 });
    fireEvent.pointerMove(el, { ...pointer, clientX: 500 + px });
    fireEvent.pointerUp(el, { ...pointer, clientX: 500 + px });
  };
  afterEach(() => vi.restoreAllMocks());

  it("shows an untouched elapsed anchor off with an elapsed chip and assigns it from the frame start", async () => {
    const h = screenHarness("ready", 8, { frameStart: "13:30" });
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const r = h.ui(<App />);
    const mr = () => r.container.querySelector('[data-anchor-id="Morning Routine"]') as HTMLElement | null;
    // No override: the elapsed anchor is off by the server's rule, hidden, and
    // counted in the note even though its config row says on.
    expect(mr()).toBeNull();
    // Morning Routine and Foods Breakfast are both elapsed with no override.
    expect(r.getByRole("button", { name: /hidden today/ }).textContent).toContain("2");
    fireEvent.click(r.getByRole("button", { name: /^All blocks/ }));
    const chip = () => {
      const row = within(r.getByRole("dialog", { name: "All blocks" }) as HTMLElement)
        .getByText("Morning Routine")
        .closest(".all-blocks__row") as HTMLElement;
      return (row.querySelector(".all-blocks__status") as HTMLElement).textContent;
    };
    const sw = () => r.getByRole("switch", { name: "Count Morning Routine today" });
    expect(chip()).toBe("elapsed");
    expect(sw().getAttribute("aria-checked")).toBe("false");
    // The first click turns it ON (never off) and writes a plain on-override.
    fireEvent.click(sw());
    await vi.waitFor(() => expect(sw().getAttribute("aria-checked")).toBe("true"));
    expect(save).toHaveBeenLastCalledWith(
      "Morning Routine",
      expect.objectContaining({ on: true, skipToday: false }),
    );
    expect(chip()).toBe("counted");
    // Drawn from the frame start as a live, counted, movable block.
    const block = mr();
    expect(block).toBeTruthy();
    expect(parseFloat(block!.style.left)).toBeCloseTo(0, 1);
    expect(block!.classList.contains("strip-anchor--not-counted")).toBe(false);
    expect(block!.classList.contains("strip-anchor--movable")).toBe(true);
    // Foods Breakfast stays default-off elapsed, so exactly one stays hidden;
    // Morning Routine itself has left the hidden note.
    expect(r.getByRole("button", { name: /hidden today/ }).textContent).toContain("1");
    // Turning it off again hides it.
    fireEvent.click(sw());
    await vi.waitFor(() => expect(sw().getAttribute("aria-checked")).toBe("false"));
    expect(mr()).toBeNull();
  });

  it("lets a re-included elapsed anchor be moved with the popover and the strip drag", async () => {
    stubLane();
    const h = screenHarness("ready", 8, { frameStart: "13:30" });
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const r = h.ui(<App />);
    fireEvent.click(r.getByRole("button", { name: /^All blocks/ }));
    fireEvent.click(r.getByRole("switch", { name: "Count Morning Routine today" }));
    await vi.waitFor(() =>
      expect(
        r.getByRole("switch", { name: "Count Morning Routine today" }).getAttribute("aria-checked"),
      ).toBe("true"),
    );
    fireEvent.click(r.getByRole("button", { name: "Done" }));
    const block = () => r.container.querySelector('[data-anchor-id="Morning Routine"]') as HTMLElement | null;
    // The popover Start field opens at the frame start and is editable.
    fireEvent.click(block()!);
    const starts = r.getByLabelText("Starts") as HTMLInputElement;
    expect(starts.value).toBe("13:30");
    fireEvent.change(starts, { target: { value: "14:00" } });
    await vi.waitFor(() =>
      expect(save).toHaveBeenLastCalledWith(
        "Morning Routine",
        expect.objectContaining({ time: "14:00" }),
      ),
    );
    // The pointer drag path moves it on the strip too.
    drag(block()!, 30);
    await vi.waitFor(() =>
      expect(save).toHaveBeenLastCalledWith(
        "Morning Routine",
        expect.objectContaining({ time: "14:30" }),
      ),
    );
  });
});

describe("Already placed follows the elapsed default", () => {
  it("hides an untouched elapsed anchor and draws it once explicitly on", async () => {
    const h = makeHarness("ready");
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.time = { ...inputs.time, anchor: "13:30", configEod: "23:45" };
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
    const r = h.ui(<App />);
    const lane = () => r.container.querySelector(".already-placed") as HTMLElement;
    // No override: the read-only Plan lane must not draw the elapsed anchor.
    // Foods Breakfast is also elapsed, so the note counts the hidden pair.
    expect(lane().querySelector('[data-anchor-id="Morning Routine"]')).toBeNull();
    expect(lane().textContent).toContain("2 hidden today");
    // An explicit on-override re-includes it.
    const before = h.store.getState().daySetup;
    h.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: {
        ...before,
        anchored: {
          ...before.anchored,
          "Morning Routine": { on: true, skipToday: false, time: null, blocks: null },
        },
      },
    });
    await waitFor(() =>
      expect(lane().querySelector('[data-anchor-id="Morning Routine"]')).toBeTruthy(),
    );
  });
});

