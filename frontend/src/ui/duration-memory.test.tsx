/* duration-memory.test.tsx — duration-memory MVP (2026-08-17): the
   remembered/source chip distinction, strict invalid-value blocking, the
   explicit save and per-item reset mutations, failure preservation, and the
   accessible pending/error state on the existing controls. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { Ctx } from "./context";
import { createStore } from "../store/createStore";
import { Controller } from "../store/controller";
import { FixtureAdapter } from "../adapters/fixture";
import { makeScenario } from "../fixtures/scenarios";
import { Queue } from "./Queue";
import { BlockEditor } from "./BlockEditor";

afterEach(cleanup);

function memoryHarness() {
  const sc = makeScenario("ready");
  const store = createStore();
  store.dispatch({ type: "INPUTS_LOADED", inputs: sc.inputs, ledger: { ...sc.ledger } });
  const adapter = new FixtureAdapter("ready");
  const controller = new Controller(adapter, store.dispatch, store.getState);
  const ui = (children: Parameters<typeof render>[0]) =>
    render(<Ctx.Provider value={{ store, controller }}>{children}</Ctx.Provider>);
  return { store, controller, adapter, ui };
}

const PRESS = "Press"; // vault row, path 50 - Operations/Pursuits/Press.md

function openEditor(h: ReturnType<typeof memoryHarness>) {
  h.store.dispatch({
    type: "UI",
    patch: { editorItem: PRESS, editorIntent: "duration" },
  });
}

describe("duration-memory MVP: chip distinguishes remembered and source", () => {
  it("a durable-remembered row renders 'remembered', not 'source'", () => {
    const sc = makeScenario("ready");
    sc.inputs.assigned = sc.inputs.assigned.map((r) =>
      r.id === PRESS ? { ...r, durationSource: "remembered" } : r,
    );
    const store = createStore();
    store.dispatch({ type: "INPUTS_LOADED", inputs: sc.inputs, ledger: { ...sc.ledger } });
    const adapter = new FixtureAdapter("ready");
    const controller = new Controller(adapter, store.dispatch, store.getState);
    const { container } = render(
      <Ctx.Provider value={{ store, controller }}>
        <Queue />
      </Ctx.Provider>,
    );
    const slider = container.querySelector(
      'input[aria-label="Press duration in 15-minute steps"]',
    ) as HTMLInputElement;
    expect(slider.getAttribute("aria-valuetext")).toMatch(/\(remembered\)$/);
    const chip = slider
      .closest(".qrow")!
      .querySelector(".qrow__src-tag") as HTMLElement;
    expect(chip.textContent).toBe("remembered");
    expect(chip.className).toContain("qrow__src-tag--remembered");
  });

  it("a plain source-resolved row keeps the existing 'source' label", () => {
    const h = memoryHarness();
    const { container } = h.ui(<Queue />);
    const slider = container.querySelector(
      'input[aria-label="Press duration in 15-minute steps"]',
    ) as HTMLInputElement;
    expect(slider.getAttribute("aria-valuetext")).toMatch(/\(source\)$/);
    const chip = slider
      .closest(".qrow")!
      .querySelector(".qrow__src-tag") as HTMLElement;
    expect(chip.textContent).toBe("source");
  });
});

describe("duration-memory MVP: explicit save in the exact editor", () => {
  it("an off-grid typed value is blocked with an alert and NO adapter call", async () => {
    const h = memoryHarness();
    const save = vi.spyOn(h.adapter, "saveDurationMemory");
    openEditor(h);
    const { getByLabelText, getByText } = h.ui(<BlockEditor />);
    const minutes = getByLabelText("Exact minutes (5-minute steps)") as HTMLInputElement;
    fireEvent.input(minutes, { target: { value: "7" } });
    fireEvent.click(getByText("Save duration"));
    expect(save).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(getByText(/5-minute steps/)).toBeTruthy();
    });
    // The invalid value never became a mutation attempt: no store entry, no
    // pending state, no error record — the editor's local guard blocked it.
    expect(h.store.getState().durationMemory[PRESS]).toBeUndefined();
  });

  it("save sends exactly ONE mutation, updates the model, and closes on success", async () => {
    const h = memoryHarness();
    const save = vi.spyOn(h.adapter, "saveDurationMemory").mockResolvedValue({
      identity: "50 - Operations/Pursuits/Press.md",
      minutes: 90,
      source: "remembered",
    });
    openEditor(h);
    const { getByLabelText, getByText } = h.ui(<BlockEditor />);
    const minutes = getByLabelText("Exact minutes (5-minute steps)") as HTMLInputElement;
    fireEvent.input(minutes, { target: { value: "90" } });
    fireEvent.click(getByText("Save duration"));
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith("50 - Operations/Pursuits/Press.md", 90);
    await waitFor(() => {
      expect(h.store.getState().ui.editorItem).toBeNull();
    });
    const row = h.store.getState().inputs!.assigned.find((r) => r.id === PRESS)!;
    expect(row.blocks).toBe(3);
    expect(row.durationSource).toBe("remembered");
  });

  it("save exposes accessible pending state and never claims success before the response", async () => {
    const h = memoryHarness();
    let resolve!: (v: { identity: string; minutes: number; source: "remembered" }) => void;
    const gate = new Promise<{ identity: string; minutes: number; source: "remembered" }>((r) => {
      resolve = r;
    });
    vi.spyOn(h.adapter, "saveDurationMemory").mockImplementation(() => gate);
    openEditor(h);
    const { getByLabelText, getByText } = h.ui(<BlockEditor />);
    fireEvent.input(getByLabelText("Exact minutes (5-minute steps)"), {
      target: { value: "60" },
    });
    const saveBtn = getByText("Save duration").closest("button")!;
    fireEvent.click(saveBtn);
    expect(saveBtn.disabled).toBe(true);
    expect(saveBtn.getAttribute("aria-busy")).toBe("true");
    expect(h.store.getState().durationMemory[PRESS]?.pending).toBe(true);
    resolve({ identity: "50 - Operations/Pursuits/Press.md", minutes: 60, source: "remembered" });
    await waitFor(() => {
      expect(h.store.getState().ui.editorItem).toBeNull();
    });
  });

  it("save failure keeps the editor open with an accessible alert and the old value", async () => {
    const h = memoryHarness();
    vi.spyOn(h.adapter, "saveDurationMemory").mockRejectedValue(new Error("server 503"));
    openEditor(h);
    const { getByLabelText, getByText } = h.ui(<BlockEditor />);
    fireEvent.input(getByLabelText("Exact minutes (5-minute steps)"), {
      target: { value: "90" },
    });
    fireEvent.click(getByText("Save duration"));
    await waitFor(() => {
      expect(getByText(/server 503/)).toBeTruthy();
    });
    expect(h.store.getState().ui.editorItem).toBe(PRESS); // stays open
    const row = h.store.getState().inputs!.assigned.find((r) => r.id === PRESS)!;
    expect(row.durationSource).not.toBe("remembered");
  });
});

describe("duration-memory MVP: per-item reset in the exact editor", () => {
  it("reset applies the returned source fallback and drops the remembered label", async () => {
    const h = memoryHarness();
    const inputs = h.store.getState().inputs!;
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        assigned: inputs.assigned.map((r) =>
          r.id === PRESS ? { ...r, blocks: 3, durationSource: "remembered" } : r,
        ),
      },
      ledger: h.store.getState().ledger!,
    });
    const reset = vi.spyOn(h.adapter, "resetDurationMemory").mockResolvedValue({
      identity: "50 - Operations/Pursuits/Press.md",
      minutes: 60,
      source: "default",
    });
    openEditor(h);
    const { getByText } = h.ui(<BlockEditor />);
    fireEvent.click(getByText("Reset duration"));
    expect(reset).toHaveBeenCalledTimes(1);
    expect(reset).toHaveBeenCalledWith("50 - Operations/Pursuits/Press.md");
    await waitFor(() => {
      expect(h.store.getState().ui.editorItem).toBeNull();
    });
    const row = h.store.getState().inputs!.assigned.find((r) => r.id === PRESS)!;
    expect(row.blocks).toBe(2);
    expect(row.durationSource).toBe("default");
  });

  it("reset failure preserves the remembered value and reports the error", async () => {
    const h = memoryHarness();
    const inputs = h.store.getState().inputs!;
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        assigned: inputs.assigned.map((r) =>
          r.id === PRESS ? { ...r, blocks: 3, durationSource: "remembered" } : r,
        ),
      },
      ledger: h.store.getState().ledger!,
    });
    vi.spyOn(h.adapter, "resetDurationMemory").mockRejectedValue(new Error("reset failed"));
    openEditor(h);
    const { getByText } = h.ui(<BlockEditor />);
    fireEvent.click(getByText("Reset duration"));
    await waitFor(() => {
      expect(getByText(/reset failed/)).toBeTruthy();
    });
    const row = h.store.getState().inputs!.assigned.find((r) => r.id === PRESS)!;
    expect(row.blocks).toBe(3);
    expect(row.durationSource).toBe("remembered");
    expect(h.store.getState().ui.editorItem).toBe(PRESS);
  });

  // FT-05 F2: found:false must never render All day or zero — the remembered
  // row stays authoritative and a bounded failure alert surfaces instead.
  it("reset with no source fallback keeps the remembered value and shows a bounded alert", async () => {
    const h = memoryHarness();
    const inputs = h.store.getState().inputs!;
    h.store.dispatch({
      type: "INPUTS_LOADED",
      inputs: {
        ...inputs,
        assigned: inputs.assigned.map((r) =>
          r.id === PRESS ? { ...r, blocks: 3, durationSource: "remembered" } : r,
        ),
      },
      ledger: h.store.getState().ledger!,
    });
    vi.spyOn(h.adapter, "resetDurationMemory").mockResolvedValue({
      identity: "50 - Operations/Pursuits/Press.md",
      minutes: null,
      source: "default",
    });
    openEditor(h);
    const { getByText, queryByText } = h.ui(<BlockEditor />);
    fireEvent.click(getByText("Reset duration"));
    await waitFor(() => {
      expect(getByText(/no source/i)).toBeTruthy();
    });
    const row = h.store.getState().inputs!.assigned.find((r) => r.id === PRESS)!;
    expect(row.blocks).toBe(3);
    expect(row.durationSource).toBe("remembered");
    expect(queryByText("All day")).toBeNull();
    expect(h.store.getState().ui.editorItem).toBe(PRESS);
  });
});

describe("duration-memory MVP: keyboard-operable controls", () => {
  it("save and reset are native focusable buttons with accessible labels", () => {
    const h = memoryHarness();
    openEditor(h);
    const { getByRole } = h.ui(<BlockEditor />);
    const save = getByRole("button", { name: "Save duration" }) as HTMLButtonElement;
    const reset = getByRole("button", { name: "Reset duration" }) as HTMLButtonElement;
    expect(save.tagName).toBe("BUTTON");
    expect(reset.tagName).toBe("BUTTON");
    save.focus();
    expect(document.activeElement).toBe(save);
    reset.focus();
    expect(document.activeElement).toBe(reset);
  });
});
