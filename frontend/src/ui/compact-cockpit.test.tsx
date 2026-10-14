import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { App } from "./App";
import { ActionDock } from "./ActionDock";
import { ApprovalDrawer } from "./ApprovalDrawer";
import { makeHarness } from "./test-harness";
import { sourceHealthOf } from "../adapters/wire";

describe("compact planning cockpit", () => {
  it("keeps the assigned work list as the primary surface with a local capacity readout", () => {
    const h = makeHarness("ready");
    const r = h.ui(<App />);

    expect(r.getByRole("region", { name: "Assigned this morning" })).toBeTruthy();
    expect(r.getByText("Edits here affect today only. Mark complete writes back to the source.")).toBeTruthy();
    expect(r.container.querySelector(".cap-card")).toBeTruthy();
    expect(r.container.querySelector('[aria-label="Agenda"]')).toBeNull();
    expect(r.container.querySelector(".placement")).toBeNull();
  });

  it("renders calendar evidence inline inside the work surface with visible rows (S5)", () => {
    const h = makeHarness("ready");
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;
    const calendar = queue.querySelector(".already-placed")!;

    // Containment: the compact sibling mount is replaced — the day timeline is
    // a card of the work surface, never a separate section beside it.
    expect(calendar).toBeTruthy();
    expect(queue.contains(calendar)).toBe(true);
    expect(r.container.querySelector(".cockpit__main > .calendar-impact")).toBeNull();

    // Visible blocks: the read-only DayStrip renders directly with both in-frame
    // calendar rows placed.
    expect(calendar.querySelector(".day-strip")).toBeTruthy();
    expect(calendar.querySelectorAll(".strip-cal").length).toBe(2);
    expect(r.getByText(/Trinoor Standup/)).toBeTruthy();
    expect(r.getByText(/PHEP sync \(Vlad\)/)).toBeTruthy();
  });

  it("renders the read-only timeline expanded by default and never writes on selection (S5 round 2)", () => {
    const h = makeHarness("ready");
    const save = vi.spyOn(h.controller, "saveAnchoredOverride").mockResolvedValue();
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;
    const calendar = queue.querySelector(".already-placed")!;

    // The retired band disclosure is gone; the DayStrip renders its rows
    // directly, expanded by default.
    expect(calendar.querySelector(".band")).toBeNull();
    expect(calendar.querySelector(".day-strip")).toBeTruthy();
    expect(calendar.querySelectorAll(".strip-cal").length).toBe(2);

    // The Plan-screen timeline is read-only: blocks are inert spans, not
    // buttons, so selecting one can never write accounting state.
    expect(calendar.querySelector("button")).toBeNull();
    fireEvent.click(calendar.querySelector(".strip-cal") as Element);
    fireEvent.click(calendar.querySelector(".strip-anchor") as Element);
    expect(save).not.toHaveBeenCalled();
  });

  it("leads the priority-band stack with the calendar timeline, before the urgency bands (S5 round 2)", () => {
    const h = makeHarness("ready");
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;
    const calendar = queue.querySelector(".already-placed")!;
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
    const labels = (
      Array.from(
        queue.querySelectorAll(".day-strip__lane--calendar .strip-cal__label"),
      ) as HTMLElement[]
    ).map((el) => el.textContent ?? "");

    expect(labels.length).toBe(2);
    expect(labels[0]).toContain("Earlier sync");
    expect(labels[1]).toContain("Later sync");
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
    const calendar = queue.querySelector(".already-placed")!;

    // The card frame stays in the work surface; an ignored-only day draws no
    // calendar lane and no empty list.
    expect(calendar.querySelector(".day-strip")).toBeTruthy();
    expect(calendar.querySelector(".day-strip__lane--calendar")).toBeNull();
    expect(calendar.querySelectorAll(".strip-cal").length).toBe(0);
    expect(calendar.querySelector(".calendar-impact__list")).toBeNull();
  });

  it("keeps a calendar-free frame as the same Already placed card, not a stray band", () => {
    const h = makeHarness("ready", (scenario) => {
      scenario.inputs.anchored = [];
    });
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;

    // Planning 2a: the timeline card is always on the Plan screen; a frame
    // with no calendar rows simply draws no calendar lane. The retired
    // CalendarImpact band never comes back beside the work surface.
    const calendar = queue.querySelector(".already-placed") as HTMLElement;
    expect(calendar).toBeTruthy();
    expect(calendar.querySelector(".day-strip")).toBeTruthy();
    expect(calendar.querySelector(".day-strip__lane--calendar")).toBeNull();
    expect(queue.querySelector(".calendar-impact")).toBeNull();
    expect(r.container.querySelector(".cockpit__main > .calendar-impact")).toBeNull();
  });

  it("keeps inline calendar evidence in the empty-assigned branch", () => {
    const h = makeHarness("ready", (scenario) => {
      scenario.inputs.assigned = [];
    });
    const r = h.ui(<App />);
    const queue = r.container.querySelector(".queue")!;

    expect(queue.querySelector(".queue__quiet")).toBeTruthy();
    const calendar = queue.querySelector(".already-placed")!;
    expect(queue.contains(calendar)).toBe(true);
    expect(calendar.querySelectorAll(".strip-cal").length).toBe(2);
  });

  it("puts the committed execution view before the planning surface", () => {
    const h = makeHarness("verified");
    const r = h.ui(<App />);
    const main = r.container.querySelector(".cockpit__main")!;
    const execution = main.querySelector(".execution")!;
    const queue = main.querySelector(".queue")!;
    const calendar = queue.querySelector(".already-placed")!;

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
    // The source-health alert stays truthful — it does not advertise the
    // Plan-only copy fallback.
    expect(r.getByRole("alert").textContent).not.toMatch(/copy prompt|fallback/i);
    // R20b: the Plan-only copy fallback is restored while degraded; the note
    // keeps it distinct from the opt-in prompt-task export (Commit-only).
    expect(r.getByRole("button", { name: "Copy plan prompt for an external LLM" })).toBeTruthy();
    expect(r.getByText(/Commit-only/)).toBeTruthy();
  });

  it("does not block sequencing when the only warnings are skipped Capacities objects", () => {
    const h = makeHarness("ready", (scenario) => {
      scenario.inputs.sourceWarnings = [
        "skipped Capacities object bb95d80d-573c-4aff-8faa-9dccdd2617a1 — not plannable: No title",
      ];
      scenario.inputs.sourceHealth = sourceHealthOf(scenario.inputs.sourceWarnings);
    });
    const r = h.ui(<ActionDock />);
    const sequence = r
      .getByText(/^Sequence & commit \d+ blk$/)
      .closest("button") as HTMLButtonElement;

    expect(sequence.disabled).toBe(false);
    expect(r.queryByRole("alert")).toBeNull();
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
