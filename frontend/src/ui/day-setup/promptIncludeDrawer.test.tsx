/* Drawer-variant Captures section: the For Meegy and Stoic captures carry the
   same include-in-commit switch as the Morning prompts card, so both surfaces
   agree. OFF is prompt-only: the textarea keeps its draft text and stays
   editable (just muted), the skip-description control is disabled because
   nothing is exported, and the day is never confirmed. Reminder has no switch. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";
import type { PromptDisabled } from "../../model/types";
import { DaySetupPanel } from "../DaySetupPanel";
import { makeHarness } from "../test-harness";

afterEach(cleanup);

function drawer(promptDisabled?: PromptDisabled) {
  const h = makeHarness("fresh", (sc) => {
    if (promptDisabled) {
      sc.inputs.daySetup = { ...sc.inputs.daySetup, promptDisabled };
    }
  });
  const r = h.ui(<DaySetupPanel active={true} />);
  return { h, r };
}

describe("drawer Captures include-in-commit switches", () => {
  it("shows one switch on For Meegy and Stoic, and none on Reminder", () => {
    const { r } = drawer();
    const meegy = r.getByLabelText("Include For Meegy in commit") as HTMLInputElement;
    const stoic = r.getByLabelText("Include Stoic in commit") as HTMLInputElement;
    expect(meegy.getAttribute("role")).toBe("switch");
    expect(meegy.getAttribute("aria-checked")).toBe("true");
    expect(meegy.checked).toBe(true);
    expect(stoic.checked).toBe(true);
    expect(meegy.closest(".field")?.querySelector("#cap-meegy")).toBeTruthy();
    expect(stoic.closest(".field")?.querySelector("#cap-stoic")).toBeTruthy();
    const reminder = r.getByLabelText("Reminder").closest(".field") as HTMLElement;
    expect(reminder.querySelector('[role="switch"]')).toBeNull();
    expect(r.getAllByText("Include in commit")).toHaveLength(2);
  });

  it("follows the day-setup echo", () => {
    const { r } = drawer({ forMeegy: true, stoic: false });
    const meegy = r.getByLabelText("Include For Meegy in commit") as HTMLInputElement;
    const stoic = r.getByLabelText("Include Stoic in commit") as HTMLInputElement;
    expect(meegy.checked).toBe(false);
    expect(meegy.getAttribute("aria-checked")).toBe("false");
    expect(stoic.checked).toBe(true);
  });

  it("toggles through the prompt-only path without confirming the day", async () => {
    const { h, r } = drawer();
    const save = vi.spyOn(h.controller, "savePromptDisabled");
    fireEvent.click(r.getByLabelText("Include Stoic in commit"));
    await vi.waitFor(() => {
      expect(save).toHaveBeenLastCalledWith({ forMeegy: false, stoic: true });
    });
    expect(h.store.getState().daySetup.confirmed).toBe(false);
    expect(h.store.getState().daySetup.captures).toEqual({
      intention: "",
      forMeegy: "",
      stoic: "",
    });
  });

  it("leaves the textarea editable and mutes it when the prompt is off", () => {
    const { r } = drawer({ forMeegy: true, stoic: false });
    const meegy = r.getByLabelText("For Meegy") as HTMLTextAreaElement;
    const stoic = r.getByLabelText("Stoic") as HTMLTextAreaElement;
    expect(meegy.disabled).toBe(false);
    expect(meegy.className).toContain("trow__muted");
    expect(stoic.className).not.toContain("trow__muted");
  });
});
