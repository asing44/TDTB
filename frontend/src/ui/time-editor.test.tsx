/* time-editor.test.tsx — task-actions F4b: the inline exact-start editor.
   The editor's Apply commits at once through controller.applyRowEdit (no Save
   step); Clear drops the pin again. Fixture adapters only. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";

afterEach(cleanup);

function setup(protect: boolean) {
  const h = makeHarness("ready", (sc) => {
    if (protect) {
      const press = sc.inputs.assigned.find((a) => a.id === "Press")!;
      press.scheduledStart = "09:00";
    }
  });
  const r = h.ui(<Queue />);
  const container = r.container as HTMLElement;
  const row = () =>
    Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
      (x) => x.querySelector(".qrow__name")?.textContent === "Press",
    )!;
  fireEvent.click(row().querySelector('button[aria-label="Expand"]')!);
  const btn = (text: string) =>
    Array.from(row().querySelectorAll<HTMLButtonElement>("button")).find(
      (b) => b.textContent?.trim() === text,
    )!;
  return { h, row, btn };
}

describe("time editor", () => {
  it("applies a validated pin on Apply", () => {
    const { h, row, btn } = setup(false);
    fireEvent.click(btn("Set time"));
    const input = row().querySelector<HTMLInputElement>('input[type="time"]')!;
    fireEvent.input(input, { target: { value: "" } });
    expect(btn("Apply").disabled).toBe(true);
    fireEvent.input(input, { target: { value: "13:30" } });
    expect(btn("Apply").disabled).toBe(false);
    fireEvent.click(btn("Apply"));
    expect(h.store.getState().placements["Press"]).toBe("13:30");
    expect(h.store.getState().rowUndo["Press"]).toBeDefined();
    expect(row().querySelector(".trow__editor")).toBeNull();
    expect(btn("Clear")).toBeDefined();
  });

  it("requires the source-time permission before Apply on a protected row", () => {
    const { h, row, btn } = setup(true);
    fireEvent.click(btn("Set time"));
    const allow = row().querySelector<HTMLInputElement>(".trow__editor input[type=checkbox]")!;
    expect(allow.closest("label")?.textContent).toContain("Allow changing source time");
    expect(allow.closest("label")?.textContent).toContain("Save keeps this in today's plan only");
    expect(btn("Apply").disabled).toBe(true);
    fireEvent.click(allow);
    expect(btn("Apply").disabled).toBe(false);
    fireEvent.click(btn("Apply"));
    expect(h.store.getState().placements["Press"]).toBe("09:00");
    expect(h.store.getState().timeAdjustmentOptIns["Press"]).toBe(true);
  });

  it.each(["", "9:00", "24:00", "12:60", "13:30:01"])("rejects non-HH:MM input %s", (time) => {
    const { row, btn } = setup(false);
    fireEvent.click(btn("Set time"));
    fireEvent.input(row().querySelector('input[type="time"]')!, { target: { value: time } });
    expect(btn("Apply").disabled).toBe(true);
  });

  it("Cancel discards editor changes and returns focus", () => {
    const { h, row, btn } = setup(false);
    fireEvent.click(btn("Set time"));
    fireEvent.input(row().querySelector('input[type="time"]')!, { target: { value: "14:30" } });
    fireEvent.click(btn("Cancel"));
    // Nothing was applied: the reader never pressed Apply.
    expect(h.store.getState().placements["Press"] ?? null).toBeNull();
    expect(h.store.getState().rowUndo["Press"]).toBeUndefined();
    expect(row().querySelector(".trow__editor")).toBeNull();
    expect(document.activeElement).toBe(btn("Set time"));
  });

  it("applies and clears a protected pin without source actions or billed calls", () => {
    const { h, row, btn } = setup(true);
    const sourceAction = vi.spyOn(h.adapter, "runtimeAction");
    const commit = vi.spyOn(h.adapter, "liveCommit");
    const sequence = vi.spyOn(h.adapter, "autoSequence");
    fireEvent.click(btn("Set time"));
    fireEvent.input(row().querySelector('input[type="time"]')!, { target: { value: "14:30" } });
    fireEvent.click(row().querySelector('.trow__editor input[type="checkbox"]')!);
    fireEvent.click(btn("Apply"));
    expect(h.store.getState().placements["Press"]).toBe("14:30");
    fireEvent.click(btn("Clear"));
    expect(h.store.getState().placements["Press"] ?? null).toBeNull();
    expect(h.store.getState().timeAdjustmentOptIns["Press"] ?? false).toBe(false);
    expect(sourceAction).not.toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled();
    expect(sequence).not.toHaveBeenCalled();
  });

  it("does not show the permission for an unprotected row", () => {
    const { row, btn } = setup(false);
    fireEvent.click(btn("Set time"));
    expect(row().querySelector(".trow__editor input[type=checkbox]")).toBeNull();
  });

  it("Escape closes the editor and returns focus to its trigger", () => {
    const { row, btn } = setup(false);
    fireEvent.click(btn("Set time"));
    const editor = row().querySelector<HTMLElement>(".trow__editor")!;
    fireEvent.keyDown(editor, { key: "Escape" });
    expect(row().querySelector(".trow__editor")).toBeNull();
    expect(document.activeElement?.id).toMatch(/^start-btn-/);
  });
});
