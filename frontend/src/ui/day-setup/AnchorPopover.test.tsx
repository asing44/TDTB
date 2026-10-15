/* Planning 2a anchor popover: the 300px card under a selected anchor block.
   Starts edits today only (the Move handler), Window is read-only, the Count
   today switch is the existing skip-today action, Edit items opens the shared
   anchored editor, Reset to default clears today's override time, and Save as
   default persists today's time to config. Escape and an outside click close
   it. jsdom has no layout, so placement is read from the computePopoverPlacement
   math with zero-size rects. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/preact";
import { AnchorPopover, CalendarPopover, type AnchorTimeState, type CalendarPopoverEvent } from "./AnchorPopover";

afterEach(() => {
  cleanup();
  document.querySelectorAll("[data-anchor-id]").forEach((el) => el.remove());
});

const clean: AnchorTimeState = {
  configTime: "08:30",
  dirty: false,
  saved: false,
  saving: false,
  error: null,
};

function setup(overrides: Partial<Parameters<typeof AnchorPopover>[0]> = {}) {
  const handlers = {
    onMoveTime: vi.fn(),
    onToggleCount: vi.fn(),
    onEdit: vi.fn(),
    onResetDefault: vi.fn(),
    onSaveDefault: vi.fn(),
    onClose: vi.fn(),
  };
  const r = render(
    <AnchorPopover
      anchor={{
        id: "Foods Breakfast",
        name: "Foods Breakfast",
        start: "08:30",
        end: "13:00",
        blocks: 2,
        counted: true,
        timeState: clean,
      }}
      {...handlers}
      {...overrides}
    />,
  );
  return { r, ...handlers };
}

describe("AnchorPopover content", () => {
  it("shows the name and block count under the selected block, as a labelled dialog", () => {
    const { r } = setup();
    const dialog = r.getByRole("dialog", { name: "Foods Breakfast" });
    expect(dialog).toBeTruthy();
    expect(r.getByText("Foods Breakfast")).toBeTruthy();
    expect(r.getByText("2 blocks")).toBeTruthy();
  });

  it("edits today's start through the move handler once for a valid time, and shows the read-only window", () => {
    const { r, onMoveTime } = setup();
    const starts = r.getByLabelText("Starts") as HTMLInputElement;
    expect(starts.value).toBe("08:30");
    expect(starts.getAttribute("step")).toBe("900");
    fireEvent.input(starts, { target: { value: "09:00" } });
    fireEvent.change(starts, { target: { value: "09:00" } });
    fireEvent.blur(starts, { target: { value: "09:00" } });
    expect(onMoveTime).toHaveBeenCalledTimes(1);
    expect(onMoveTime).toHaveBeenCalledWith("Foods Breakfast", "09:00");
    expect(r.getByText("Until 1 PM")).toBeTruthy();
  });

  it("never persists a cleared or partial start and shows the inline error", () => {
    const { r, onMoveTime } = setup();
    const starts = r.getByLabelText("Starts") as HTMLInputElement;
    fireEvent.input(starts, { target: { value: "" } });
    fireEvent.change(starts, { target: { value: "" } });
    fireEvent.blur(starts, { target: { value: "" } });
    expect(onMoveTime).not.toHaveBeenCalled();
    expect(starts.getAttribute("aria-invalid")).toBe("true");
    expect(r.getByRole("alert").textContent).toContain("Start time is required.");
  });

  it("flips the Count today switch through the skip-today action", () => {
    const { r, onToggleCount } = setup();
    const toggle = r.getByRole("switch", { name: "Count today" });
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(toggle);
    expect(onToggleCount).toHaveBeenCalledWith("Foods Breakfast");
  });

  it("shows Count today off for a skipped anchor", () => {
    const { r } = setup({
      anchor: {
        id: "Sudsing",
        name: "Sudsing",
        start: "17:45",
        end: "18:15",
        blocks: 1,
        counted: false,
        timeState: clean,
      },
    });
    expect(r.getByRole("switch", { name: "Count today" }).getAttribute("aria-checked")).toBe(
      "false",
    );
  });

  it("opens the existing editor from Edit items and resets today's time", () => {
    const { r, onEdit, onResetDefault } = setup();
    fireEvent.click(r.getByRole("button", { name: "Edit items" }));
    expect(onEdit).toHaveBeenCalledWith("Foods Breakfast");
    fireEvent.click(r.getByRole("button", { name: "Reset to default" }));
    expect(onResetDefault).toHaveBeenCalledWith("Foods Breakfast");
  });

  it("offers Save as default only once today's start differs from config", () => {
    const { r } = setup();
    expect(r.queryByRole("button", { name: "Save as default" })).toBeNull();

    cleanup();
    const dirty = setup({ anchor: {
      id: "Foods Breakfast",
      name: "Foods Breakfast",
      start: "09:00",
      end: "13:00",
      blocks: 2,
      counted: true,
      timeState: { ...clean, dirty: true },
    } });
    fireEvent.click(dirty.r.getByRole("button", { name: "Save as default" }));
    expect(dirty.onSaveDefault).toHaveBeenCalledWith("Foods Breakfast");
  });

  it("shows a refused config save inline and keeps the link", () => {
    const { r } = setup({
      anchor: {
        id: "Foods Breakfast",
        name: "Foods Breakfast",
        start: "09:00",
        end: "13:00",
        blocks: 2,
        counted: true,
        timeState: { ...clean, dirty: true, error: "config.json overrides anchored_blocks" },
      },
    });
    expect(r.getByRole("alert").textContent).toContain("overrides anchored_blocks");
    expect(r.getByRole("button", { name: "Save as default" })).toBeTruthy();
  });

  it("disables Save as default while saving and reads Saved once persisted", () => {
    const { r } = setup({
      anchor: {
        id: "Foods Breakfast",
        name: "Foods Breakfast",
        start: "09:00",
        end: "13:00",
        blocks: 2,
        counted: true,
        timeState: { ...clean, dirty: true, saving: true },
      },
    });
    expect(
      (r.getByRole("button", { name: /Saving/ }) as HTMLButtonElement).disabled,
    ).toBe(true);

    cleanup();
    const saved = setup({ anchor: {
      id: "Foods Breakfast",
      name: "Foods Breakfast",
      start: "09:00",
      end: "13:00",
      blocks: 2,
      counted: true,
      timeState: { ...clean, configTime: "09:00", dirty: false, saved: true },
    } });
    expect(saved.r.getByText("Saved")).toBeTruthy();
  });
});

describe("AnchorPopover placement and dismissal", () => {
  it("clamps inside the viewport using the shared placement math", () => {
    const { r } = setup();
    const dialog = r.getByRole("dialog") as HTMLElement;
    expect(dialog.style.position).toBe("fixed");
    expect(dialog.style.width).toBe("300px");
    // Zero-size rects in jsdom clamp to the 8px viewport margin.
    expect(dialog.style.top).toBe("8px");
    expect(dialog.style.left).toBe("8px");
  });

  it("takes focus so Escape closes it", () => {
    const { r, onClose } = setup();
    expect(document.activeElement).toBe(r.getByRole("dialog"));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on an outside click but not on a click inside or on a block", () => {
    const { r, onClose } = setup();
    const dialog = r.getByRole("dialog");
    fireEvent.mouseDown(dialog);
    fireEvent.mouseDown(r.getByLabelText("Starts"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("returns focus to the originating block when closed by Escape or an outside click", () => {
    const block = document.createElement("button");
    block.setAttribute("data-anchor-id", "Foods Breakfast");
    document.body.appendChild(block);
    const { r, onClose } = setup();
    expect(document.activeElement).toBe(r.getByRole("dialog"));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(block);
    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(document.activeElement).toBe(block);
  });

  it("keeps focus on the popover after Reset or Count today, which leave it open", () => {
    const block = document.createElement("button");
    block.setAttribute("data-anchor-id", "Foods Breakfast");
    document.body.appendChild(block);
    const { r, onClose, onResetDefault, onToggleCount } = setup();
    const dialog = r.getByRole("dialog");
    fireEvent.click(r.getByRole("button", { name: "Reset to default" }));
    fireEvent.click(r.getByRole("switch", { name: "Count today" }));
    expect(onResetDefault).toHaveBeenCalledWith("Foods Breakfast");
    expect(onToggleCount).toHaveBeenCalledWith("Foods Breakfast");
    expect(onClose).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(dialog);
  });
});

/* The calendar popover holds the Duration (blocks) field locally. A cleared
   field would otherwise read Number("") === 0 and silently zero-count the
   event, removing its wall; only a whole number of at least one block may be
   committed. */
describe("CalendarPopover Duration guard", () => {
  const event: CalendarPopoverEvent = {
    id: "Dentist",
    name: "Dentist",
    start: "19:00",
    calendarStart: "19:00",
    durationMin: 60,
    blocks: 2,
    counted: true,
    edited: false,
    blocksEdited: false,
    changedInCalendar: false,
  };

  function calendarSetup(overrides: Partial<Parameters<typeof CalendarPopover>[0]> = {}) {
    const handlers = {
      onMoveStart: vi.fn(),
      onSetBlocks: vi.fn(),
      onToggleCount: vi.fn(),
      onReset: vi.fn(),
      onClose: vi.fn(),
    };
    const r = render(<CalendarPopover event={event} {...handlers} {...overrides} />);
    return { r, ...handlers };
  }

  const duration = (r: ReturnType<typeof calendarSetup>["r"]) =>
    r.getByLabelText("Duration") as HTMLInputElement;

  it("keeps the previous duration and saves nothing when the field is cleared", () => {
    const { r, onSetBlocks } = calendarSetup();
    const input = duration(r);
    fireEvent.input(input, { target: { value: "" } });
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);
    expect(onSetBlocks).not.toHaveBeenCalled();
    expect(input.value).toBe("2");
  });

  it("refuses a zero duration and keeps the previous value", () => {
    const { r, onSetBlocks } = calendarSetup();
    const input = duration(r);
    fireEvent.input(input, { target: { value: "0" } });
    fireEvent.change(input, { target: { value: "0" } });
    fireEvent.blur(input);
    expect(onSetBlocks).not.toHaveBeenCalled();
    expect(input.value).toBe("2");
  });

  it("refuses a non-integer duration and keeps the previous value", () => {
    const { r, onSetBlocks } = calendarSetup();
    const input = duration(r);
    fireEvent.input(input, { target: { value: "2.5" } });
    fireEvent.change(input, { target: { value: "2.5" } });
    fireEvent.blur(input);
    expect(onSetBlocks).not.toHaveBeenCalled();
    expect(input.value).toBe("2");
  });

  it("commits a whole number of at least one block", () => {
    const { r, onSetBlocks } = calendarSetup();
    const input = duration(r);
    fireEvent.change(input, { target: { value: "3" } });
    expect(onSetBlocks).toHaveBeenCalledWith("Dentist", 3);
  });
});
