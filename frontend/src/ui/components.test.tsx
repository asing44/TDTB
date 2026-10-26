import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, fireEvent, act, type RenderResult } from "@testing-library/preact";

// globals:false → testing-library can't self-register its cleanup hook.
afterEach(() => {
  cleanup();
  Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
});
import { Ctx, useAppState } from "./context";
import { BlockEditor } from "./BlockEditor";
import { ActionDock } from "./ActionDock";
import { ApprovalDrawer } from "./ApprovalDrawer";
import { Queue } from "./Queue";
import { SKIP_OCCURRENCE_TRIGGER_LABEL, UNASSIGN_TRIGGER_LABEL } from "./ConfirmUnassign";
import { SetupDrawer } from "./SetupDrawer";
import { AnchoredEditor } from "./AnchoredEditor";
import { Rail } from "./Rail";
import { FooterBanners } from "./FooterBanners";
import { App } from "./App";
import { ExecutionView } from "./ExecutionView";
import { createStore, type Store } from "../store/createStore";
import { Controller } from "../store/controller";
import { effectiveAnchoredBlocks } from "../store/store";
import { FixtureAdapter } from "../adapters/fixture";
import {
  makeScenario,
  fixedInputsOf,
  restorePlannedDay,
  type ScenarioName,
} from "../fixtures/scenarios";
import { fingerprintFixedInputs } from "../model/fingerprint";
import { fixtureValidate } from "../adapters/fixture";
import { CalendarImpact } from "./CalendarImpact";
import { calendarWalls } from "../model/overflow";
import type { ComponentChildren } from "preact";

function makeHarness(scenario: ScenarioName): {
  store: Store;
  controller: Controller;
  ui: (children: ComponentChildren) => RenderResult & { container: HTMLElement };
} {
  const sc = makeScenario(scenario);
  const store = createStore();
  store.dispatch({ type: "INPUTS_LOADED", inputs: sc.inputs, ledger: { ...sc.ledger } });
  restorePlannedDay(store);
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
    // The library types the container as Element; render mounts a real div.
    render(
      <Ctx.Provider value={{ store, controller }}>{children}</Ctx.Provider>,
    ) as RenderResult & { container: HTMLElement };
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
    expect(getByText(/^Sequence & commit \d+ blk$/)).toBeTruthy();
    expect(getByText("1 billed call")).toBeTruthy();
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

  it("Plan-only Copy prompt carries the full day-prompt content and reports success", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const { ui, controller } = makeHarness("ready");
    const seqSpy = vi.spyOn(controller, "autoSequence");
    const { getByRole, getByText } = ui(<ActionDock />);
    const button = getByRole("button", { name: "Copy plan prompt for an external LLM" });
    expect(button.textContent).toContain("Copy prompt");
    fireEvent.click(button);
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const text = writeText.mock.calls[0][0] as string;
    expect(text).toMatch(/^# Schedule my day/);
    expect(text).toContain("## Capacity");
    expect(text).toContain("## Fixed commitments");
    await vi.waitFor(() => expect(getByText(/Copied to clipboard/)).toBeTruthy());
    // Pure local: no billed sequence, no controller write.
    expect(seqSpy).not.toHaveBeenCalled();
  });

  it("falls back to a selectable read-only textarea when the clipboard rejects", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("clipboard denied"));
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const { ui } = makeHarness("ready");
    const { getByRole, getByLabelText } = ui(<ActionDock />);
    fireEvent.click(getByRole("button", { name: "Copy plan prompt for an external LLM" }));
    const area = await vi.waitFor(
      () => getByLabelText("Prompt text to copy manually") as HTMLTextAreaElement,
    );
    expect(area.readOnly).toBe(true);
    expect(area.value).toMatch(/^# Schedule my day/);
    expect(area.value).toContain("## Capacity");
  });

  it("keeps Copy prompt available while sources are degraded", () => {
    const sc = makeScenario("ready");
    sc.inputs.sourceHealth = "degraded";
    const { ui, store } = makeHarness("ready");
    store.dispatch({ type: "INPUTS_LOADED", inputs: sc.inputs, ledger: { ...sc.ledger } });
    const { getByRole } = ui(<ActionDock />);
    expect(getByRole("button", { name: "Copy plan prompt for an external LLM" })).toBeTruthy();
  });

  it("keeps Copy prompt available when the billed budget is spent", () => {
    const sc = makeScenario("ready");
    const { ui, store } = makeHarness("ready");
    store.dispatch({
      type: "INPUTS_LOADED",
      inputs: sc.inputs,
      ledger: { ...sc.ledger, spent: 4, remaining: 0 },
    });
    const { getByRole, getByText } = ui(<ActionDock />);
    expect(getByRole("button", { name: "Copy plan prompt for an external LLM" })).toBeTruthy();
    // The clarification keeps copying plan context distinct from the
    // opt-in prompt-task export, which stays Commit-only.
    expect(getByText(/Commit-only/)).toBeTruthy();
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
    const { getByText, getAllByText, container } = ui(<Queue />);
    // KTD12 grouping: Critical / When there's room / Fixed / Out today.
    expect(getByText(/^Critical$/)).toBeTruthy();
    expect(getByText(/When there's room/)).toBeTruthy();
    expect(getAllByText(/^Fixed$/).length).toBeGreaterThan(0);
    // Planning 2a: the trailing Out today lane is collapsed by default and
    // omitted entirely when nothing is out.
    expect(container.querySelector(".band--drop")).toBeNull();
    // F4a: all-day rows band with the rest and state the state in the
    // duration column ("All day"); the old sr-only marker is gone.
    expect(getByText("All day")).toBeTruthy();
    const charge = Array.from(container.querySelectorAll(".qrow")).find(
      (r) => r.querySelector(".qrow__name")?.textContent === "Charge GoPro",
    );
    expect(charge?.querySelector(".trow__dur")?.textContent).toBe("All day");
    expect(container.querySelector(".qrow__allday")).toBeNull();
    expect(getAllByText("Charge GoPro").length).toBeGreaterThan(0);
  });

  it("keeps assignment controls upstream while retaining explicit row completion", () => {
    const { ui } = makeHarness("ready");
    const { queryByText, container } = ui(<Queue />);
    expect(queryByText(/Suggested/i)).toBeNull();
    expect(queryByText(/Add item/i)).toBeNull();
    expect(queryByText(/Deassign/i)).toBeNull();
    // F4a: completion is the row's one labelled primary button; the button
    // carries an explicit name rather than leaning on concatenated children.
    const complete = Array.from(
      container.querySelectorAll<HTMLElement>(".trow__btn--primary"),
    );
    expect(complete.length).toBeGreaterThan(0);
    expect(complete[0].getAttribute("aria-label")).toBe("Complete");
    expect(complete[0].textContent).toBe("");
    expect(complete[0].getAttribute("title")).toBeTruthy();
  });

  it("shows parent-child and related-tag context without duplicate rows", () => {
    const { ui, store } = makeHarness("ready");
    const inputs = store.getState().inputs!;
    const base = inputs.assigned[0];
    const parent = { ...base, id: "Zeta parent", name: "Zeta parent", identity: "todoist:zeta-parent", tags: ["systems"], relatesTo: null };
    const child = { ...base, id: "Alpha child", name: "Alpha child", identity: "todoist:alpha-child", tags: ["systems"], relatesTo: "[[Zeta parent]]" };
    const sibling = { ...base, id: "Systems sibling", name: "Systems sibling", identity: "todoist:systems-sibling", tags: ["systems"], relatesTo: null };
    store.dispatch({ type: "INPUTS_LOADED", inputs: { ...inputs, assigned: [child, sibling, parent] }, ledger: store.getState().ledger! });

    const { container, getByText } = ui(<Queue />);
    expect(getByText("Related · #systems")).toBeTruthy();
    expect(getByText("child of Zeta parent")).toBeTruthy();
    expect(container.querySelectorAll(".qrow")).toHaveLength(3);
    expect(container.querySelector('[data-hierarchy-depth="1"]')).toBeTruthy();
  });

  it("exclude control moves an item out of today", () => {
    // F4a: browser-only Exclude today left the row for a durable,
    // server-recorded source drop — now U4's confirmed "Unassign in
    // Todoist…". The legacy store path stays intact for rows excluded before
    // the migration.
    const { ui, store } = makeHarness("ready");
    const { container } = ui(<Queue />);
    // F4a/U4: the row's source verb is a labelled button on every Todoist row.
    const skips = Array.from(
      container.querySelectorAll<HTMLElement>(".trow__btn"),
    ).filter((b) => b.getAttribute("aria-label") === UNASSIGN_TRIGGER_LABEL);
    expect(skips.length).toBeGreaterThan(0);
    store.dispatch({
      type: "OVERRIDE_SET",
      id: "Press",
      override: { included: false, blocks: null },
    });
    expect(store.getState().overrides["Press"]).toEqual({ included: false, blocks: null });
  });

  /* The T7 slider reached all day, as it always did. What changed (2026-07-27,
     Adam) is the grid and the destination: 30min → 15min → All day, and the row
     STAYS in its urgency band wearing an all-day badge. It used to relocate to
     a trailing All-day section the instant the slider bottomed out, which is
     what "bumping somewhere out of screen" meant. F4a removed the slider from
     the collapsed row; these cases move to the expanded panel (F4b). */

  it("T7 sub-block shaping stays reachable through the row's own controls", () => {
    // F4a: the collapsed row offers Complete, Skip today and Expand only. The
    // 15-minute notches and the exact-minutes entry point come back with the
    // expanded row panel (F4b), so the collapsed row must not pretend to own
    // them — and must not silently lose completion either.
    const { ui } = makeHarness("ready");
    const { container } = ui(<Queue />);
    expect(container.querySelector('[aria-label="Exact duration for Note Processing"]')).toBeNull();
    const noteRow = Array.from(
      container.querySelectorAll<HTMLElement>(".qrow"),
    ).find((r) => r.querySelector(".qrow__name")?.textContent === "Note Processing");
    const complete = noteRow?.querySelector<HTMLElement>(".trow__btn--primary");
    expect(complete?.getAttribute("aria-label")).toBe("Complete");
  });
});

describe("T13 retained exact-placement paths", () => {
  it("queue rows are never draggable and an exact start still stages through the expanded row", () => {
    // F7: the More menu's Adjust time item left; the exact start is the
    // expanded row's Start control (Set time → Apply → Save). The placement
    // result is unchanged.
    const { ui, store } = makeHarness("ready");
    const { container, getByLabelText, getByText } = ui(
      <>
        <Queue />
        <EditorHost />
      </>,
    );
    expect(container.querySelector('[draggable="true"]')).toBeNull();
    const row = () =>
      Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
        (r) => r.querySelector(".qrow__name")?.textContent === "Magic Mirror",
      )!;
    fireEvent.click(row().querySelector('button[aria-label="Expand"]')!);
    fireEvent.click(getByText("Set time"));
    const start = row().querySelector<HTMLInputElement>('input[type="time"]')!;
    fireEvent.input(start, { target: { value: "10:15" } });
    fireEvent.click(getByText("Apply"));
    expect(store.getState().placements["Magic Mirror"]).toBeUndefined();
    fireEvent.click(getByText("Save"));
    const placed = store.getState().sequence?.find((r) => r.id === "Magic Mirror");
    expect(placed?.start).toBe("10:15");
    expect(store.getState().placements["Magic Mirror"]).toBe("10:15");
    expect(getByLabelText("Select Magic Mirror")).toBeTruthy();
  });

  it("✎ duration shaping is no longer offered on the collapsed row (F4b)", () => {
    // F4a: the Exact duration control left the row with the slider; the
    // duration-only editor contract returns in the expanded panel. The placed
    // path above is untouched.
    const { ui } = makeHarness("ready");
    const { queryByLabelText } = ui(
      <>
        <Queue />
        <EditorHost />
      </>,
    );
    expect(queryByLabelText("Exact duration for Magic Mirror")).toBeNull();
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
  function mintSessions(count: number) {
    return Array.from({ length: count }, (_, index) => {
      const start = 8 * 60 + index * 30;
      const hhmm = (minutes: number) =>
        `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
      return {
        id: `mint:default:${index}`,
        name: `Mint ${index + 1}`,
        slot: "Day",
        start: hhmm(start),
        end: hhmm(start + 30),
      };
    });
  }

  it("defaults Mint to eight 30-minute sessions and exposes no checkbox controls", () => {
    const { ui, store } = makeHarness("ready");
    const inputs = store.getState().inputs!;
    store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        daySetup: { ...inputs.daySetup, schedulable: { minting: { on: true } } },
        daySemantics: {
          ...inputs.daySemantics,
          effectiveAllotmentMinutes: 0,
          defaultAllotmentMinutes: 0,
          mintSessions: mintSessions(10),
        },
      },
      ledger: store.getState().ledger!,
    });
    store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const { getByLabelText, container, queryByRole } = ui(<SetupDrawer />);

    expect((getByLabelText("Mint allotment") as HTMLInputElement).value).toBe("240");
    expect((getByLabelText("Mint session count") as HTMLInputElement).value).toBe("8");
    expect(container.querySelectorAll('input[type="checkbox"]').length).toBe(0);
    expect(queryByRole("status", { name: /below the 8-block/i })).toBeNull();
  });

  it("allows fewer than eight Mint blocks but warns clearly", () => {
    const { ui, store } = makeHarness("ready");
    const inputs = store.getState().inputs!;
    store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        daySetup: { ...inputs.daySetup, schedulable: { minting: { on: true } } },
        daySemantics: { ...inputs.daySemantics, mintSessions: mintSessions(10) },
      },
      ledger: store.getState().ledger!,
    });
    store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const { getByLabelText, getByText } = ui(<SetupDrawer />);
    fireEvent.input(getByLabelText("Mint session count"), { target: { value: "7" } });

    expect((getByLabelText("Mint allotment") as HTMLInputElement).value).toBe("210");
    expect(getByText("Mint is below the 8-block (4-hour) daily default. You can continue with less.")).toBeTruthy();
    expect((getByText("Save day setup").closest("button") as HTMLButtonElement).disabled).toBe(false);
  });

  it("warns for a positive Mint allotment without concrete session rows", () => {
    const { ui, store } = makeHarness("ready");
    const inputs = store.getState().inputs!;
    store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        daySetup: {
          ...inputs.daySetup,
          workAllotmentMinutes: 180,
          schedulable: { minting: { on: true } },
        },
        daySemantics: {
          ...inputs.daySemantics,
          effectiveAllotmentMinutes: 180,
          defaultAllotmentMinutes: 240,
          mintBelowDefault: true,
          mintSessions: [],
        },
      },
      ledger: store.getState().ledger!,
    });
    store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const { getByLabelText, getByText, queryByRole } = ui(<SetupDrawer />);
    const allotment = getByLabelText("Work allotment") as HTMLInputElement;
    const warning = "Mint is below the 8-block (4-hour) daily default. You can continue with less.";

    expect(allotment.value).toBe("180");
    expect(getByText(warning)).toBeTruthy();
    fireEvent.input(allotment, { target: { value: "0" } });
    expect(queryByRole("status", { name: /below the 8-block/i })).toBeNull();
    fireEvent.input(allotment, { target: { value: "180" } });
    expect(getByText(warning)).toBeTruthy();
    fireEvent.input(allotment, { target: { value: "240" } });
    expect(queryByRole("status", { name: /below the 8-block/i })).toBeNull();
  });

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

  it("keeps Mint session slider, allotment, and saved payload synchronized", async () => {
    const { ui, store, controller } = makeHarness("ready");
    const inputs = store.getState().inputs!;
    // Wall-free sessions (the ready scenario's calendar walls sit 09:15-15:30):
    // the sync contract is exercised without wall filtering noise.
    const sessions = [
      { id: "mint:morning:08:30", name: "Mint Morning · 08:30", slot: "Morning", start: "08:30", end: "09:00" },
      { id: "mint:afternoon:13:30", name: "Mint Afternoon · 13:30", slot: "Afternoon", start: "13:30", end: "14:00" },
      { id: "mint:afternoon:14:00", name: "Mint Afternoon · 14:00", slot: "Afternoon", start: "14:00", end: "14:30" },
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
    fireEvent.input(getByLabelText("Mint session count"), { target: { value: "2" } });
    expect((getByLabelText("Mint allotment") as HTMLInputElement).value).toBe("60");
    fireEvent.input(getByLabelText("Mint allotment"), { target: { value: "90" } });
    expect(getByText("3 / 3")).toBeTruthy();

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
        // Anchor behavior is exercised wall-free; wall filtering has its own
        // dedicated FEEDBACK-28 tests.
        anchored: inputs.anchored.filter((a) => a.kind !== "calendar"),
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
    const { getByLabelText, getAllByText } = ui(<SetupDrawer />);

    expect((getByLabelText("Mint session count") as HTMLInputElement).value).toBe("2");
    expect(getAllByText("Selected", { exact: true }).length).toBe(2);

    fireEvent.input(getByLabelText("Start (anchor)"), { target: { value: "08:00" } });
    expect((getByLabelText("Mint session count") as HTMLInputElement).value).toBe("2");
  });

  /* FEEDBACK-28: the August 17 incident — Day Setup selected the first
     post-anchor Mint rows without checking calendar walls, so Mint 15:00-15:30
     collided with the OPPD fixed wall at 15:00. Default, edited, and saved
     Mint choices must all exclude fixed or work wall overlaps. */
  describe("FEEDBACK-28 wall-aware Mint choices (SetupDrawer)", () => {
    function mintWallHarness() {
      const h = makeHarness("ready");
      const inputs = structuredClone(h.store.getState().inputs!);
      const sessions = [
        { id: "mint:morning:08:30", name: "Mint Morning · 08:30", slot: "Morning", start: "08:30", end: "09:00" },
        { id: "mint:morning:09:00", name: "Mint Morning · 09:00", slot: "Morning", start: "09:00", end: "09:30" },
        { id: "mint:afternoon:15:00", name: "Mint Afternoon · 15:00", slot: "Afternoon", start: "15:00", end: "15:30" },
      ];
      inputs.anchored = [
        ...inputs.anchored.filter((a) => a.kind !== "calendar"),
        {
          id: "OPPD meter read", name: "OPPD meter read", kind: "calendar",
          start: "15:00", end: "15:30", durationMin: 30, overlapAllowed: false,
          on: true, skipToday: false, calendarId: "oppd", calendarTitle: "OPPD",
          capacityClass: "fixed",
        },
      ];
      inputs.daySemantics = {
        ...inputs.daySemantics,
        mintEnabled: true,
        effectiveAllotmentMinutes: 90,
        mintSessions: sessions,
      };
      inputs.daySetup = {
        ...inputs.daySetup,
        workAllotmentMinutes: 60,
        schedulable: { minting: { on: true, sessions: [sessions[0].id, sessions[2].id] } },
      };
      h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
      h.store.dispatch({ type: "SETUP_SAVED", daySetup: inputs.daySetup });
      h.store.dispatch({ type: "UI", patch: { setupOpen: true } });
      return { ...h, sessions };
    }

    it("default selection never includes the wall-conflicting 15:00 session", () => {
      const { ui } = mintWallHarness();
      const { getByText } = ui(<SetupDrawer />);
      // The stale saved choice [08:30, 15:00] keeps its wall-free half;
      // 15:00-15:30 overlaps OPPD 15:00-15:30 and is never restored.
      expect(getByText("Mint Morning · 08:30").closest(".mint-session-row")?.className)
        .toContain("mint-session-row--selected");
      expect(getByText("Mint Afternoon · 15:00").closest(".mint-session-row")?.className)
        .not.toContain("mint-session-row--selected");
      expect(getByText("Blocked by wall")).toBeTruthy();
    });

    it("edited allotment still excludes the wall-conflicting session and save payload is clean", async () => {
      const { ui, controller, sessions } = mintWallHarness();
      const save = vi.spyOn(controller, "saveDaySetup").mockResolvedValue();
      const { getByLabelText, getByText } = ui(<SetupDrawer />);
      const slider = getByLabelText("Mint allotment") as HTMLInputElement;
      // The wall-conflicting row is not part of the selectable slider range.
      fireEvent.input(slider, { target: { value: "90" } });
      expect((getByLabelText("Mint allotment") as HTMLInputElement).value).toBe("60");
      await act(async () => {
        fireEvent.click(getByText("Save day setup"));
      });
      expect(save).toHaveBeenCalledWith(expect.objectContaining({
        workAllotmentMinutes: 60,
        schedulable: {
          minting: {
            on: true,
            n: 2,
            sessions: [sessions[0].id, sessions[1].id],
          },
        },
      }));
    });
  });

  /* FEEDBACK-28: an unlisted timed calendar defaults to fixed and must remain
     visible — the incident silently omitted real events before Mint selection
     could avoid them. */
  it("surfaces an unlisted timed calendar (A + M Busy Bees) as a fixed wall", () => {
    const { ui, store } = makeHarness("ready");
    const inputs = structuredClone(store.getState().inputs!);
    inputs.anchored = [
      {
        id: "A + M Busy Bees", name: "A + M Busy Bees", kind: "calendar",
        start: "10:30", end: "11:00", durationMin: 30, overlapAllowed: false,
        on: true, skipToday: false, calendarId: "busy-bees",
        calendarTitle: "A + M Busy Bees",
      },
    ];
    store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: store.getState().ledger! });
    const r = ui(<CalendarImpact />);
    expect(r.getAllByText("A + M Busy Bees").length).toBeGreaterThan(0);
    // U10: select the block to open its inline detail row.
    fireEvent.click(r.getByRole("button", { name: /A \+ M Busy Bees/ }));
    expect(r.getByText("Fixed")).toBeTruthy();
    expect(r.getByText("hard block")).toBeTruthy();
    expect(r.getByText("1 blk counted")).toBeTruthy();
    // And it is a real wall for Mint selection / overflow avoidance.
    const walls = calendarWalls(effectiveAnchoredBlocks(store.getState()));
    expect(walls).toEqual([{ start: 10 * 60 + 30, end: 11 * 60 }]);
  });
});

describe("T13 canvas deletion", () => {
  it("keeps the allocator and editor paths but serves no timeline or Edit-day canvas", () => {
    const { ui, store } = makeHarness("fresh");
    // Plan-surface test: the app now boots on Set up day, so select Plan.
    store.dispatch({ type: "UI", patch: { screen: "plan", settingsPanel: null } });
    const { queryByRole, queryByLabelText, queryByText, container } = ui(<App />);
    // Pre-commit NOW/NEXT is dead space — absent by design (brief problem 5).
    expect(container.querySelector(".execution")).toBeNull();
    // IMP-07: the allocator surface is named "Today's work" (item 1).
    expect(queryByLabelText("Assigned this morning")).toBeTruthy();
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

  it("derives execution card time labels from the supplied row interval", () => {
    const h = makeHarness("verified");
    const inputs = h.store.getState().inputs!;
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: { ...inputs, time: { ...inputs.time, now: "06:20" } },
      ledger: h.store.getState().ledger!,
    });
    h.store.dispatch({
      type: "SEQUENCE_OK",
      sequence: [{ id: "Magic Mirror", start: "06:10", end: "06:55", zone: null, kind: "work" }],
      warnings: [],
      fingerprint: "fixed",
      anchoredSourceFingerprint: inputs.anchoredSourceFingerprint,
      planningConfigFingerprint: inputs.planningConfigFingerprint,
      ledger: h.store.getState().ledger!,
    });
    h.store.dispatch({ type: "COMMIT_DONE", report: { status: "ok", surfaces: [], verifyFailures: [] } });

    const { getByText } = h.ui(<ExecutionView />);
    expect(getByText("6:10 AM – 6:55 AM")).toBeTruthy();
  });

  it("keeps parent-child context visible in the committed schedule", () => {
    const h = makeHarness("verified");
    const inputs = h.store.getState().inputs!;
    const parent = inputs.assigned.find((item) => item.name === "Magic Mirror")!;
    const child = inputs.assigned.find((item) => item.name === "Review AWS module 4")!;
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        time: { ...inputs.time, now: "06:20" },
        assigned: [
          ...inputs.assigned.map((item) =>
            item.id === parent.id
              ? { ...item, tags: ["morning systems"] }
              : item.id === child.id
                ? { ...item, relatesTo: "[[Magic Mirror]]", tags: ["morning systems"] }
                : item,
          ),
        ],
      },
      ledger: h.store.getState().ledger!,
    });
    h.store.dispatch({
      type: "SEQUENCE_OK",
      sequence: [
        { id: parent.id, start: "06:10", end: "06:55", zone: null, kind: "work" },
        { id: child.id, start: "07:00", end: "07:30", zone: null, kind: "work" },
      ],
      warnings: [],
      fingerprint: "fixed",
      anchoredSourceFingerprint: inputs.anchoredSourceFingerprint,
      planningConfigFingerprint: inputs.planningConfigFingerprint,
      ledger: h.store.getState().ledger!,
    });
    h.store.dispatch({ type: "COMMIT_DONE", report: { status: "ok", surfaces: [], verifyFailures: [] } });

    const { getByText } = h.ui(<ExecutionView />);
    expect(getByText(/child of Magic Mirror/)).toBeTruthy();
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

  it("renders structured validation diagnostics with affected rows and exact quarter-hour intervals", () => {
    const { ui, store } = makeHarness("ready");
    store.dispatch({
      type: "VALIDATED",
      validation: {
        ok: false,
        hardErrors: ["A overlaps Dentist"],
        warnings: [],
        diagnostics: [{
          rule: "calendar_overlap",
          severity: "error",
          detail: "A overlaps Dentist",
          affectedRows: ["A", "Dentist"],
          intervals: [
            { id: "A", start: "09:15", end: "10:30" },
            { id: "Dentist", start: "09:00", end: "10:00" },
          ],
        }],
      },
    });
    const { container, getByText } = ui(<FooterBanners />);
    fireEvent.click(container.querySelector(".alert-pill--error") as Element);

    expect(getByText("A overlaps Dentist")).toBeTruthy();
    expect(getByText(/affected rows: A, Dentist/)).toBeTruthy();
    expect(getByText(/A 9:15 AM–10:30 AM/)).toBeTruthy();
    expect(getByText(/Dentist 9 AM–10 AM/)).toBeTruthy();
    // The flattened hard-error string is replaced, not rendered twice.
    expect(container.querySelectorAll(".alert-pills__item").length).toBe(1);
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
    // U11 labeling (2026-10-10): provenance LEADS the line as a coloured
    // source chip (Todoist / Capacities), then the human KIND, then
    // plain meta. The raw uppercase TASK/ROOTTASK/UUID badge is gone.
    expect(rows.length).toBeGreaterThan(0);
    for (const el of rows) {
      const chip = el.querySelector(".qrow__src");
      expect(chip).toBeTruthy();
      expect((chip as HTMLElement).textContent).toMatch(
        /^(Todoist|Capacities|Calendar)$/,
      );
    }
    // The source itself is still named on the line.
    expect(rows.map((el) => el.textContent).join(" ")).toMatch(/Todoist|Capacities/);
    const kinds = rows.map((el) => el.querySelector(".qrow__type")?.textContent);
    expect(new Set(kinds.filter(Boolean)).size).toBeGreaterThan(0);
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

  it("recurring rows expose the source skip-of-occurrence — placement-immune must not mean undroppable", () => {
    /* T12 qualification (2026-07-26): recurring rows rendered no exclude verb
       at all, so LOOTS and M2.5 could only be dropped from the day by marking
       them done (a lie), deferring (moves them), or deleting permanently
       (destructive). There was no "not today". Being pinned to a native time
       is a placement constraint, not a reason to be undroppable. F4a moved the
       verb from the browser-only Exclude control to a durable source write; U4
       renamed it after what it does upstream and put it behind a confirm, so a
       recurring row reads "Skip occurrence in Todoist…". */
    const h = makeHarness("ready");
    withRecurring(h);
    const { container } = h.ui(<Queue />);
    const loots = Array.from(
      container.querySelectorAll<HTMLElement>(".qrow"),
    ).find((r) => r.querySelector(".qrow__name")?.textContent === "LOOTS");
    const skip = Array.from(
      loots?.querySelectorAll<HTMLElement>(".trow__btn") ?? [],
    ).find((b) => b.getAttribute("aria-label") === SKIP_OCCURRENCE_TRIGGER_LABEL);
    expect(skip).toBeTruthy();
  });

  it("shows a protected-by-default time-movement opt-in per timed row", () => {
    // F7: the More menu's "Allow move" item is the permission checkbox in the
    // expanded row's time editor; it stays off until granted and Save commits it.
    const h = makeHarness("ready");
    withRecurring(h);
    const { container, getByText } = h.ui(<Queue />);
    const loots = () =>
      Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
        (r) => r.querySelector(".qrow__name")?.textContent === "LOOTS",
      )!;
    fireEvent.click(loots().querySelector('button[aria-label="Expand"]')!);
    expect(loots().textContent).toContain("Source 12:30 PM");
    fireEvent.click(getByText("Set time"));
    const allow = loots().querySelector<HTMLInputElement>(
      '.trow__editor input[type="checkbox"]',
    )!;
    expect(allow.checked).toBe(false);
    expect(allow.closest("label")?.textContent).toContain("Allow changing source time");
    fireEvent.click(allow);
    fireEvent.click(getByText("Apply"));
    expect(h.store.getState().timeAdjustmentOptIns).toEqual({});
    fireEvent.click(getByText("Save"));
    expect(h.store.getState().timeAdjustmentOptIns).toEqual({ LOOTS: true });
  });

  it("shows the source time only for Todoist rows with a native start", () => {
    const h = makeHarness("ready");
    const inputs = structuredClone(h.store.getState().inputs!);
    const base = inputs.assigned.find((item) => item.source === "todoist")!;
    inputs.assigned.push(
      {
        ...base,
        id: "Timed Todoist",
        name: "Timed Todoist",
        todoistId: "timed-todoist",
        scheduledStart: "14:00",
        blocks: 1,
        durationLabel: "30min",
      },
      {
        ...base,
        id: "Untimed Todoist",
        name: "Untimed Todoist",
        todoistId: "untimed-todoist",
        scheduledStart: null,
        blocks: 1,
        durationLabel: "30min",
      },
      {
        ...base,
        id: "All-day Todoist",
        name: "All-day Todoist",
        todoistId: "all-day-todoist",
        scheduledStart: null,
        blocks: 0,
        durationLabel: "All day",
      },
    );
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs,
      ledger: { today: inputs.validDate, spent: 0, cap: 7, remaining: 7 },
    });
    const { container } = h.ui(<Queue />);
    const expand = (name: string) => {
      const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
        (r) => r.querySelector(".qrow__name")?.textContent === name,
      )!;
      fireEvent.click(row.querySelector('button[aria-label="Expand"]')!);
      return Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
        (r) => r.querySelector(".qrow__name")?.textContent === name,
      )!;
    };
    expect(expand("Timed Todoist").querySelector(".trow__start")?.textContent).toContain(
      "Source 2 PM",
    );
    const untimed = expand("Untimed Todoist").querySelector(".trow__start")?.textContent;
    expect(untimed).toContain("Auto");
    expect(untimed).not.toContain("Source");
    expect(expand("All-day Todoist").querySelector(".trow__start")?.textContent).toContain(
      "none (all-day)",
    );
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
        path: null, source: "todoist",
        types: ["task"], urgency: "4", deadline: null,
        priorityScore: 20, blocks: 1, durationLabel: "30min", todoistId: "t8",
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

  it("a top-priority todoist row renders a crit tier chip, never the raw int", () => {
    const h = makeHarness("ready");
    withLegibilityRows(h);
    const { container } = h.ui(<Queue />);
    expect(container.querySelector(".qrow__prio--crit")).toBeTruthy();
  });

  it("todoist priority int renders as a p-level", () => {
    const h = makeHarness("ready");
    withLegibilityRows(h);
    expect(h.ui(<Queue />).getAllByText("p1").length).toBeGreaterThan(0);
  });

  it("past deadlines render an overdue day count, not a bare date", () => {
    const h = makeHarness("ready");
    withLegibilityRows(h);
    const { container } = h.ui(<Queue />);
    const overdue = Array.from(container.querySelectorAll(".due--overdue"));
    expect(overdue.length).toBeGreaterThanOrEqual(1);
    for (const el of overdue) {
      expect((el as HTMLElement).textContent).toMatch(/^overdue \d+d$/);
      expect((el as HTMLElement).closest(".qrow")?.classList.contains("trow--overdue")).toBe(true);
    }
  });
});

describe("P6-01 grouping relationship fixtures", () => {
  it("keeps a related infeasible child visible once, ordered after its parent", () => {
    const h = makeHarness("ready");
    const inputs = structuredClone(h.store.getState().inputs!);
    const base = inputs.assigned[0];
    const parent = {
      ...base,
      id: "Zeta parent",
      name: "Zeta parent",
      path: null,
      urgency: null,
      deadline: null,
      blocks: 2,
      durationLabel: "1hr",
      tags: ["systems"],
    };
    const child = {
      ...base,
      id: "Alpha child",
      name: "Alpha child",
      path: null,
      urgency: null,
      deadline: null,
      blocks: 1,
      durationLabel: "30min",
      relatesTo: "[[Zeta parent]]",
      tags: ["systems"],
    };
    inputs.assigned = [child, parent];
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs,
      ledger: { today: inputs.validDate, spent: 0, cap: 3, remaining: 3 },
    });
    h.store.dispatch({
      type: "VALIDATED",
      validation: {
        ok: true,
        hardErrors: [],
        warnings: ["⚠ overflow infeasible — Alpha child: no free gap"],
      },
    });

    const { container } = h.ui(<Queue />);
    const names = [...(container as HTMLElement).querySelectorAll<HTMLElement>(".qrow__name")]
      .map((node) => node.textContent);
    expect(names).toEqual(["Zeta parent", "Alpha child"]);
    expect(new Set(names).size).toBe(2);
  });
});
