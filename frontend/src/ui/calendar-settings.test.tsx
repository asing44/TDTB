/* calendar-settings.test.tsx — the per-calendar on/off defaults panel. Each
   switch is that calendar's default; unseen calendars are on with a "New"
   badge; own-write calendars are read-only on with a reason; a failed save
   (including a 409) keeps the draft and requires a reload. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { CalendarSettingsPanel } from "./CalendarSettingsPanel";
import { makeHarness } from "./test-harness";
import type { CalendarSettings } from "../model/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function settingsFixture(overrides: Partial<CalendarSettings> = {}): CalendarSettings {
  return {
    version: 1,
    revision: 4,
    persisted: true,
    calendars: [{ id: "cal-work", title: "Work", enabled: false }],
    inventory: [
      { id: "cal-work", title: "Work", source: "iCloud", writable: true, locked: null, effectiveEnabled: false },
      { id: "cal-new", title: "Gym", source: "iCloud", writable: true, locked: null, effectiveEnabled: true },
      { id: "cal-own", title: "TDTB Focus", source: "iCloud", writable: true, locked: "own_write", effectiveEnabled: true },
    ],
    inventoryStatus: "ok",
    warnings: [],
    ...overrides,
  };
}

function open(load: CalendarSettings) {
  const h = makeHarness("ready");
  vi.spyOn(h.controller, "loadCalendarSettings").mockResolvedValue(load);
  const rendered = h.ui(<CalendarSettingsPanel active />);
  return { h, rendered };
}

const sw = (r: ReturnType<typeof open>["rendered"], title: string) =>
  r.getByRole("switch", { name: `Include ${title} by default` }) as HTMLButtonElement;

describe("CalendarSettingsPanel", () => {
  it("shows saved, new, and locked calendars", async () => {
    const { rendered } = open(settingsFixture());
    await waitFor(() => expect(rendered.getByText("Work")).toBeTruthy());

    expect(sw(rendered, "Work").getAttribute("aria-checked")).toBe("false");
    expect(sw(rendered, "Gym").getAttribute("aria-checked")).toBe("true");
    // Only the unseen calendar is badged.
    expect(rendered.getAllByText("New")).toHaveLength(1);
    const own = sw(rendered, "TDTB Focus");
    expect(own.getAttribute("aria-checked")).toBe("true");
    expect(own.disabled).toBe(true);
    expect(rendered.getByText(/Always on — TDTB writes your plan/)).toBeTruthy();
    expect(rendered.getByText(/re-reads your sources, which can make a staged sequence stale/)).toBeTruthy();
    expect(rendered.getByText("Saved (version 4)")).toBeTruthy();
  });

  it("saves the full list with the toggled value and clears the New badge", async () => {
    const { h, rendered } = open(settingsFixture());
    const save = vi.spyOn(h.controller, "saveCalendarSettings").mockImplementation(async (draft) => ({
      settings: settingsFixture({
        revision: draft.expectedRevision + 1,
        calendars: draft.calendars,
      }),
      refreshError: null,
    }));
    await waitFor(() => expect(rendered.getByText("Gym")).toBeTruthy());

    fireEvent.click(sw(rendered, "Gym"));
    fireEvent.click(rendered.getByRole("button", { name: "Save calendars" }));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save).toHaveBeenCalledWith({
      expectedRevision: 4,
      calendars: [
        { id: "cal-work", title: "Work", enabled: false },
        { id: "cal-new", title: "Gym", enabled: false },
        { id: "cal-own", title: "TDTB Focus", enabled: true },
      ],
    });
    await waitFor(() => expect(rendered.getByText("Saved. Sources were re-read.")).toBeTruthy());
    expect(rendered.queryByText("New")).toBeNull();
    expect(sw(rendered, "Gym").getAttribute("aria-checked")).toBe("false");
  });

  it("reports a failed planning refresh after a successful save", async () => {
    const { h, rendered } = open(settingsFixture());
    vi.spyOn(h.controller, "saveCalendarSettings").mockResolvedValue({
      settings: settingsFixture({ revision: 5 }),
      refreshError: "boom",
    });
    await waitFor(() => expect(rendered.getByText("Gym")).toBeTruthy());
    fireEvent.click(rendered.getByRole("button", { name: "Save calendars" }));
    await waitFor(() =>
      expect(rendered.getByText(/Saved; planning refresh failed/)).toBeTruthy(),
    );
  });

  it("keeps the draft and requires a reload after a 409", async () => {
    const { h, rendered } = open(settingsFixture());
    const save = vi
      .spyOn(h.controller, "saveCalendarSettings")
      .mockRejectedValue(new Error("Calendar settings changed since they were read"));
    await waitFor(() => expect(rendered.getByText("Gym")).toBeTruthy());

    fireEvent.click(sw(rendered, "Gym"));
    fireEvent.click(rendered.getByRole("button", { name: "Save calendars" }));

    await waitFor(() => expect(rendered.getByRole("alert").textContent).toContain("Save failed"));
    expect(rendered.getByRole("alert").textContent).toContain("changed since they were read");
    // Draft retained; Save is blocked until reload.
    expect(sw(rendered, "Gym").getAttribute("aria-checked")).toBe("false");
    expect((rendered.getByRole("button", { name: "Save calendars" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(rendered.getByRole("button", { name: "Reload settings" }));
    await waitFor(() => expect(rendered.queryByRole("alert")).toBeNull());
    expect(sw(rendered, "Gym").getAttribute("aria-checked")).toBe("true");
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("keeps saved calendars that are not in the live inventory", async () => {
    const { h, rendered } = open(
      settingsFixture({
        calendars: [
          { id: "cal-work", title: "Work", enabled: false },
          { id: "cal-gone", title: "Old shared", enabled: false },
        ],
      }),
    );
    const save = vi.spyOn(h.controller, "saveCalendarSettings").mockResolvedValue({
      settings: settingsFixture(),
      refreshError: null,
    });
    await waitFor(() => expect(rendered.getByText("Old shared")).toBeTruthy());
    expect(rendered.getByText("Not in your calendars right now")).toBeTruthy();

    fireEvent.click(rendered.getByRole("button", { name: "Save calendars" }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls[0][0].calendars).toContainEqual({
      id: "cal-gone",
      title: "Old shared",
      enabled: false,
    });
  });

  it("shows a load failure with a reload action", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCalendarSettings").mockRejectedValueOnce(new Error("offline"));
    const rendered = h.ui(<CalendarSettingsPanel active />);
    await waitFor(() => expect(rendered.getByRole("alert").textContent).toContain("offline"));
    expect(rendered.getByRole("button", { name: "Reload settings" })).toBeTruthy();
  });

  it("explains an unavailable inventory without hiding saved choices", async () => {
    const { rendered } = open(
      settingsFixture({ inventory: [], inventoryStatus: "unavailable", warnings: ["no EventKit grant"] }),
    );
    await waitFor(() => expect(rendered.getByText(/Calendar list unavailable/)).toBeTruthy());
    expect(rendered.getByText("no EventKit grant")).toBeTruthy();
    expect(rendered.getByText("Work")).toBeTruthy();
  });
});
