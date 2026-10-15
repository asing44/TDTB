/* Set up day screen: calendar events are editable locally only. Drives the
   real screen (App) through the shared popover and the All blocks modal and
   asserts the persisted day-setup writes through the fixture adapter. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { App } from "../App";
import { FixtureAdapter } from "../../adapters/fixture";
import { makeHarness } from "../test-harness";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function screenHarness(saved?: Record<string, unknown>) {
  const h = makeHarness("ready");
  const inputs = structuredClone(h.store.getState().inputs!);
  inputs.daySetup = { ...inputs.daySetup, anchor: "07:30" };
  inputs.daySemantics = { ...inputs.daySemantics, mintSessions: [] };
  h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: h.store.getState().ledger! });
  if (saved) {
    h.store.dispatch({
      type: "SETUP_SAVED",
      daySetup: { ...h.store.getState().daySetup, anchored: saved as never },
    });
  }
  h.store.dispatch({ type: "UI", patch: { screen: "setup", settingsPanel: null } });
  return h;
}

type R = ReturnType<ReturnType<typeof makeHarness>["ui"]>;
const ID = "Trinoor Standup"; // calendar, 09:15-09:45, work class
const block = (r: R, id = ID) =>
  r.container.querySelector(`.strip-cal[data-anchor-id="${id}"]`) as HTMLElement;
const saved = (h: ReturnType<typeof screenHarness>) => h.store.getState().daySetup.anchored[ID];

function open(h: ReturnType<typeof screenHarness>) {
  const r = h.ui(<App />);
  fireEvent.click(block(r));
  return { r, dialog: r.getByRole("dialog", { name: ID }) };
}

describe("calendar popover", () => {
  it("opens the shared popover with Count today, Starts, Duration and a local-only note", () => {
    const { r, dialog } = open(screenHarness());
    expect(dialog.className).toContain("anchor-popover");
    expect((r.getByLabelText("Starts") as HTMLInputElement).value).toBe("09:15");
    expect((r.getByLabelText(/^Duration/) as HTMLInputElement).value).toBe("1");
    expect(r.getByRole("switch", { name: "Count today" }).getAttribute("aria-checked")).toBe("true");
    expect(dialog.textContent).toMatch(/Google Calendar is not changed/i);
    expect((r.getByRole("button", { name: "Reset to calendar" }) as HTMLButtonElement).disabled).toBe(true);
    expect(r.queryByText("Edit items")).toBeNull();
    expect(r.queryByText("Save as default")).toBeNull();
  });

  it("persists a Starts edit with the calendar's own start and moves the block", async () => {
    const h = screenHarness();
    const save = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    const { r } = open(h);
    const input = r.getByLabelText("Starts") as HTMLInputElement;
    fireEvent.input(input, { target: { value: "10:00" } });
    fireEvent.change(input, { target: { value: "10:00" } });
    await waitFor(() => expect(save).toHaveBeenCalled());
    const sent = save.mock.calls.at(-1)![0].anchored[ID];
    expect(sent).toMatchObject({ start: "10:00", calendarStartAtEdit: "09:15", skipToday: false, time: null });
    expect(saved(h).start).toBe("10:00");
    const span = 23 * 60 + 45 - (7 * 60 + 30);
    const pct = ((10 * 60 - (7 * 60 + 30)) / span) * 100;
    expect(parseFloat(block(r).style.left)).toBeCloseTo(pct, 1);
    expect((r.getByRole("button", { name: "Reset to calendar" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("persists a Duration (blocks) edit", async () => {
    const h = screenHarness();
    const { r } = open(h);
    const input = r.getByLabelText(/^Duration/) as HTMLInputElement;
    fireEvent.input(input, { target: { value: "3" } });
    fireEvent.change(input, { target: { value: "3" } });
    await waitFor(() => expect(saved(h)?.blocks).toBe(3));
    expect(saved(h).start ?? null).toBeNull();
  });

  it("persists nothing for a cleared or partial Starts", async () => {
    const h = screenHarness();
    const save = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    const { r } = open(h);
    const input = r.getByLabelText("Starts") as HTMLInputElement;
    fireEvent.input(input, { target: { value: "" } });
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);
    expect(r.getByRole("alert").textContent).toMatch(/Start time is required/);
    expect(save).not.toHaveBeenCalled();
  });

  it("Count today writes the skip override", async () => {
    const h = screenHarness();
    const { r } = open(h);
    fireEvent.click(r.getByRole("switch", { name: "Count today" }));
    await waitFor(() => expect(saved(h)?.skipToday).toBe(true));
  });

  it("Reset to calendar clears the override and returns the block", async () => {
    const h = screenHarness({
      [ID]: { on: true, skipToday: false, time: null, start: "10:00", calendarStartAtEdit: "09:15", blocks: 2 },
    });
    const save = vi.spyOn(FixtureAdapter.prototype, "saveDaySetup");
    const { r } = open(h);
    fireEvent.click(r.getByRole("button", { name: "Reset to calendar" }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    const sent = save.mock.calls.at(-1)![0].anchored[ID];
    expect(sent.start ?? null).toBeNull();
    expect(sent.calendarStartAtEdit ?? null).toBeNull();
    expect(sent.blocks ?? null).toBeNull();
    const span = 23 * 60 + 45 - (7 * 60 + 30);
    expect(parseFloat(block(r).style.left)).toBeCloseTo(((9 * 60 + 15 - 450) / span) * 100, 1);
  });

  it("makes no calendar write call", async () => {
    const h = screenHarness();
    const spies = [
      vi.spyOn(FixtureAdapter.prototype, "liveCommit"),
      vi.spyOn(FixtureAdapter.prototype, "shadowCommit"),
      vi.spyOn(FixtureAdapter.prototype, "saveAnchoredStart"),
    ];
    const { r } = open(h);
    const input = r.getByLabelText("Starts") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "11:00" } });
    await waitFor(() => expect(saved(h)?.start).toBe("11:00"));
    for (const s of spies) expect(s).not.toHaveBeenCalled();
  });

  it("rolls the edit back and shows the error when the save is refused", async () => {
    const h = screenHarness();
    const { r, dialog } = open(h);
    vi.spyOn(FixtureAdapter.prototype, "saveDaySetup").mockRejectedValueOnce(
      new Error("network down"),
    );
    const input = r.getByLabelText("Starts") as HTMLInputElement;
    fireEvent.input(input, { target: { value: "10:00" } });
    fireEvent.change(input, { target: { value: "10:00" } });
    await waitFor(() =>
      expect(dialog.textContent).toMatch(/Could not save today's change/),
    );
    expect(r.getByRole("alert").textContent).toMatch(/network down/);
    expect(saved(h)?.start ?? null).toBeNull();
    await waitFor(() =>
      expect((r.getByLabelText("Starts") as HTMLInputElement).value).toBe("09:15"),
    );
  });
});

describe("changed in calendar", () => {
  const edit = { on: true, skipToday: false, time: null, start: "10:00" };

  it("shows no chip while the live start still matches the start at edit", () => {
    const h = screenHarness({ [ID]: { ...edit, calendarStartAtEdit: "09:15" } });
    const { r, dialog } = open(h);
    expect(dialog.textContent).not.toMatch(/Changed in calendar/i);
    expect(r.queryByRole("button", { name: "Use calendar time" })).toBeNull();
  });

  it("shows the chip when the live start differs, keeps the edit, and clears on Use calendar time", async () => {
    const h = screenHarness({ [ID]: { ...edit, calendarStartAtEdit: "09:00" } });
    const { r, dialog } = open(h);
    expect(dialog.textContent).toMatch(/Changed in calendar/i);
    expect((r.getByLabelText("Starts") as HTMLInputElement).value).toBe("10:00");
    fireEvent.click(r.getByRole("button", { name: "Use calendar time" }));
    await waitFor(() => expect(saved(h).start ?? null).toBeNull());
    expect(saved(h).calendarStartAtEdit ?? null).toBeNull();
    expect(r.container.textContent).not.toMatch(/Changed in calendar/i);
  });
});

describe("All blocks modal", () => {
  it("shows edited calendar rows at their edited time with an edited chip and keeps the switch", async () => {
    const h = screenHarness({ [ID]: { on: true, skipToday: false, time: null, start: "10:00", calendarStartAtEdit: "09:15" } });
    const r = h.ui(<App />);
    fireEvent.click(r.getByRole("button", { name: /^All blocks/ }));
    const modal = r.getByRole("dialog", { name: "All blocks" });
    expect(modal.textContent).toContain("10:00 AM–10:30 AM");
    expect(modal.textContent).toMatch(/edited/i);
    const sw = r.getByRole("switch", { name: `Count ${ID} today` });
    fireEvent.click(sw);
    await waitFor(() => expect(saved(h).skipToday).toBe(true));
    expect(saved(h).start).toBe("10:00"); // toggling the switch keeps the edit
  });

  it("offers Use calendar time on a changed-in-calendar row", async () => {
    const h = screenHarness({ [ID]: { on: true, skipToday: false, time: null, start: "10:00", calendarStartAtEdit: "09:00" } });
    const r = h.ui(<App />);
    fireEvent.click(r.getByRole("button", { name: /^All blocks/ }));
    expect(r.getByRole("dialog", { name: "All blocks" }).textContent).toMatch(/Changed in calendar/i);
    fireEvent.click(r.getByRole("button", { name: `Use calendar time for ${ID}` }));
    await waitFor(() => expect(saved(h).start ?? null).toBeNull());
  });
});

describe("overlap warning", () => {
  it("moving onto another event is allowed and the footer counts the warning", async () => {
    const h = screenHarness();
    const { r } = open(h);
    fireEvent.change(r.getByLabelText("Starts"), { target: { value: "14:45" } }); // over PHEP sync
    await waitFor(() => expect(saved(h)?.start).toBe("14:45"));
    const pill = await r.findByRole("button", { name: /scheduling warning/ });
    fireEvent.click(pill);
    expect(r.container.textContent).toMatch(/Edited calendar event 'Trinoor Standup' \(14:45-15:15\) overlaps 'PHEP sync \(Vlad\)'/);
  });
});
