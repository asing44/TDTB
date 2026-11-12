/* settings-hub.test.tsx — the Settings hub body: a status line, a sticky jump
   bar and ONE scrolling page of five sections (Day, Habits, Plan contents,
   Sources, Advanced). Every section is always rendered; folds stay closed by
   default; every wrapped component mounts at most once; pre-hub panel ids
   still arrive at the right destination. Dialog chrome and focus are covered
   in settings-shell.test.tsx. */

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

const LABELS = ["Day", "Habits", "Plan contents", "Sources", "Advanced"];

function open(panel: SettingsPanel | string | null, section?: string | null) {
  const h = makeHarness("ready");
  h.store.dispatch({
    type: "UI",
    patch: {
      settingsPanel: panel as SettingsPanel | null,
      ...(section !== undefined ? { settingsSection: section } : {}),
    },
  });
  return { h, rendered: h.ui(<SettingsShell />) };
}

type R = ReturnType<typeof open>["rendered"];

const jump = (r: R, label: string) =>
  r.getByRole("button", { name: new RegExp(`^${label}`, "i") });

const count = (r: R, heading: string) =>
  r.queryAllByRole("heading", { name: heading, hidden: true }).length;

const current = (r: R) =>
  Array.from(r.container.querySelectorAll<HTMLElement>(".settings-hub__jump-btn"))
    .filter((el) => el.getAttribute("aria-current") === "location")
    .map((el) => el.getAttribute("data-entry"));

describe("SettingsHub", () => {
  it("renders every section on one page, with the freshness content in Sources", () => {
    const { rendered } = open("freshness");
    for (const t of ["Day", "Habits", "Plan contents", "Sources", "Advanced"]) {
      expect(count(rendered, t), t).toBe(1);
    }
    expect(count(rendered, "Capacities refresh")).toBe(1);
    expect(count(rendered, "Items that need a look")).toBe(1);
  });

  it("lists exactly the five jump buttons, in order", () => {
    const { rendered } = open("freshness");
    const labels = Array.from(
      rendered.container.querySelectorAll(".settings-hub__jump-btn"),
    ).map((el) => el.textContent?.trim());
    expect(labels).toEqual(LABELS);
    expect(HUB_ENTRIES.map((e) => e.label)).toEqual(LABELS);
  });

  it("marks the current destination with aria-current and records jumps in the store", () => {
    const { h, rendered } = open("freshness");
    expect(current(rendered)).toEqual(["freshness"]);

    fireEvent.click(jump(rendered, "Habits"));
    expect(current(rendered)).toEqual(["habits"]);
    expect(h.store.getState().ui.settingsPanel).toBe("day");
    expect(h.store.getState().ui.settingsSection).toBe("habits");

    fireEvent.click(jump(rendered, "Day"));
    expect(current(rendered)).toEqual(["day"]);
    expect(h.store.getState().ui.settingsSection).toBeNull();

    fireEvent.click(jump(rendered, "Advanced"));
    expect(current(rendered)).toEqual(["repair"]);
    fireEvent.click(jump(rendered, "Advanced"));
    expect(current(rendered)).toEqual(["repair"]);
  });

  it("scrolls the target section into view when a jump is pressed", () => {
    const { rendered } = open("freshness");
    const spy = vi.fn();
    const proto = window.HTMLElement.prototype as { scrollIntoView?: unknown };
    proto.scrollIntoView = spy;
    try {
      fireEvent.click(jump(rendered, "Plan contents"));
      expect(spy).toHaveBeenCalled();
      expect(spy.mock.contexts.at(-1)).toBe(
        rendered.container.querySelector("#settings-panel-plan"),
      );
    } finally {
      delete proto.scrollIntoView;
    }
  });

  it("opens the missing-items checklist inside Sources for the pre-hub `missing` panel", () => {
    const { rendered } = open("missing");
    expect(rendered.getByText("Check these in order")).toBeTruthy();
    expect(current(rendered)).toEqual(["freshness"]);
    const fold = rendered.getByRole("button", { name: /why is something missing/i });
    expect(fold.getAttribute("aria-expanded")).toBe("true");
  });

  it("mounts each wrapped component exactly once, across every section", async () => {
    const { rendered } = open("freshness");
    fireEvent.click(rendered.getByRole("button", { name: /fine-tune by item type/i }));
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    const mounted: Record<string, string[]> = {
      Sources: ["Capacities refresh", "Items that need a look"],
      "Plan contents": ["Excluded tags", "Fine-tune by item type"],
      Day: ["Frame"],
      Advanced: ["How each item type gets in"],
      Habits: [],
    };
    for (const [section, headings] of Object.entries(mounted)) {
      expect(count(rendered, section), section).toBe(1);
      for (const heading of headings) {
        await waitFor(() => expect(count(rendered, heading), heading).toBe(1));
      }
    }
    for (const id of ["capacities-sec-refresh", "capacities-sec-unknown", "capacities-sec-rules"]) {
      expect(rendered.container.querySelectorAll(`#${id}`).length, id).toBeLessThanOrEqual(1);
    }
  });

  it("keeps the page skeleton fixed: summary, jump bar, then five sections in scan order", () => {
    const { rendered } = open("freshness");
    const hub = rendered.container.querySelector(".settings-hub") as HTMLElement;
    const skeleton = () => Array.from(hub.children).map((el) => el.id || el.className.split(" ")[0]);
    const expected = [
      "settings-hub__summary",
      "settings-hub__jump",
      "settings-panel-day",
      "settings-section-habits",
      "settings-panel-plan",
      "settings-panel-freshness",
      "settings-panel-repair",
    ];
    expect(skeleton()).toEqual(expected);
    for (const label of LABELS) {
      fireEvent.click(jump(rendered, label));
      expect(skeleton(), label).toEqual(expected);
      expect(hub.querySelectorAll("[hidden].settings-hub__panel").length).toBe(0);
    }
    const order = Array.from(
      rendered.container.querySelectorAll<HTMLElement>(".settings-hub__jump-btn"),
    ).map((el) => el.getAttribute("data-entry"));
    expect(order).toEqual(["day", "habits", "plan", "freshness", "repair"]);
  });

  it("renders the plain-language heading of every panel the copy pass renamed", async () => {
    const { rendered } = open("freshness");
    expect(rendered.getByRole("heading", { name: "Capacities refresh" })).toBeTruthy();
    expect(rendered.getByRole("heading", { name: "Items that need a look" })).toBeTruthy();
    await waitFor(() =>
      expect(rendered.getByRole("heading", { name: "Excluded tags" })).toBeTruthy(),
    );
    fireEvent.click(rendered.getByRole("button", { name: /fine-tune by item type/i }));
    await waitFor(() =>
      expect(rendered.getByRole("heading", { name: "Fine-tune by item type" })).toBeTruthy(),
    );
    expect(rendered.getByRole("heading", { name: "Advanced" })).toBeTruthy();
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    for (const heading of [
      "How each item type gets in",
      "Known Capacities items",
      "Capacities setup (technical details)",
    ]) {
      await waitFor(
        () => expect(rendered.getByRole("heading", { name: heading }), heading).toBeTruthy(),
      );
    }
  });

  it("lands focus on the Advanced heading for ?settings=capacities", async () => {
    window.history.replaceState(null, "", "/?settings=capacities");
    const h = makeHarness("ready");
    const rendered = h.ui(
      <>
        <SettingsUrlSync />
        <SettingsShell />
      </>,
    );
    await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBe("repair"));
    const heading = await waitFor(() => rendered.getByRole("heading", { name: "Advanced" }));
    expect(heading.closest("[hidden]")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });

  it("lands focus on the Habits heading for ?settings=habits", async () => {
    window.history.replaceState(null, "", "/?settings=habits");
    const h = makeHarness("ready");
    const rendered = h.ui(
      <>
        <SettingsUrlSync />
        <SettingsShell />
      </>,
    );
    await waitFor(() => expect(h.store.getState().ui.settingsSection).toBe("habits"));
    expect(h.store.getState().ui.settingsPanel).toBe("day");
    const heading = rendered.getByRole("heading", { name: "Habits" });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(current(rendered)).toEqual(["habits"]);
  });

  it("keeps the folds closed by default and the technical details behind a second click", async () => {
    const { rendered } = open("freshness");
    const fold = rendered.getByRole("button", { name: /todoist and calendar/i });
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("true");

    const fine = rendered.getByRole("button", { name: /fine-tune by item type/i });
    expect(fine.getAttribute("aria-expanded")).toBe("false");
    expect(count(rendered, "Fine-tune by item type")).toBe(0);

    const tools = rendered.getByRole("button", { name: /repair tools/i });
    expect(tools.getAttribute("aria-expanded")).toBe("false");
    expect(rendered.queryByRole("button", { name: /show technical details/i })).toBeNull();
    expect(count(rendered, "How each item type gets in")).toBe(0);

    fireEvent.click(tools);
    expect(count(rendered, "How each item type gets in")).toBe(0);
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    await waitFor(() => expect(count(rendered, "How each item type gets in")).toBe(1));
  });

  it("maps pre-hub panel ids to their destinations", () => {
    const expected: Array<[string, string, string]> = [
      ["capacities", "repair", "repair"],
      ["tags", "plan", "plan"],
      ["day", "day", "day"],
    ];
    for (const [legacy, panel, entry] of expected) {
      const { h, rendered } = open(legacy);
      expect(h.store.getState().ui.settingsPanel, legacy).toBe(panel);
      expect(current(rendered), legacy).toEqual([entry]);
      cleanup();
    }
  });

  it("maps ?settings= URLs through to the right destination and still lands on a section", async () => {
    for (const [query, panel, entry] of [
      ["?settings=capacities", "repair", "repair"],
      ["?settings=tags", "plan", "plan"],
      ["?settings=day&section=captures", "day", "day"],
    ] as const) {
      window.history.replaceState(null, "", `/${query}`);
      const h = makeHarness("ready");
      const rendered = h.ui(
        <>
          <SettingsUrlSync />
          <SettingsShell />
        </>,
      );
      await waitFor(() => expect(h.store.getState().ui.settingsPanel).toBe(panel));
      expect(current(rendered), query).toEqual([entry]);
      if (entry === "day") {
        const captures = await waitFor(() =>
          rendered.getByRole("heading", { name: "Captures" }),
        );
        await waitFor(() => expect(document.activeElement).toBe(captures));
      }
      cleanup();
    }
  });

  it("keeps a Day setup draft when jumping to other sections and back", () => {
    const { rendered } = open("day");
    const allot = () => rendered.container.querySelector("#setup-work-allotment") as HTMLInputElement;
    fireEvent.input(allot(), { target: { value: "90" } });
    const typed = allot().value;
    fireEvent.click(jump(rendered, "Plan contents"));
    fireEvent.click(jump(rendered, "Day"));
    expect(allot().value).toBe(typed);
  });

  it("keeps a Technical-details draft when jumping to other sections and back", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadCapacitiesSettings").mockResolvedValue({
      version: 2,
      revision: 0,
      persisted: true,
      statuses: { open: ["active"], completed: ["Completed", "Dropped"] },
      types: {
        RootTask: { mode: "native", property: null, statuses: null },
        Task: { mode: "native", property: null, statuses: null },
      },
      native: { deadlineHorizonDays: 2 },
      excluded: [],
      availableStructures: [],
      structureTitles: {},
      declarationDiagnostics: [],
      migratedFrom: null,
    } satisfies CapacitiesSettings);
    h.store.dispatch({ type: "UI", patch: { settingsPanel: "repair" } });
    const rendered = h.ui(<SettingsShell />);
    fireEvent.click(rendered.getByRole("button", { name: /repair tools/i }));
    fireEvent.click(rendered.getByRole("button", { name: /show technical details/i }));
    const horizon = await waitFor(
      () => rendered.getByLabelText("Deadline coming up") as HTMLInputElement,
    );
    fireEvent.input(horizon, { target: { value: "6" } });
    fireEvent.click(jump(rendered, "Day"));
    fireEvent.click(jump(rendered, "Advanced"));
    expect((rendered.getByLabelText("Deadline coming up") as HTMLInputElement).value).toBe("6");
  });

  it("closes on Escape", () => {
    const { h, rendered } = open("freshness");
    fireEvent.keyDown(rendered.getByRole("dialog", { name: "Settings" }), { key: "Escape" });
    expect(h.store.getState().ui.settingsPanel).toBeNull();
    expect(rendered.container.querySelector('[role="dialog"]')).toBeNull();
  });
});
