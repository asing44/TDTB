/* U12 — Set up day screen (R20a). Recomposed to the reference A composition:
   a two-column body with the Day frame card + drag strip, an Anchors card, and
   a Morning prompts card, plus the shared footer's save state, Undo, and
   Continue. Behavioral coverage only — jsdom cannot judge layout (KTD13). */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";
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

  it("keeps a config anchor that started before the frame at the frame edge, and the in-frame ones", () => {
    const h = screenHarness("ready", 8, { frameStart: "13:30" });
    const r = h.ui(<App />);
    // Morning Routine 07:45 with 3 counted blocks (90 min) is fully elapsed
    // before the frame; it is no longer dropped — it sits at the frame edge.
    const mr = r.container.querySelector('[data-anchor-id="Morning Routine"]') as HTMLElement;
    expect(mr).toBeTruthy();
    expect(parseFloat(mr.style.left)).toBeCloseTo(0, 2);
    expect(mr.classList.contains("strip-anchor--not-counted")).toBe(false);
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

  it("keeps a fully elapsed config anchor off but re-includable, placed from the frame start when included", async () => {
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
    const r = h.ui(<App />);
    const mr = () =>
      r.container.querySelector('[data-anchor-id="Morning Routine"]') as HTMLElement;
    // Morning Routine 07:45 + 90 min ends 09:15, before the 13:30 frame; it is
    // off but stays visible and re-includable, drawn at the frame edge.
    expect(mr()).toBeTruthy();
    expect(mr().classList.contains("strip-anchor--not-counted")).toBe(true);
    expect(mr().getAttribute("aria-label")).toContain("1:30 PM");
    fireEvent.click(mr());
    expect(r.getByRole("switch", { name: "Count today" }).getAttribute("aria-checked")).toBe("false");
    fireEvent.click(r.getByRole("switch", { name: "Count today" }));
    await vi.waitFor(() =>
      expect(mr().classList.contains("strip-anchor--not-counted")).toBe(false),
    );
    expect(mr().getAttribute("aria-label")).toContain("1:30 PM");
    expect(mr().getAttribute("aria-label")).toContain("3 blocks");
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

  it("draws a skipped anchor dimmed on the strip", () => {
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
    const sudsing = r.container.querySelector('[data-anchor-id="Sudsing"]') as HTMLElement;
    expect(sudsing.classList.contains("strip-anchor--not-counted")).toBe(true);
    expect(sudsing.classList.contains("strip-anchor--skipped")).toBe(true);
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

