import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, fireEvent, act } from "@testing-library/preact";

// globals:false → testing-library can't self-register its cleanup hook.
afterEach(cleanup);
import { Ctx, useAppState } from "./context";
import { BlockEditor } from "./BlockEditor";
import { ActionDock } from "./ActionDock";
import { ApprovalDrawer } from "./ApprovalDrawer";
import { Queue } from "./Queue";
import { SetupDrawer } from "./SetupDrawer";
import { AnchoredEditor } from "./AnchoredEditor";
import { Rail } from "./Rail";
import { FooterBanners } from "./FooterBanners";
import { App } from "./App";
import { ExecutionView } from "./ExecutionView";
import { createStore, type Store } from "../store/createStore";
import { Controller } from "../store/controller";
import { FixtureAdapter } from "../adapters/fixture";
import { makeScenario, fixedInputsOf, type ScenarioName } from "../fixtures/scenarios";
import { fingerprintFixedInputs } from "../model/fingerprint";
import { fixtureValidate } from "../adapters/fixture";
import { buildDayPrompt } from "../store/exportPrompt";
import type { ComponentChildren } from "preact";

function makeHarness(scenario: ScenarioName): {
  store: Store;
  controller: Controller;
  ui: (children: ComponentChildren) => ReturnType<typeof render>;
} {
  const sc = makeScenario(scenario);
  const store = createStore();
  store.dispatch({ type: "INPUTS_LOADED", inputs: sc.inputs, ledger: { ...sc.ledger } });
  if (sc.staged.daySetupConfirmed) {
    store.dispatch({
      type: "SETUP_SAVED",
      daySetup: { ...sc.inputs.daySetup, confirmed: true },
    });
  }
  if (sc.staged.sequence) {
    store.dispatch({
      type: "SEQUENCE_OK",
      sequence: sc.staged.sequence.map((r) => ({ ...r })),
      warnings: sc.proposal?.warnings ?? [],
      fingerprint: fingerprintFixedInputs(fixedInputsOf(sc.inputs)),
      anchoredSourceFingerprint: sc.inputs.anchoredSourceFingerprint,
      ledger: { ...sc.ledger },
    });
    store.dispatch({
      type: "VALIDATED",
      validation: fixtureValidate(sc.staged.sequence, sc.inputs),
    });
  }
  if (sc.staged.shadowCurrent) {
    store.dispatch({ type: "SHADOW_OK", shadow: structuredClone(sc.shadow) });
    store.dispatch({ type: "UI", patch: { approvalOpen: true } });
  }
  if (sc.staged.committed) {
    store.dispatch({ type: "COMMIT_DONE", report: structuredClone(sc.commitReport) });
  }
  const controller = new Controller(new FixtureAdapter(scenario), store.dispatch, store.getState);
  const ui = (children: ComponentChildren) =>
    render(<Ctx.Provider value={{ store, controller }}>{children}</Ctx.Provider>);
  return { store, controller, ui };
}

describe("ActionDock", () => {
  it("fresh scenario keeps day setup directly reachable", () => {
    const { ui } = makeHarness("fresh");
    const { getByText } = ui(<ActionDock />);
    expect(getByText("Confirm day setup")).toBeTruthy();
  });

  it("ready scenario offers ONE explicit billed sequence action with cost label", () => {
    const { ui } = makeHarness("ready");
    const { getByText } = ui(<ActionDock />);
    // T12e: the block count being committed is part of the label.
    expect(getByText(/^Auto sequence \d+ blk$/)).toBeTruthy();
    expect(getByText(/1 billed call · 4 left today/)).toBeTruthy();
  });

  it("sequenced scenario offers shadow preview, labeled as writing nothing", () => {
    const { ui } = makeHarness("sequenced");
    const { getByText } = ui(<ActionDock />);
    expect(getByText("Preview commit")).toBeTruthy();
    expect(getByText(/writes nothing/)).toBeTruthy();
  });

  it("conflict scenario shows blocking-issue status and keeps manual tools", () => {
    const { ui, store } = makeHarness("conflict");
    const { getByText } = ui(<ActionDock />);
    expect(store.getState().validation!.ok).toBe(false);
    expect(getByText(/blocking issue/)).toBeTruthy();
    expect(getByText("Revalidate")).toBeTruthy();
  });

  it("verified scenario shows the calm done state", () => {
    const { ui } = makeHarness("verified");
    const { getByText } = ui(<ActionDock />);
    expect(getByText(/Committed and verified/)).toBeTruthy();
  });

  it("does not claim copied when both clipboard paths fail", async () => {
    const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    const execCommandDescriptor = Object.getOwnPropertyDescriptor(document, "execCommand");
    const writeText = vi.fn().mockRejectedValue(new Error("clipboard denied"));
    try {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: { writeText },
      });
      Object.defineProperty(document, "execCommand", {
        configurable: true,
        value: vi.fn(() => false),
      });

      const { ui, store } = makeHarness("ready");
      const { getByRole, getByLabelText, getByText, queryByText } = ui(<ActionDock />);
      await act(async () => {
        fireEvent.click(getByRole("button", { name: "Copy plan prompt for an external LLM" }));
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      expect(writeText).toHaveBeenCalledWith(buildDayPrompt(store.getState()));
      expect(queryByText("Copied ✓")).toBeNull();
      expect(getByText("Clipboard unavailable — select the prompt below")).toBeTruthy();
      expect((getByLabelText("Prompt text to copy manually") as HTMLTextAreaElement).value).toBe(
        buildDayPrompt(store.getState()),
      );
    } finally {
      if (clipboardDescriptor) {
        Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
      } else {
        Reflect.deleteProperty(navigator, "clipboard");
      }
      if (execCommandDescriptor) {
        Object.defineProperty(document, "execCommand", execCommandDescriptor);
      } else {
        Reflect.deleteProperty(document, "execCommand");
      }
    }
  });

  it("never renders a live-commit control — that lives behind the approval drawer", () => {
    for (const name of ["fresh", "ready", "sequenced", "commit-preview"] as const) {
      const { ui } = makeHarness(name);
      const { queryByText, unmount } = ui(<ActionDock />);
      expect(queryByText(/Commit live/)).toBeNull();
      unmount();
    }
  });
});

describe("good-enough override (locked decision 24)", () => {
  const WARN = "⚠ past EOD — ends 23:30, effective EOD 23:00";
  const warned = { ok: true, hardErrors: [], warnings: [WARN] };

  it("pending acceptable defects disable preview and expose one Accept as-is control", () => {
    const { ui, store } = makeHarness("sequenced");
    store.dispatch({ type: "VALIDATED", validation: warned });
    const { getByText } = ui(<ActionDock />);
    expect(getByText(/1 acceptable defect/)).toBeTruthy();
    const preview = getByText("Preview commit").closest("button")!;
    expect(preview.disabled).toBe(true);
    const accept = getByText("Accept as-is").closest("button")!;
    fireEvent.click(accept);
    expect(store.getState().acceptedDefects).toEqual([WARN]);
    expect(preview.disabled).toBe(false);
  });

  it("after acceptance the control disappears and status returns to calm", () => {
    const { ui, store } = makeHarness("sequenced");
    store.dispatch({ type: "VALIDATED", validation: warned });
    store.dispatch({ type: "ACCEPT_DEFECTS" });
    const { queryByText, getByText } = ui(<ActionDock />);
    expect(queryByText("Accept as-is")).toBeNull();
    expect(getByText(/Preview the exact writes next/)).toBeTruthy();
  });

  it("the approval drawer lists accepted defects verbatim", () => {
    const { ui, store } = makeHarness("commit-preview");
    store.dispatch({ type: "VALIDATED", validation: warned });
    store.dispatch({ type: "ACCEPT_DEFECTS" });
    store.dispatch({ type: "SHADOW_OK", shadow: structuredClone(makeScenario("commit-preview").shadow) });
    const { getByText } = ui(<ApprovalDrawer />);
    expect(getByText("Accepted defects")).toBeTruthy();
    expect(getByText(new RegExp("ends 23:30, effective EOD 23:00"))).toBeTruthy();
    expect(getByText(/accepted as-is/)).toBeTruthy();
  });

  it("hard errors never render the Accept as-is control", () => {
    const { ui, store } = makeHarness("conflict");
    expect(store.getState().validation!.ok).toBe(false);
    const { queryByText } = ui(<ActionDock />);
    expect(queryByText("Accept as-is")).toBeNull();
  });
});

describe("ApprovalDrawer two-gate commit", () => {
  it("shows surface totals and hides the live button until armed", () => {
    const { ui } = makeHarness("commit-preview");
    const { getByText, queryByText } = ui(<ApprovalDrawer />);
    expect(getByText("todoist")).toBeTruthy();
    expect(getByText(/arm live commit/)).toBeTruthy();
    expect(queryByText(/Commit live — write to all surfaces/)).toBeNull();
  });

  it("arming reveals the separate live button (second click)", () => {
    const { ui, store } = makeHarness("commit-preview");
    const { getByText } = ui(<ApprovalDrawer />);
    fireEvent.click(getByText(/arm live commit/));
    expect(store.getState().liveArmed).toBe(true);
    expect(getByText(/Commit live — write to all surfaces/)).toBeTruthy();
  });

  it("verified scenario renders the report with zero verify failures", () => {
    const { ui, store } = makeHarness("verified");
    store.dispatch({ type: "UI", patch: { approvalOpen: true } });
    const { getByText } = ui(<ApprovalDrawer />);
    expect(getByText(/All surfaces verified — zero failures/)).toBeTruthy();
  });

  it("a 409/422 rejection renders the nothing-was-written banner and stays retryable", () => {
    const { ui, store } = makeHarness("commit-preview");
    store.dispatch({ type: "ARM_LIVE" });
    store.dispatch({ type: "COMMIT_START" });
    store.dispatch({
      type: "COMMIT_REJECTED",
      error: "live commit already in flight — retry after it returns",
    });
    const { getByText, queryByText } = ui(<ApprovalDrawer />);
    expect(getByText(/nothing was written; retry when ready/)).toBeTruthy();
    // Not a commit result — the approve/arm flow is still on screen.
    expect(queryByText("Commit result")).toBeNull();
    expect(getByText(/arm live commit/)).toBeTruthy();
  });

  it("stale shadow shows the rerun warning", () => {
    const { ui, store } = makeHarness("commit-preview");
    store.dispatch({ type: "ROW_MOVED", id: "Magic Mirror", start: "11:00" });
    const { getByText } = ui(<ApprovalDrawer />);
    expect(getByText(/changed since this preview/)).toBeTruthy();
  });
});

describe("Queue", () => {
  it("groups by planning state with all-day work kept visible", () => {
    const { ui } = makeHarness("sequenced");
    const { getByText, container } = ui(<Queue />);
    expect(getByText(/Needs placement/)).toBeTruthy();
    expect(getByText(/Scheduled \(7\)/)).toBeTruthy();
    // All-day rows band with the rest now — there is no trailing "All day"
    // heading to count, so the badge on the row carries the state.
    expect(getByText("All day")).toBeTruthy();
    expect(container.querySelector(".qrow__allday")).toBeTruthy();
    expect(getByText("Charge GoPro")).toBeTruthy();
  });

  it("has no add/deassign/complete/suggested affordances (assigned-only)", () => {
    const { ui } = makeHarness("ready");
    const { queryByText, container } = ui(<Queue />);
    expect(queryByText(/Suggested/i)).toBeNull();
    expect(queryByText(/Add item/i)).toBeNull();
    expect(queryByText(/Deassign/i)).toBeNull();
    expect(container.textContent).not.toMatch(/complete/i);
  });

  it("exclude control moves an item to Excluded today", () => {
    const { ui, store } = makeHarness("ready");
    const { getByLabelText } = ui(<Queue />);
    fireEvent.click(getByLabelText("Exclude Press today"));
    expect(store.getState().overrides["Press"]).toEqual({ included: false, blocks: null });
  });

  it("T7 duration slider sets a today-only override, marked with *", () => {
    const { ui, store } = makeHarness("ready");
    const { getAllByLabelText, getByText } = ui(<Queue />);
    const slider = getAllByLabelText(/duration in 15-minute steps$/)[0];
    fireEvent.input(slider, { target: { value: "4" } });
    const s = store.getState();
    const overridden = Object.entries(s.overrides).find(([, o]) => o.blocks != null);
    expect(overridden).toBeTruthy();
    expect(getByText(/\*/)).toBeTruthy();
  });

  /* The T7 slider reaches all day, as it always did. What changed (2026-07-27,
     Adam) is the grid and the destination: 30min → 15min → All day, and the row
     STAYS in its urgency band wearing an all-day badge. It used to relocate to
     a trailing All-day section the instant the slider bottomed out, which is
     what "bumping somewhere out of screen" meant. */
  it("T7 slider steps 30min → 15min → All day without moving the row", () => {
    const { ui, store } = makeHarness("ready");
    const { getByLabelText, container } = ui(<Queue />);
    const slider = getByLabelText("Note Processing duration in 15-minute steps");
    const bandOfRow = () =>
      container
        .querySelector(".qrow:has(.qrow__name)") &&
      [...container.querySelectorAll(".queue__band")].findIndex((b) =>
        [...b.querySelectorAll(".qrow__name")].some((n) => n.textContent === "Note Processing"),
      );
    const startBand = bandOfRow();
    fireEvent.input(slider, { target: { value: "0.5" } });
    expect(store.getState().overrides["Note Processing"].blocks).toBe(0.5);
    fireEvent.input(slider, { target: { value: "0" } });
    expect(store.getState().overrides["Note Processing"].blocks).toBe(0);
    // Still in the same band, and now saying so.
    expect(bandOfRow()).toBe(startBand);
    expect(container.querySelector(".qrow__allday")).toBeTruthy();
  });

  it("T7 the − stepper walks a row down to all day in 15-minute steps", () => {
    const { ui, store } = makeHarness("ready");
    const { getByLabelText, getByText } = ui(<Queue />);
    const blocks = () => store.getState().overrides["Note Processing"]?.blocks;
    // 1 block (30min) → 15min → all day. Before the half-block grid landed,
    // clampBlocks rounded n − 0.5 straight back to n and this button was dead.
    fireEvent.click(getByLabelText("15 minutes less for Note Processing"));
    expect(blocks()).toBe(0.5);
    fireEvent.click(getByLabelText("Make Note Processing all day"));
    expect(blocks()).toBe(0);
    expect(getByText("All day*")).toBeTruthy();
  });

  it("T7 slider clamps an out-of-range value to the block grid", () => {
    const { ui, store } = makeHarness("ready");
    const { getByLabelText } = ui(<Queue />);
    const slider = getByLabelText("Note Processing duration in 15-minute steps");
    fireEvent.input(slider, { target: { value: "999" } });
    expect(store.getState().overrides["Note Processing"].blocks).toBe(16);
  });

  it("T7 sub-block shaping stays reachable on every row via the exact editor", () => {
    // The slider's 30-minute notches can't express 15 minutes; the exact
    // editor is now offered on every included row, not just recurring ones.
    const { ui } = makeHarness("ready");
    const { getByLabelText } = ui(<Queue />);
    expect(getByLabelText("Exact duration for Note Processing")).toBeTruthy();
  });
});

describe("T13 retained exact-placement paths", () => {
  it("queue rows are never draggable and Place-at still stages through the exact editor", () => {
    const { ui, store } = makeHarness("ready");
    const { container, getByLabelText, getByText, getAllByRole } = ui(
      <>
        <Queue />
        <EditorHost />
      </>,
    );
    expect(container.querySelector('[draggable="true"]')).toBeNull();
    // FEEDBACK-10 (A13): placement moved into the More menu — the row stays
    // clean, the exact-placement path is unchanged.
    fireEvent.click(getAllByRole("button", { name: /^More actions for Magic Mirror$/ })[0]);
    fireEvent.click(getByLabelText("Place Magic Mirror at a specific time"));
    expect(store.getState().ui.editorIntent).toBe("place");
    const start = getByLabelText("Start") as HTMLInputElement;
    fireEvent.input(start, { target: { value: "10:15" } });
    fireEvent.click(getByText("Apply"));
    const placed = store.getState().sequence?.find((r) => r.id === "Magic Mirror");
    expect(placed?.start).toBe("10:15");
    expect(store.getState().placements["Magic Mirror"]).toBe("10:15");
  });

  it("✎ opens the exact editor duration-only — placing never re-asks duration (T12e brief problem 7)", () => {
    const { ui, store } = makeHarness("ready");
    const { getByLabelText } = ui(
      <>
        <Queue />
        <EditorHost />
      </>,
    );
    fireEvent.click(getByLabelText("Exact duration for Magic Mirror"));
    expect(store.getState().ui.editorItem).toBe("Magic Mirror");
    expect(store.getState().ui.editorIntent).toBe("duration");
    // duration intent: no Start field at all
    expect(document.querySelector("#editor-start")).toBeNull();
  });
});

function EditorHost() {
  const s = useAppState();
  return s.ui.editorItem ? <BlockEditor /> : null;
}


describe("T12 anchored-block adjustment", () => {
  it("Day Setup exposes 30-minute anchored duration controls including zero", () => {
    const { ui, store } = makeHarness("ready");
    store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const { getByLabelText, getByText } = ui(<SetupDrawer />);
    fireEvent.click(getByLabelText("Shorten Morning Routine"));
    fireEvent.click(getByLabelText("Shorten Morning Routine"));
    fireEvent.click(getByLabelText("Shorten Morning Routine"));
    expect(getByText(/Background · 0min/)).toBeTruthy();
  });

  it("anchored exact editor warns on a past-window placement but keeps Apply enabled (T13c)", () => {
    const { ui, store } = makeHarness("sequenced");
    store.dispatch({ type: "UI", patch: { editorAnchor: "Foods Dinner" } });
    const { getByLabelText, getByText, container } = ui(<AnchoredEditor />);
    fireEvent.input(getByLabelText("Anchored start"), { target: { value: "20:00" } });
    fireEvent.click(getByLabelText("Longer anchored duration"));
    expect(getByText(/past the window end \(8:30 PM\)/)).toBeTruthy();
    expect(container.querySelector(".field-error")).toBeNull();
    expect((getByText("Apply") as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("T18g Day Setup semantics", () => {
  it("shows automatic preset and config-prefilled Work allotment including zero/reset", () => {
    const { ui, store } = makeHarness("ready");
    const inputs = store.getState().inputs!;
    store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        daySemantics: {
          ...inputs.daySemantics,
          availablePresets: [
            { name: "Workday", days: ["Mon-Fri"], enabledZones: ["Mint"], workAllotmentMinutes: 240 },
            { name: "Weekend", days: ["Sat-Sun"], enabledZones: [], workAllotmentMinutes: 0 },
          ],
          selectedPreset: { name: "Workday", days: ["Mon-Fri"], enabledZones: ["Mint"], workAllotmentMinutes: 240 },
          effectiveAllotmentMinutes: 240,
          defaultAllotmentMinutes: 180,
        },
      },
      ledger: store.getState().ledger!,
    });
    store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const { getByLabelText, getByText } = ui(<SetupDrawer />);

    expect((getByLabelText("Day preset") as HTMLSelectElement).value).toBe("__automatic__");
    expect((getByLabelText("Work allotment") as HTMLInputElement).value).toBe("240");
    fireEvent.input(getByLabelText("Work allotment"), { target: { value: "0" } });
    expect((getByLabelText("Work allotment") as HTMLInputElement).value).toBe("0");
    fireEvent.click(getByText("Reset to config"));
    expect((getByLabelText("Work allotment") as HTMLInputElement).value).toBe("240");
    expect(getByText("Save day setup")).toBeTruthy();
  });

  it("does not sequence on drawer open or ordinary control edits", () => {
    const { ui, store, controller } = makeHarness("ready");
    const sequence = vi.spyOn(controller, "autoSequence");
    store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const { getByLabelText } = ui(<SetupDrawer />);
    fireEvent.input(getByLabelText("Work allotment"), { target: { value: "0" } });
    fireEvent.change(getByLabelText("Buffering"), { target: { value: "off" } });
    expect(sequence).not.toHaveBeenCalled();
  });

  it("keeps checked Mint sessions, slider total, and saved payload synchronized", async () => {
    const { ui, store, controller } = makeHarness("ready");
    const inputs = store.getState().inputs!;
    const sessions = [
      { id: "mint:morning:08:30", name: "Mint Morning · 08:30", slot: "Morning", start: "08:30", end: "09:00" },
      { id: "mint:morning:09:00", name: "Mint Morning · 09:00", slot: "Morning", start: "09:00", end: "09:30" },
      { id: "mint:morning:09:30", name: "Mint Morning · 09:30", slot: "Morning", start: "09:30", end: "10:00" },
    ];
    store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        daySetup: {
          ...inputs.daySetup,
          workAllotmentMinutes: 180,
          schedulable: { minting: { on: true, sessions: [sessions[1].id] } },
        },
        daySemantics: {
          ...inputs.daySemantics,
          effectiveAllotmentMinutes: 180,
          mintSessions: sessions,
        },
      },
      ledger: store.getState().ledger!,
    });
    store.dispatch({
      type: "SETUP_SAVED",
      daySetup: {
        ...store.getState().daySetup,
        workAllotmentMinutes: 180,
        schedulable: { minting: { on: true, sessions: [sessions[1].id] } },
      },
    });
    const save = vi.spyOn(controller, "saveDaySetup").mockResolvedValue();
    store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const { getByLabelText, getByText } = ui(<SetupDrawer />);

    expect((getByLabelText("Mint allotment") as HTMLInputElement).value).toBe("30");
    fireEvent.click(getByLabelText("Enable Mint Morning · 08:30"));
    expect((getByLabelText("Mint allotment") as HTMLInputElement).value).toBe("60");
    fireEvent.input(getByLabelText("Mint allotment"), { target: { value: "90" } });
    expect(getByLabelText("Disable Mint Morning · 09:30")).toBeTruthy();

    await act(async () => {
      fireEvent.click(getByText("Save day setup"));
    });
    expect(save).toHaveBeenCalledWith(expect.objectContaining({
      workAllotmentMinutes: 90,
      schedulable: {
        minting: {
          on: true,
          n: 3,
          sessions: sessions.map((session) => session.id),
        },
      },
    }));
  });

  it("anchors the default Mint checks to the edited start time", () => {
    const { ui, store } = makeHarness("ready");
    const inputs = store.getState().inputs!;
    const sessions = [
      { id: "mint:morning:08:30", name: "Mint Morning · 08:30", slot: "Morning", start: "08:30", end: "09:00" },
      { id: "mint:morning:09:00", name: "Mint Morning · 09:00", slot: "Morning", start: "09:00", end: "09:30" },
      { id: "mint:afternoon:13:30", name: "Mint Afternoon · 13:30", slot: "Afternoon", start: "13:30", end: "14:00" },
      { id: "mint:afternoon:14:00", name: "Mint Afternoon · 14:00", slot: "Afternoon", start: "14:00", end: "14:30" },
    ];
    const daySetup = {
      ...inputs.daySetup,
      anchor: "13:00",
      workAllotmentMinutes: 60,
      schedulable: { minting: { on: true } },
    };
    store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        daySetup,
        daySemantics: {
          ...inputs.daySemantics,
          effectiveAllotmentMinutes: 60,
          mintSessions: sessions,
        },
      },
      ledger: store.getState().ledger!,
    });
    store.dispatch({ type: "SETUP_SAVED", daySetup });
    store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const { getByLabelText } = ui(<SetupDrawer />);

    expect(getByLabelText("Enable Mint Morning · 08:30")).toBeTruthy();
    expect(getByLabelText("Disable Mint Afternoon · 13:30")).toBeTruthy();

    fireEvent.input(getByLabelText("Start (anchor)"), { target: { value: "08:00" } });
    expect(getByLabelText("Disable Mint Morning · 08:30")).toBeTruthy();
    expect(getByLabelText("Enable Mint Afternoon · 13:30")).toBeTruthy();
  });
});

describe("T13 canvas deletion", () => {
  it("keeps the allocator and editor paths but serves no timeline or Edit-day canvas", () => {
    const { ui } = makeHarness("fresh");
    const { queryByRole, queryByLabelText, queryByText, container } = ui(<App />);
    // Pre-commit NOW/NEXT is dead space — absent by design (brief problem 5).
    expect(container.querySelector(".execution")).toBeNull();
    // IMP-07: the allocator surface is named "Today's work" (item 1).
    expect(queryByLabelText("Today's work")).toBeTruthy();
    expect(queryByLabelText("Timeline")).toBeNull();
    expect(queryByRole("button", { name: "Edit day" })).toBeNull();
    expect(queryByText("Confirm day setup")).toBeTruthy();
  });

  it("renders quiet zone bands and exact overlap clusters once committed (T12e: allotment line lives in the rail)", () => {
    const { ui, store } = makeHarness("sequenced");
    const s = store.getState();
    store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...s.inputs!,
        time: { ...s.inputs!.time, now: "10:50" },
        daySemantics: { ...s.inputs!.daySemantics, effectiveAllotmentMinutes: 240 },
        capacity: { ...s.inputs!.capacity, mint: 8 },
      },
      ledger: s.ledger!,
    });
    store.dispatch({
      type: "SEQUENCE_OK",
      sequence: [
        { id: "Magic Mirror", start: "10:45", end: "12:15", zone: null, kind: "work" },
        { id: "Pairing", start: "11:00", end: "11:30", zone: null, kind: "work" },
        { id: "Mint", start: "09:00", end: "13:00", zone: "Mint", kind: "zone" },
      ],
      warnings: [],
      fingerprint: "fixed",
      anchoredSourceFingerprint: s.inputs!.anchoredSourceFingerprint,
      planningConfigFingerprint: s.inputs!.planningConfigFingerprint,
      overlapGrants: [{
        primaryId: "Magic Mirror",
        companionId: "Pairing",
        primaryInterval: { start: "10:45", end: "12:15" },
        companionInterval: { start: "11:00", end: "11:30" },
        reason: "Paired work",
        planningConfigFingerprint: s.inputs!.planningConfigFingerprint,
      }],
      ledger: s.ledger!,
    });
    store.dispatch({
      type: "COMMIT_DONE",
      report: { status: "ok", surfaces: [], verifyFailures: [] },
    });
    const { getByLabelText } = ui(<ExecutionView />);
    expect(getByLabelText("Template zones").textContent).toContain("Mint");
    expect(getByLabelText("Allowed overlap cluster").textContent).toContain("Magic Mirror");
    expect(getByLabelText("Allowed overlap cluster").textContent).toContain("Pairing");
  });

  it("T20: runtime verbs absent before a live commit", () => {
    const { ui } = makeHarness("sequenced");
    const { container } = ui(<ExecutionView />);
    expect(container.querySelector(".runtime-actions")).toBeNull();
  });

  // Verified scenario at its own 07:22 "now" shows anchored morning blocks —
  // shift now into the evening so a work row ("Press", 19:00) is the Now card.
  function committedEveningHarness() {
    const sc = makeScenario("verified");
    sc.inputs.time.now = "19:30";
    const store = createStore();
    store.dispatch({ type: "INPUTS_LOADED", inputs: sc.inputs, ledger: { ...sc.ledger } });
    store.dispatch({
      type: "SETUP_SAVED",
      daySetup: { ...sc.inputs.daySetup, confirmed: true },
    });
    store.dispatch({
      type: "SEQUENCE_OK",
      sequence: sc.staged.sequence!.map((row) => ({ ...row })),
      warnings: [],
      fingerprint: fingerprintFixedInputs(fixedInputsOf(sc.inputs)),
      anchoredSourceFingerprint: sc.inputs.anchoredSourceFingerprint,
      ledger: { ...sc.ledger },
    });
    store.dispatch({ type: "COMMIT_DONE", report: structuredClone(sc.commitReport) });
    const controller = new Controller(
      new FixtureAdapter("verified"), store.dispatch, store.getState);
    const ui = (children: ComponentChildren) =>
      render(<Ctx.Provider value={{ store, controller }}>{children}</Ctx.Provider>);
    return { ui, store };
  }

  it("T20: committed plan exposes one-tap verbs; complete surfaces the undo chip", async () => {
    const { ui } = committedEveningHarness();
    // T12e: the undo chip renders in the footer, not mid-page.
    const r = ui(<><ExecutionView /><FooterBanners /></>);
    const groups = r.container.querySelectorAll(".runtime-actions");
    expect(groups.length).toBeGreaterThan(0);
    fireEvent.click(r.getAllByLabelText(/^Complete /)[0]);
    const undo = await r.findByText("Undo");
    fireEvent.click(undo);
    await r.findByText(/Undone ·/);
  });

  it("T20: permanent delete is two taps on the same control, no dialog", async () => {
    const { ui } = committedEveningHarness();
    const r = ui(<><ExecutionView /><FooterBanners /></>);
    const del = r.getAllByLabelText(/permanently$/)[0];
    fireEvent.click(del);
    expect(del.textContent).toBe("Confirm delete");
    expect(document.querySelector("dialog, [role=dialog]")).toBeNull();
    fireEvent.click(del);
    await r.findByText(/Deleted permanently/);
  });
});

describe("Rail budget readout (T12e)", () => {
  it("renders spend against the selectable budget with a live delta", () => {
    const { ui } = makeHarness("ready");
    const { container } = ui(<Rail />);
    expect(container.querySelector(".rail-budget__spend")).toBeTruthy();
    const delta = container.querySelector(".rail-budget__delta");
    expect(delta?.textContent).toMatch(/left|over|fully booked/);
  });

  it("overassigned readout renders in the over style", () => {
    const { ui } = makeHarness("conflict");
    const { container } = ui(<Rail />);
    const delta = container.querySelector(".rail-budget__delta");
    expect(delta?.textContent).toMatch(/over/);
    expect(delta?.className).toContain("rail-budget__delta--over");
  });
});

describe("FooterBanners alerts (T12i: floating pills own the roll-up)", () => {
  it("rolls up warnings and errors behind their pills with exact texts", () => {
    const { ui } = makeHarness("conflict");
    const { container, getByText, getAllByText } = ui(<FooterBanners />);
    fireEvent.click(container.querySelector(".alert-pill--warning") as Element);
    expect(getByText(/Todoist read failed/)).toBeTruthy();
    // The overlap hard-errors live behind the blocking pill.
    fireEvent.click(container.querySelector(".alert-pill--error") as Element);
    expect(getAllByText(/overlaps/).length).toBeGreaterThan(0);
  });

  it("renders nothing on a clean day", () => {
    const { ui } = makeHarness("fresh");
    const { container } = ui(<FooterBanners />);
    expect(container.querySelector(".alerts")).toBeNull();
  });

  /* 2026-07-27 21:11, Adam: even collapsed to a count, the error bar plus the
     warnings bar were two full-width footer stripes covering the table they
     describe. Everything floats as pills now — counts only, details in an
     on-demand popover. The dock status line still narrates blocking state. */
  it("alerts render as floating pills — counts only, details on demand (T12i)", () => {
    const { ui, store } = makeHarness("ready");
    store.dispatch({
      type: "VALIDATED",
      validation: {
        ok: false,
        hardErrors: ["planning snapshot is stale"],
        warnings: [
          "'Clean bathrooms' overlaps movable work 'Frequent CWEAN'",
          "'Clean bathrooms' overlaps movable work 'Note Processing'",
          "'Clean bathrooms' overlaps movable work 'Reading'",
          "'Frequent CWEAN' overlaps movable work 'Stillness'",
        ],
      },
    } as never);
    const { container, getByText, queryByText } = ui(<FooterBanners />);
    // No full-width stripes at rest — pills only.
    expect(container.querySelector(".footer-banner--error")).toBeNull();
    expect(queryByText("planning snapshot is stale")).toBeNull();
    expect(getByText(/1 blocking/)).toBeTruthy();
    expect(getByText(/4 warnings/)).toBeTruthy();
    fireEvent.click(getByText(/1 blocking/).closest("button") as Element);
    expect(getByText("planning snapshot is stale")).toBeTruthy();
    // Switching pills swaps the panel rather than stacking two popovers.
    fireEvent.click(getByText(/4 warnings/).closest("button") as Element);
    expect(queryByText("planning snapshot is stale")).toBeNull();
    expect(getByText(/overlaps movable work 'Reading'/)).toBeTruthy();
  });

  it("a granted overlap renders as a non-blocking info item (T29)", () => {
    const { ui, store } = makeHarness("ready");
    store.dispatch({
      type: "SEQUENCE_OK",
      sequence: [
        { id: "Haircut", start: "14:00", end: "14:30", zone: null, kind: "work" },
        { id: "Return burr", start: "14:00", end: "14:30", zone: null, kind: "work" },
      ],
      warnings: [],
      fingerprint: "fp",
      anchoredSourceFingerprint: "asf",
      planningConfigFingerprint: "pcfp",
      overlapGrants: [{
        primaryId: "Return burr",
        companionId: "Haircut",
        primaryInterval: { start: "14:00", end: "14:30" },
        companionInterval: { start: "14:00", end: "14:30" },
        reason: "ride-along",
        planningConfigFingerprint: "pcfp",
      }],
      ledger: { today: "2026-07-18", spent: 1, cap: 4, remaining: 3 },
    });
    const { container, getByText } = ui(<FooterBanners />);
    fireEvent.click(container.querySelector(".alert-pill--warning") as Element);
    expect(getByText(/Allowed overlap: 'Haircut'/)).toBeTruthy();
    const item = container.querySelector(".alert-pills__item--info");
    expect(item).toBeTruthy();
  });
});

describe("T25 recurring duration shaping", () => {
  it("queue source chip carries context beyond the bare source name", () => {
    const { ui } = makeHarness("ready");
    const { container } = ui(<Queue />);
    const rows = Array.from(container.querySelectorAll(".qrow__source")) as HTMLElement[];
    // T23's property, restated for the T12f chip: the line is never just the
    // bare source name. The note kind LEADS it as a chip now (2026-07-27)
    // rather than sitting in prose after "vault · ".
    expect(rows.length).toBeGreaterThan(0);
    for (const el of rows) {
      const chip = el.querySelector(".qrow__type");
      expect(chip).toBeTruthy();
      expect((chip as HTMLElement).textContent).not.toBe("");
      // …and the source itself is still named beside it.
      expect((el as HTMLElement).textContent).toMatch(/vault|todoist/);
    }
    const kinds = rows.map((el) => el.querySelector(".qrow__type")?.textContent);
    expect(new Set(kinds).size).toBeGreaterThan(1);
  });

  function withRecurring(harness: ReturnType<typeof makeHarness>) {
    const s0 = harness.store.getState();
    const inputs = structuredClone(s0.inputs!);
    inputs.assigned.push({
      id: "LOOTS", name: "LOOTS", path: null, source: "todoist",
      types: ["todoist"], urgency: null, deadline: inputs.validDate,
      priorityScore: 1, blocks: 1, durationLabel: "30min",
      todoistId: "loots-1", isRecurring: true, scheduledStart: "12:30",
      labels: [],
    });
    harness.store.dispatch({
      type: "INPUTS_LOADED", inputs,
      ledger: { today: inputs.validDate, spent: 0, cap: 5, remaining: 5 },
    });
  }

  it("recurring rows expose the duration slider and exact-duration editor", () => {
    const h = makeHarness("ready");
    withRecurring(h);
    const { getByLabelText } = h.ui(<Queue />);
    const slider = getByLabelText("LOOTS duration in 15-minute steps");
    expect(getByLabelText("Exact duration for LOOTS")).toBeTruthy();
    fireEvent.input(slider, { target: { value: "1" } });
    expect(h.store.getState().overrides["LOOTS"].blocks).toBe(1);
  });

  it("recurring rows expose Exclude — placement-immune must not mean undroppable", () => {
    /* T12 qualification (2026-07-26): recurring rows rendered no exclude verb
       at all, so LOOTS and M2.5 could only be dropped from the day by marking
       them done (a lie), deferring (moves them), or deleting permanently
       (destructive). There was no "not today". Being pinned to a native time
       is a placement constraint, not a reason to be undroppable. */
    const h = makeHarness("ready");
    withRecurring(h);
    const { getByLabelText } = h.ui(<Queue />);
    fireEvent.click(getByLabelText("Exclude LOOTS today"));
    expect(h.store.getState().overrides["LOOTS"].included).toBe(false);
  });

  it("block editor accepts a 5-minute exact value and snaps stray input (LD22 amendment)", () => {
    const h = makeHarness("ready");
    withRecurring(h);
    h.store.dispatch({ type: "UI", patch: { editorItem: "LOOTS" } });
    const { getByLabelText, getByText } = h.ui(<BlockEditor />);
    const minutes = getByLabelText("Exact minutes (5-minute steps)") as HTMLInputElement;
    fireEvent.input(minutes, { target: { value: "7" } });
    expect(getByText("5min")).toBeTruthy(); // snapped 7 → 5
    fireEvent.click(getByText("Apply"));
    expect(h.store.getState().overrides["LOOTS"].blocks).toBeCloseTo(5 / 30, 5);
    // recurring: start is pattern-owned — no placement dispatched
    expect(h.store.getState().sequence?.some((r) => r.id === "LOOTS" && r.kind === "work")).toBeFalsy();
  });

  it("recurring rows render no start field at all — the pattern owns the time", () => {
    const h = makeHarness("ready");
    withRecurring(h);
    h.store.dispatch({ type: "UI", patch: { editorItem: "LOOTS" } });
    const { container } = h.ui(<BlockEditor />);
    expect(container.querySelector("#editor-start")).toBeNull();
  });
});

describe("T26 queue legibility", () => {
  function withLegibilityRows(h: ReturnType<typeof makeHarness>) {
    const s0 = h.store.getState();
    const inputs = structuredClone(s0.inputs!);
    inputs.assigned.push(
      {
        id: "Institute WALL·E-OS timeblock everyday again",
        name: "Institute WALL·E-OS timeblock everyday again",
        path: "50 - Operations/Tasks/Institute.md", source: "vault",
        types: ["task"], urgency: "['4-crit']", deadline: null,
        priorityScore: 20, blocks: 1, durationLabel: "30min", todoistId: null,
      },
      {
        id: "Press overdue", name: "Press overdue", path: null,
        source: "todoist", types: ["todoist"], urgency: "4",
        deadline: "2026-07-15", priorityScore: 4, blocks: 1,
        durationLabel: "30min", todoistId: "t9", labels: [],
      },
    );
    h.store.dispatch({
      type: "INPUTS_LOADED", inputs,
      ledger: { today: inputs.validDate, spent: 0, cap: 5, remaining: 5 },
    });
  }

  it("stringified vault urgency renders as a clean tier chip, never the literal", () => {
    const h = makeHarness("ready");
    withLegibilityRows(h);
    const { container, queryByText, getAllByText } = h.ui(<Queue />);
    expect(queryByText("['4-crit']")).toBeNull();
    expect(getAllByText("4-crit").length).toBeGreaterThanOrEqual(1);
    expect(container.querySelector(".badge--ucrit")).toBeTruthy();
  });

  it("todoist priority int renders as a p-level", () => {
    const h = makeHarness("ready");
    withLegibilityRows(h);
    expect(h.ui(<Queue />).getByText("p1")).toBeTruthy();
  });

  it("past deadlines render an overdue day count, not a bare date", () => {
    const h = makeHarness("ready");
    withLegibilityRows(h);
    const { container } = h.ui(<Queue />);
    const overdue = Array.from(container.querySelectorAll(".due--overdue"));
    expect(overdue.length).toBeGreaterThanOrEqual(1);
    for (const el of overdue) {
      expect((el as HTMLElement).textContent).toMatch(/^overdue \d+d$/);
    }
  });
});
