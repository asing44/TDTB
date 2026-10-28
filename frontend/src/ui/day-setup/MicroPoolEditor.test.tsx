/* MicroPoolEditor: the Set up day drawer that edits the micro-adventure pool
   items themselves (idea, category, effort, active). It is a form over the
   store's microPool slice: every edit is local until Save, which hands the FULL
   pool to onSave. Existing ids never change; new ids continue the maNN pattern. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/preact";

import type { MicroPoolItem } from "../../model/types";
import type { MicroPoolState } from "../../store/store";
import { MicroPoolEditor } from "./MicroPoolEditor";

afterEach(cleanup);

const POOL: MicroPoolItem[] = [
  { id: "ma01", idea: "Watch sunset", category: "nature", effort: "low", active: true },
  { id: "ma02", idea: "Try pottery", category: "craft", effort: "high", active: false },
];

function state(over: Partial<MicroPoolState> = {}): MicroPoolState {
  return {
    items: POOL, load: "idle", loadError: null, save: "idle", saveError: null, shadowed: false,
    ...over,
  };
}

function editor(over: Partial<MicroPoolState> = {}, handlers: { onSave?: any; onLoad?: any; onClose?: any } = {}) {
  const onSave = handlers.onSave ?? vi.fn();
  const onLoad = handlers.onLoad ?? vi.fn();
  const onClose = handlers.onClose ?? vi.fn();
  const r = render(<MicroPoolEditor pool={state(over)} onSave={onSave} onLoad={onLoad} onClose={onClose} />);
  return { r, onSave, onLoad, onClose };
}

const val = (r: ReturnType<typeof render>, label: string) =>
  (r.getByLabelText(label) as HTMLInputElement).value;

describe("MicroPoolEditor", () => {
  it("is a named modal dialog listing each idea with category and effort", () => {
    const { r } = editor();
    const dialog = r.getByRole("dialog", { name: "Micro-adventure ideas" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(val(r, "Idea 1")).toBe("Watch sunset");
    expect(val(r, "Category 1")).toBe("nature");
    expect(val(r, "Effort 1")).toBe("low");
    expect(val(r, "Idea 2")).toBe("Try pottery");
  });

  it("says where the edit is written", () => {
    const { r } = editor();
    expect(r.container.textContent).toContain("config/tdtb.json");
  });

  it("edits an idea locally and saves the full pool with ids untouched", () => {
    const { r, onSave } = editor();
    fireEvent.input(r.getByLabelText("Idea 1"), { target: { value: "Watch the sunset" } });
    fireEvent.click(r.getByRole("button", { name: "Save to config" }));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith([{ ...POOL[0], idea: "Watch the sunset" }, POOL[1]]);
  });

  it("toggles the active switch", () => {
    const { r, onSave } = editor();
    const sw = r.getByRole("switch", { name: /Try pottery/ }) as HTMLInputElement;
    expect(sw.checked).toBe(false);
    fireEvent.click(sw);
    fireEvent.click(r.getByRole("button", { name: "Save to config" }));
    expect(onSave.mock.calls[0][0][1]).toEqual({ ...POOL[1], active: true });
  });

  it("adds a row with a fresh maNN id that skips collisions", () => {
    const { r, onSave } = editor({
      items: [...POOL, { id: "ma03", idea: "x", category: "", effort: "", active: true }],
    });
    fireEvent.input(r.getByLabelText("New idea"), { target: { value: "Fly a kite" } });
    fireEvent.input(r.getByLabelText("New category"), { target: { value: "outdoors" } });
    fireEvent.input(r.getByLabelText("New effort"), { target: { value: "low" } });
    fireEvent.click(r.getByRole("button", { name: "Add idea" }));
    expect(val(r, "Idea 4")).toBe("Fly a kite");
    expect(val(r, "New idea")).toBe("");
    fireEvent.click(r.getByRole("button", { name: "Save to config" }));
    expect(onSave.mock.calls[0][0][3]).toEqual({
      id: "ma04", idea: "Fly a kite", category: "outdoors", effort: "low", active: true,
    });
  });

  it("will not add a blank idea", () => {
    const { r } = editor();
    fireEvent.click(r.getByRole("button", { name: "Add idea" }));
    expect(r.queryByLabelText("Idea 3")).toBeNull();
    expect(r.container.textContent).toContain("Enter an idea first");
  });

  it("removes a row", () => {
    const { r, onSave } = editor();
    fireEvent.click(r.getByRole("button", { name: "Remove Try pottery" }));
    expect(r.queryByLabelText("Idea 2")).toBeNull();
    fireEvent.click(r.getByRole("button", { name: "Save to config" }));
    expect(onSave).toHaveBeenCalledWith([POOL[0]]);
  });

  it("will not remove the last remaining idea", () => {
    const { r } = editor({ items: [POOL[0]] });
    const rm = r.getByRole("button", { name: "Remove Watch sunset" });
    expect(rm.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(rm);
    expect(val(r, "Idea 1")).toBe("Watch sunset");
    expect(r.container.textContent).toContain("can't be empty");
  });

  it("refuses to save a blank idea and says so", () => {
    const { r, onSave } = editor();
    fireEvent.input(r.getByLabelText("Idea 1"), { target: { value: "  " } });
    fireEvent.click(r.getByRole("button", { name: "Save to config" }));
    expect(onSave).not.toHaveBeenCalled();
    expect(r.getByRole("alert").textContent).toContain("Idea 1");
  });

  it("does not offer Save when nothing changed", () => {
    const { r, onSave } = editor();
    const save = r.getByRole("button", { name: "Save to config" });
    expect(save.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(save);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("keeps the frame visible while loading and announces it", () => {
    const { r } = editor({ items: null, load: "loading" });
    expect(r.getByRole("dialog")).toBeTruthy();
    expect(r.getByRole("status").textContent).toContain("Loading ideas");
  });

  it("names a failed load, keeps the last good ideas, and offers one Retry", () => {
    const { r, onLoad } = editor({ load: "failed", loadError: "offline" });
    expect(r.getByRole("alert").textContent).toContain("Could not load ideas: offline");
    expect(val(r, "Idea 1")).toBe("Watch sunset");
    fireEvent.click(r.getByRole("button", { name: "Retry" }));
    expect(onLoad).toHaveBeenCalledTimes(1);
  });

  it("says plainly when the pool is empty and cannot be saved empty", () => {
    const { r } = editor({ items: [] });
    expect(r.container.textContent).toContain("No ideas yet");
    expect(r.getByRole("button", { name: "Save to config" }).getAttribute("aria-disabled")).toBe("true");
  });

  it("shows a saving state", () => {
    const { r } = editor({ save: "saving" });
    expect(r.getByRole("status").textContent).toContain("Saving");
  });

  it("shows an honest saved state naming the file and the refresh", () => {
    const { r } = editor({ save: "saved" });
    const text = r.getByRole("status").textContent ?? "";
    expect(text).toContain("Saved to config/tdtb.json");
    expect(text).toContain("next source refresh");
  });

  it("shows a failed save with a Retry that saves the same draft", () => {
    const { r, onSave } = editor({ save: "failed", saveError: "idea is blank" });
    expect(r.container.textContent).toContain("Not saved");
    expect(r.getByRole("alert").textContent).toContain("idea is blank");
    fireEvent.click(r.getByRole("button", { name: "Retry" }));
    expect(onSave).toHaveBeenCalledWith(POOL);
  });

  it("surfaces the shadowed 409 honestly and offers no pretend retry", () => {
    const { r } = editor({ save: "failed", saveError: "overridden", shadowed: true });
    const text = r.getByRole("alert").textContent ?? "";
    expect(text).toContain("machine-local");
    expect(text).toContain("no effect");
    expect(r.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(r.container.textContent).not.toContain("Saved to");
  });

  it("closes on Escape and from the close button", () => {
    const { r, onClose } = editor();
    fireEvent.keyDown(r.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(r.getByRole("button", { name: "Close micro-adventure ideas" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("traps Tab inside the dialog", () => {
    const { r } = editor();
    const dialog = r.getByRole("dialog");
    const close = r.getByRole("button", { name: "Close micro-adventure ideas" });
    close.focus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    const focusables = dialog.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled])',
    );
    expect(document.activeElement).toBe(focusables[focusables.length - 1]);
  });
});
