import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { App } from "./App";
import { ActionDock } from "./ActionDock";
import { ApprovalDrawer } from "./ApprovalDrawer";
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

  it("renders calendar evidence inline inside the work surface with visible rows (S5)", () => {
    const h = makeHarness("ready");
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;
    const calendar = queue.querySelector(".calendar-impact")!;

    // Containment: the compact sibling mount is replaced — calendar evidence
    // is a band of the work surface, never a separate section beside it.
    expect(calendar).toBeTruthy();
    expect(r.container.querySelector(".cockpit__main > .calendar-impact")).toBeNull();
    expect(calendar.classList.contains("calendar-impact--inline")).toBe(true);

    // Visible rows: the review disclosure is gone; the list renders directly.
    expect(calendar.querySelector(".calendar-impact__review")).toBeNull();
    expect(calendar.querySelector(".calendar-impact__list")).toBeTruthy();
    expect(queue.querySelectorAll(".calendar-impact__row").length).toBe(2);
    expect(r.getByText("Trinoor Standup")).toBeTruthy();
    expect(r.getByText("PHEP sync (Vlad)")).toBeTruthy();
  });

  it("presents the inline calendar as a collapsible band, expanded by default (S5 round 2)", () => {
    const h = makeHarness("ready");
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;
    const calendar = queue.querySelector(".calendar-impact")!;
    const header = calendar.querySelector(".band") as HTMLButtonElement;

    // Expanded by default: the header announces the state and the rows render.
    expect(header).toBeTruthy();
    expect(header.getAttribute("aria-expanded")).toBe("true");
    expect(header.getAttribute("aria-label")).toBe(
      "Band Calendar impact, expanded, 2 rows, activate to collapse",
    );
    expect(calendar.querySelector(".calendar-impact__list")).toBeTruthy();
    expect(queue.querySelectorAll(".calendar-impact__row").length).toBe(2);

    // Collapsing hides the rows and announces the hidden count...
    fireEvent.click(header);
    expect(header.getAttribute("aria-expanded")).toBe("false");
    expect(header.getAttribute("aria-label")).toBe(
      "Band Calendar impact, collapsed, 2 rows hidden, activate to expand",
    );
    expect(header.textContent).toMatch(/collapsed/i);
    expect(header.textContent).toMatch(/hidden/i);
    expect(calendar.querySelector(".calendar-impact__list")).toBeNull();
    expect(queue.querySelectorAll(".calendar-impact__row").length).toBe(0);

    // ...and a disclosure-only collapse never writes accounting state.
    expect(save).not.toHaveBeenCalled();

    // Expanding restores the rows and the accessible state.
    fireEvent.click(header);
    expect(header.getAttribute("aria-expanded")).toBe("true");
    expect(queue.querySelectorAll(".calendar-impact__row").length).toBe(2);
    expect(save).not.toHaveBeenCalled();
  });

  it("leads the priority-band stack with the calendar band, before the urgency bands (S5 round 2)", () => {
    const h = makeHarness("ready");
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;
    const calendarHeader = queue.querySelector(".calendar-impact .band")!;
    const urgencyHeaders = Array.from(
      queue.querySelectorAll(".queue__band .band"),
    ) as Element[];

    // The calendar band is the first band in the priority flow, and every
    // urgency band follows it in BANDS order.
    expect(urgencyHeaders.length).toBeGreaterThan(0);
    for (const band of urgencyHeaders) {
      expect(
        calendarHeader.compareDocumentPosition(band) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });

  it("keeps the inline calendar band in chronological order (S5)", () => {
    const h = makeHarness("ready", (scenario) => {
      scenario.inputs.anchored = [...scenario.inputs.anchored].reverse();
    });
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;
    const names = (
      Array.from(
        queue.querySelectorAll(".calendar-impact__row .calendar-impact__event strong"),
      ) as HTMLElement[]
    ).map((el) => el.textContent);

    expect(names).toEqual(["Trinoor Standup", "PHEP sync (Vlad)"]);
  });

  it("keeps ignored-only calendar evidence inline without rendering an empty list", () => {
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
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;
    const calendar = queue.querySelector(".calendar-impact")!;

    expect(calendar.classList.contains("calendar-impact--inline")).toBe(true);
    expect(r.getByText("1 ignored calendar source excluded")).toBeTruthy();
    expect(calendar.querySelector(".calendar-impact__list")).toBeNull();
  });

  it("keeps a truly empty calendar frame absent from the work surface", () => {
    const h = makeHarness("ready", (scenario) => {
      scenario.inputs.anchored = [];
    });
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;

    expect(queue.querySelector(".calendar-impact")).toBeNull();
  });

  it("keeps inline calendar evidence in the empty-assigned branch", () => {
    const h = makeHarness("ready", (scenario) => {
      scenario.inputs.assigned = [];
    });
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;

    expect(queue.querySelector(".center-note")).toBeTruthy();
    const calendar = queue.querySelector(".calendar-impact")!;
    expect(calendar.classList.contains("calendar-impact--inline")).toBe(true);
    expect(queue.querySelectorAll(".calendar-impact__row").length).toBe(2);
  });

  it("puts the committed execution view before the planning surface", () => {
    const h = makeHarness("verified");
    const r = h.ui(<App />);
    const main = r.container.querySelector(".cockpit__main")!;
    const execution = main.querySelector(".execution")!;
    const queue = main.querySelector(".queue")!;
    const calendar = queue.querySelector(".calendar-impact")!;

    // Committed execution stays ahead of every planning surface; the calendar
    // band now lives inside the work surface it precedes.
    expect(execution.compareDocumentPosition(queue) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(execution.compareDocumentPosition(calendar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(queue.contains(calendar)).toBe(true);
  });

  it("keeps source-degraded planning on the no-write fallback path", () => {
    const h = makeHarness("ready", (scenario) => {
      scenario.inputs.sourceHealth = "degraded";
    });
    const r = h.ui(<ActionDock />);
    const sequence = r.getByText(/^Auto sequence \d+ blk$/).closest("button") as HTMLButtonElement;

    expect(sequence.disabled).toBe(true);
    expect(r.getByRole("alert").textContent).toMatch(/Sources degraded/);
    // U5: the manual copy-prompt bypass is retired; the dock states that
    // prompts stay local and export only on an opted-in Commit.
    expect(r.queryByRole("button", { name: "Copy plan prompt for an external LLM" })).toBeNull();
    expect(r.getByText(/export on Commit only/)).toBeTruthy();
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
