/* settings-hub.test.tsx — the Settings hub body: a status line, a grid of five
   plain-language entries, and the one open entry's panel beneath the grid
   (buttons with aria-expanded/aria-controls). Default entry is "Is my data up
   to date?"; every wrapped component mounts at most once; pre-hub panel ids
   still arrive at the right entry. Dialog chrome and focus are covered in
   settings-shell.test.tsx. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { SettingsShell } from "./SettingsShell";
import { SettingsUrlSync } from "./settingsUrl";
import { HUB_ENTRIES } from "./SettingsHub";
import { makeHarness } from "./test-harness";
import type { SettingsPanel } from "../store/store";
import type { CapacitiesSettings } from "../model/types";

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

const LABELS = [
  "Is my data up to date?",
  "What shows up in my plan?",
  "Why is something missing?",
  "Change my day setup",
  "Repair connections",
];

function open(panel: SettingsPanel | string | null) {
  const h = makeHarness("ready");
  h.store.dispatch({
    type: "UI",
    patch: { settingsPanel: panel as SettingsPanel | null },
  });
  return { h, rendered: h.ui(<SettingsShell />) };
}

const toggle = (r: ReturnType<typeof open>["rendered"], label: string) =>
  r.getByRole("button", { name: new RegExp(label.replace(/[?]/g, "\\?"), "i") });

const count = (r: ReturnType<typeof open>["rendered"], heading: string) =>
  r.queryAllByRole("heading", { name: heading, hidden: true }).length;

describe("SettingsHub", () => {
  it("defaults to the freshness entry", () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "freshness" } });
    const rendered = h.ui(<SettingsShell />);
    expect(toggle(rendered, "Is my data up to date").getAttribute("aria-expanded")).toBe("true");
    expect(count(rendered, "Capacities refresh")).toBe(1);
    expect(count(rendered, "Items that need a look")).toBe(1);
  });

  it("lists exactly the five plain-language entries, in order", () => {
    const { rendered } = open("freshness");
    const labels = Array.from(
      rendered.container.querySelectorAll(".settings-hub__toggle b"),
    ).map((el) => el.textContent);
    expect(labels).toEqual(LABELS);
    expect(HUB_ENTRIES.map((e) => e.label)).toEqual(LABELS);
  });

  it("toggles aria-expanded: one open at a time, pressing the open entry folds it", () => {
    const { h, rendered } = open("freshness");
    const expanded = () =>
      Array.from(rendered.container.querySelectorAll<HTMLElement>(".settings-hub__toggle"))
        .filter((el) => el.getAttribute("aria-expanded") === "true")
        .map((el) => el.getAttribute("data-entry"));
    expect(expanded()).toEqual(["freshness"]);

    fireEvent.click(toggle(rendered, "Why is something missing"));
    expect(expanded()).toEqual(["missing"]);
    expect(h.store.getState().ui.settingsPanel).toBe("missing");
    expect(rendered.getByText("Check these in order")).toBeTruthy();

    fireEvent.click(toggle(rendered, "Why is something missing"));
    expect(expanded()).toEqual([]);
    expect(h.store.getState().ui.settingsPanel).toBe("missing");

    fireEvent.click(toggle(rendered, "Why is something missing"));
    expect(expanded()).toEqual(["missing"]);
  });

  it("mounts each wrapped component exactly once, across every entry", async () => {
    const { rendered } = open("freshness");
    // Heading that proves each entry's wrapped component is present while open.
    const mounted: Record<string, string[]> = {
      "Is my data up to date?": ["Capacities refresh", "Items that need a look"],
      "What shows up in my plan?": ["Excluded tags", "Fine-tune by item type"],
      "Change my day setup": ["Frame"],
      "Repair connections": ["Automatic task rules"],
    };
    for (const label of LABELS) {
      const t = toggle(rendered, label);
      if (t.getAttribute("aria-expanded") !== "true") fireEvent.click(t);
      if (label === "What shows up in my plan?") {
        fireEvent.click(rendered.getByRole("button", { name: /fine-tune by item type/i }));
      }
      if (label === "Repair connections") {
        fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
        fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
      }
      await waitFor(() => expect(t.getAttribute("aria-expanded")).toBe("true"));
      for (const heading of mounted[label] ?? []) {
        await waitFor(() => expect(count(rendered, heading), heading).toBe(1));
      }
    }
    // The rules editor and refresh/unknown sections never appear twice, even
    // with the technical details (which host the other policy sections) open.
    for (const id of ["capacities-sec-refresh", "capacities-sec-unknown", "capacities-sec-rules"]) {
      expect(rendered.container.querySelectorAll(`#${id}`).length, id).toBeLessThanOrEqual(1);
    }
  });

  it("keeps all five entries in one grid above the open panel, in a fixed order", () => {
    const { rendered } = open("freshness");
    const hub = rendered.container.querySelector(".settings-hub") as HTMLElement;
    const skeleton = () =>
      Array.from(hub.children).map((el) => el.className.split(" ")[0]);
    // One summary line, one entry grid, then the five panels below it: no
    // entry can push another out of view while one is open.
    expect(skeleton()).toEqual([
      "settings-hub__summary",
      "settings-hub__entries",
      "settings-hub__panel",
      "settings-hub__panel",
      "settings-hub__panel",
      "settings-hub__panel",
      "settings-hub__panel",
    ]);
    const order = () =>
      Array.from(
        rendered.container.querySelectorAll<HTMLElement>(
          ".settings-hub__entry .settings-hub__toggle",
        ),
      ).map((el) => el.getAttribute("data-entry"));
    const ALL = ["freshness", "plan", "missing", "day", "repair"];
    expect(order()).toEqual(ALL);
    for (const label of LABELS) {
      const t = toggle(rendered, label);
      if (t.getAttribute("aria-expanded") !== "true") fireEvent.click(t);
      expect(order(), label).toEqual(ALL);
      expect(
        Array.from(rendered.container.querySelectorAll(".settings-hub__entry")).map((el) =>
          el.querySelector("button")?.getAttribute("aria-expanded"),
        ),
        label,
      ).toContain("true");
      // The open panel is a sibling after the grid, never inside an entry.
      const shown = rendered.container.querySelector(".settings-hub__panel:not([hidden])")!;
      expect(shown.closest(".settings-hub__entry")).toBeNull();
      expect(skeleton()[1]).toBe("settings-hub__entries");
    }
  });

  it("renders the plain-language heading of every panel the copy pass renamed", async () => {
    const { rendered } = open("freshness");
    expect(rendered.getByRole("heading", { name: "Capacities refresh" })).toBeTruthy();
    expect(rendered.getByRole("heading", { name: "Items that need a look" })).toBeTruthy();

    fireEvent.click(toggle(rendered, "What shows up in my plan"));
    await waitFor(() =>
      expect(rendered.getByRole("heading", { name: "Excluded tags" })).toBeTruthy(),
    );
    fireEvent.click(rendered.getByRole("button", { name: /fine-tune by item type/i }));
    await waitFor(() =>
      expect(
        rendered.getByRole("heading", { name: "Fine-tune by item type" }),
      ).toBeTruthy(),
    );

    fireEvent.click(toggle(rendered, "Repair connections"));
    expect(rendered.getByRole("heading", { name: "Repair connections" })).toBeTruthy();
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    for (const heading of [
      "Automatic task rules",
      "What counts as active",
      "“Assigned to me” settings",
      "Known Capacities items",
      "Bring in Active items",
      "Capacities setup (technical details)",
    ]) {
      await waitFor(
        () => expect(rendered.getByRole("heading", { name: heading }), heading).toBeTruthy(),
      );
    }
  });

  it("lands focus on the visible Repair connections heading for ?settings=capacities", async () => {
    window.history.replaceState(null, "", "/?settings=capacities");
    const h = makeHarness("ready");
    const rendered = h.ui(
      <>
        <SettingsUrlSync />
        <SettingsShell />
      </>,
    );
    await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBe("repair"));
    const heading = await waitFor(() =>
      rendered.getByRole("heading", { name: "Repair connections" }),
    );
    // Visible: the legacy id reaches the repair entry, not a hidden panel.
    expect(heading.closest("[hidden]")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });

  it("keeps the folds closed by default and the technical details behind a second click", async () => {
    const { rendered } = open("freshness");
    const fold = rendered.getByRole("button", { name: /todoist and calendar/i });
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(toggle(rendered, "What shows up in my plan"));
    const fine = rendered.getByRole("button", { name: /fine-tune by item type/i });
    expect(fine.getAttribute("aria-expanded")).toBe("false");
    expect(count(rendered, "Fine-tune by item type")).toBe(0);

    fireEvent.click(toggle(rendered, "Repair connections"));
    const tools = rendered.getByRole("button", { name: /repair tools/i });
    expect(tools.getAttribute("aria-expanded")).toBe("false");
    expect(rendered.queryByRole("button", { name: /show technical details/i })).toBeNull();
    expect(count(rendered, "Automatic task rules")).toBe(0);

    fireEvent.click(tools);
    expect(count(rendered, "Automatic task rules")).toBe(0);
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() => expect(count(rendered, "Automatic task rules")).toBe(1));
  });

  it("maps pre-hub panel ids to their entries", () => {
    const expected: Array<[string, string]> = [
      ["capacities", "repair"],
      ["tags", "plan"],
      ["day", "day"],
    ];
    for (const [legacy, entryId] of expected) {
      const { h, rendered } = open(legacy);
      expect(h.store.getState().ui.settingsPanel, legacy).toBe(entryId);
      const t = rendered.container.querySelector(`[data-entry="${entryId}"]`) as HTMLElement;
      expect(t.getAttribute("aria-expanded"), legacy).toBe("true");
      cleanup();
    }
  });

  it("maps ?settings= URLs through to the right entry and still lands on a section", async () => {
    for (const [query, entryId] of [
      ["?settings=capacities", "repair"],
      ["?settings=tags", "plan"],
      ["?settings=day&section=captures", "day"],
    ] as const) {
      window.history.replaceState(null, "", `/${query}`);
      const h = makeHarness("ready");
      const rendered = h.ui(
        <>
          <SettingsUrlSync />
          <SettingsShell />
        </>,
      );
      await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBe(entryId));
      const t = rendered.container.querySelector(`[data-entry="${entryId}"]`) as HTMLElement;
      expect(t.getAttribute("aria-expanded"), query).toBe("true");
      if (entryId === "day") {
        const captures = await waitFor(() =>
          rendered.getByRole("heading", { name: "Captures" }),
        );
        await waitFor(() => expect(document.activeElement).toBe(captures));
      }
      cleanup();
    }
  });

  it("keeps a Day setup draft when switching entries and back", () => {
    const { rendered } = open("day");
    const allot = () => rendered.container.querySelector("#setup-work-allotment") as HTMLInputElement;
    fireEvent.input(allot(), { target: { value: "90" } });
    const typed = allot().value;
    fireEvent.click(toggle(rendered, "What shows up in my plan"));
    fireEvent.click(toggle(rendered, "Change my day setup"));
    expect(allot().value).toBe(typed);
  });

  it("keeps a Technical-details draft when switching entries and back", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
      version: 1,
      revision: 0,
      persisted: true,
      nativeTaskAuto: {
        activeEnabled: true,
        dueEnabled: true,
        deadlineEnabled: true,
        deadlineHorizonDays: 2,
      },
      excluded: [],
      activeStructures: [],
      nativeTaskStructures: ["RootTask", "Task"],
      activeStatuses: ["active"],
      assignedStructures: {},
      assignedOnStructures: {},
      availableStructures: [],
      structureTitles: {},
    } satisfies CapacitiesSettings);
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "repair" } });
    const rendered = h.ui(<SettingsShell />);
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    const horizon = await waitFor(
      () => rendered.getByLabelText("Deadline coming up") as HTMLInputElement,
    );
    fireEvent.input(horizon, { target: { value: "6" } });
    fireEvent.click(toggle(rendered, "Change my day setup"));
    fireEvent.click(toggle(rendered, "Repair connections"));
    expect((rendered.getByLabelText("Deadline coming up") as HTMLInputElement).value).toBe("6");
  });

  it("closes on Escape", () => {
    const { h, rendered } = open("freshness");
    fireEvent.keyDown(rendered.getByRole("dialog", { name: "Settings" }), { key: "Escape" });
    expect(h.store.getState().ui.settingsPanel).toBeNull();
    expect(rendered.container.querySelector('[role="dialog"]')).toBeNull();
  });
});
