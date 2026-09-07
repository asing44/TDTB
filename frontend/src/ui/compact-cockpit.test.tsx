import { afterEach, describe, expect, it } from "vitest";
import { cleanup } from "@testing-library/preact";

afterEach(cleanup);

import { App } from "./App";
import { ActionDock } from "./ActionDock";
import { ApprovalDrawer } from "./ApprovalDrawer";
import { CalendarImpact } from "./CalendarImpact";
import { makeHarness } from "./test-harness";

describe("compact planning cockpit", () => {
  it("keeps the assigned work list as the primary surface with a local capacity readout", () => {
    const h = makeHarness("ready");
    const r = h.ui(<App />);

    expect(r.getByRole("region", { name: "Today's work" })).toBeTruthy();
    expect(r.getByText("Assigned items only · shape today's copy here; assignment stays upstream.")).toBeTruthy();
    expect(r.container.querySelector(".allocation-meter")).toBeTruthy();
    expect(r.container.querySelector('[aria-label="Agenda"]')).toBeNull();
    expect(r.container.querySelector(".placement")).toBeNull();
  });

  it("keeps calendar evidence compact in the default shell while retaining the review disclosure", () => {
    const h = makeHarness("ready");
    const r = h.ui(<App />);
    const calendar = r.container.querySelector(".calendar-impact--compact");

    expect(calendar).toBeTruthy();
    expect(calendar?.querySelector(".calendar-impact__summary")).toBeTruthy();
    expect(calendar?.querySelector(".calendar-impact__review summary")?.textContent).toBe(
      "Review calendar impact",
    );
  });

  it("keeps ignored-only calendar evidence compact without rendering an empty list", () => {
    const h = makeHarness("ready", (scenario) => {
      scenario.inputs.anchored = [
        {
          id: "Focus timer",
          name: "Focus timer",
          kind: "calendar",
          start: "10:00",
          end: "10:30",
          durationMin: 30,
          overlapAllowed: false,
          on: true,
          skipToday: false,
          calendarId: "focus",
          calendarTitle: "Session: focus",
          capacityClass: "ignored",
        },
      ];
    });
    const r = h.ui(<CalendarImpact compact />);
    const calendar = r.container.querySelector(".calendar-impact");

    expect(calendar?.classList.contains("calendar-impact--compact")).toBe(true);
    expect(r.getByText("1 ignored calendar source excluded")).toBeTruthy();
    expect(calendar?.querySelector(".calendar-impact__list")).toBeNull();
  });

  it("keeps a truly empty calendar frame absent", () => {
    const h = makeHarness("ready", (scenario) => {
      scenario.inputs.anchored = [];
    });
    const r = h.ui(<CalendarImpact compact />);

    expect(r.container.querySelector(".calendar-impact")).toBeNull();
  });

  it("puts the committed execution view before planning evidence", () => {
    const h = makeHarness("verified");
    const r = h.ui(<App />);
    const main = r.container.querySelector(".cockpit__main")!;
    const execution = main.querySelector(".execution")!;
    const calendar = main.querySelector(".calendar-impact")!;
    const queue = main.querySelector(".queue")!;

    expect(execution.compareDocumentPosition(calendar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(calendar.compareDocumentPosition(queue) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("keeps source-degraded planning on the no-write fallback path", () => {
    const h = makeHarness("ready", (scenario) => {
      scenario.inputs.sourceHealth = "degraded";
    });
    const r = h.ui(<ActionDock />);
    const sequence = r.getByText(/^Auto sequence \d+ blk$/).closest("button") as HTMLButtonElement;

    expect(sequence.disabled).toBe(true);
    expect(r.getByRole("alert").textContent).toMatch(/Sources degraded/);
    expect(r.getByRole("button", { name: "Copy plan prompt for an external LLM" })).toBeTruthy();
  });

  it("keeps degraded source health as an approval blocker", () => {
    const h = makeHarness("commit-preview", (scenario) => {
      scenario.inputs.sourceHealth = "degraded";
    });
    const r = h.ui(<ApprovalDrawer />);
    const arm = r.getByText(/arm live commit/).closest("button") as HTMLButtonElement;

    expect(arm.disabled).toBe(true);
    expect(r.getByText(/Sources degraded — refresh before approving writes/)).toBeTruthy();
  });
});
