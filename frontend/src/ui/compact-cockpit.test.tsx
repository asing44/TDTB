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
    expect(r.container.querySelector(".cap-card")).toBeTruthy();
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

    // Visible blocks: the review disclosure is gone; the timeline renders
    // directly with both in-frame rows placed.
    expect(calendar.querySelector(".calendar-impact__review")).toBeNull();
    expect(calendar.querySelector(".timeline")).toBeTruthy();
    expect(
      queue.querySelectorAll(
        ".timeline__lane--calendar .timeline__block, .timeline__lane--work .timeline__block",
      ).length,
    ).toBe(2);
    expect(r.getByText("Trinoor Standup")).toBeTruthy();
    expect(r.getByText("PHEP sync (Vlad)")).toBeTruthy();
  });

  it("renders the inline timeline expanded by default and never writes on selection (S5 round 2)", () => {
    const h = makeHarness("ready");
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;
    const calendar = queue.querySelector(".calendar-impact")!;

    // The retired band disclosure is gone; the timeline renders its blocks
    // directly, expanded by default.
    expect(calendar.querySelector(".band")).toBeNull();
    expect(calendar.querySelector(".timeline")).toBeTruthy();
    expect(
      queue.querySelectorAll(
        ".timeline__lane--calendar .timeline__block, .timeline__lane--work .timeline__block",
      ).length,
    ).toBe(2);

    // Selecting a block is presentation-only: it opens the detail row but
    // never writes accounting state.
    fireEvent.click(r.getByRole("button", { name: /Trinoor Standup/ }));
    expect(calendar.querySelector(".timeline__detail")).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
  });

  it("leads the priority-band stack with the calendar timeline, before the urgency bands (S5 round 2)", () => {
    const h = makeHarness("ready");
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;
    const calendar = queue.querySelector(".calendar-impact")!;
    const urgencyHeaders = Array.from(
      queue.querySelectorAll(".queue__band .band"),
    ) as Element[];

    // The calendar timeline precedes every urgency band in the priority flow.
    expect(urgencyHeaders.length).toBeGreaterThan(0);
    for (const band of urgencyHeaders) {
      expect(
        calendar.compareDocumentPosition(band) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });

  it("keeps blocks in each lane in chronological order (S5)", () => {
    const h = makeHarness("ready", (scenario) => {
      scenario.inputs.anchored = [
        ...scenario.inputs.anchored.filter((a) => a.kind !== "calendar"),
        {
          id: "Later sync", name: "Later sync", kind: "calendar",
          start: "15:00", end: "15:30", durationMin: 30, overlapAllowed: false,
          on: true, skipToday: false, calendarId: "later", calendarTitle: "Personal",
          capacityClass: "fixed",
        },
        {
          id: "Earlier sync", name: "Earlier sync", kind: "calendar",
          start: "10:00", end: "10:30", durationMin: 30, overlapAllowed: false,
          on: true, skipToday: false, calendarId: "earlier", calendarTitle: "Personal",
          capacityClass: "fixed",
        },
      ];
    });
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;
    const names = (
      Array.from(
        queue.querySelectorAll(".timeline__lane--calendar .timeline__block-name"),
      ) as HTMLElement[]
    ).map((el) => el.textContent);

    expect(names).toEqual(["Earlier sync", "Later sync"]);
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
    expect(
      queue.querySelectorAll(
        ".timeline__lane--calendar .timeline__block, .timeline__lane--work .timeline__block",
      ).length,
    ).toBe(2);
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

  it("blocks sequencing and commit while sources are degraded, with no retired fallback copy", () => {
    const h = makeHarness("ready", (scenario) => {
      scenario.inputs.sourceHealth = "degraded";
    });
    const r = h.ui(<ActionDock />);
    const sequence = r.getByText(/^Sequence & commit \d+ blk$/).closest("button") as HTMLButtonElement;

    expect(sequence.disabled).toBe(true);
    expect(r.getByRole("alert").textContent).toMatch(/Sources degraded — sequencing and commit are blocked until the read is healthy\./);
    // The alert must not advertise the retired copy-prompt fallback.
    expect(r.getByRole("alert").textContent).not.toMatch(/copy prompt|fallback/i);
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
