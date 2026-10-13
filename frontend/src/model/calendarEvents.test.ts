import { describe, expect, it } from "vitest";
import { dedupeCalendarEvents, displayAnchored, isMintSessionEvent, isSessionCalendar } from "./calendarEvents";
import type { AnchoredBlock } from "./types";

const ev = (name: string, start: string, durationMin = 30, kind: AnchoredBlock["kind"] = "calendar"): AnchoredBlock => ({
  id: name, name, kind, start, end: null, durationMin,
  overlapAllowed: false, on: true, skipToday: false,
});

describe("calendar event display rules", () => {
  it.each([
    "MINT : Regular: OPPD Payment Risk",
    "🟡 MINT : Regular",
    "mint: Regular",
    "  MINT : Regular: x",
  ])("recognises %s as a Mint session", (name) => {
    expect(isMintSessionEvent(name)).toBe(true);
  });

  it.each(["Adam pickup Aldi", "Mintz review", "Peppermint tea", "Meegy cook"])(
    "keeps %s",
    (name) => {
      expect(isMintSessionEvent(name)).toBe(false);
    },
  );

  it("dedupes the same title, start and length but not different times", () => {
    const out = dedupeCalendarEvents([
      ev("OPPD Internal Team Meeting", "15:00", 60),
      ev("OPPD Internal Team Meeting", "15:00", 60),
      ev("OPPD Internal Team Meeting", "16:00", 60),
    ]);
    expect(out.map((e) => e.start)).toEqual(["15:00", "16:00"]);
  });

  // A calendar-synced event can sit on several calendars under the same
  // (title, start, end, length). The live bug: an ignored '🟡 Trinoor' copy
  // listed first won the group, so the counted work copy vanished.
  const cal = (name: string, start: string, over: Partial<AnchoredBlock>): AnchoredBlock =>
    ({ ...ev(name, start, 30), ...over });

  it("keeps the counted copy of a duplicate pair that differs by capacity class", () => {
    const rows = [
      cal("OPPD-Payment Risk Weekly Working Session", "15:00", {
        id: "OPPD (Trinoor)", calendarTitle: "🟡 Trinoor", capacityClass: "ignored",
      }),
      cal("OPPD-Payment Risk Weekly Working Session", "15:00", {
        id: "OPPD (Calendar)", calendarTitle: "Calendar", capacityClass: "work",
      }),
    ];
    const out = dedupeCalendarEvents(rows);
    expect(out).toHaveLength(1);
    expect(out[0].capacityClass).toBe("work");
    expect(out[0].calendarTitle).toBe("Calendar");
  });

  it("keeps the counted copy at the group's first position so order is stable", () => {
    const rows = [
      cal("A", "09:00", { capacityClass: "fixed" }),
      cal("OPPD", "15:00", { capacityClass: "ignored" }),
      cal("B", "16:00", { capacityClass: "fixed" }),
      cal("OPPD", "15:00", { capacityClass: "work" }),
    ];
    const out = dedupeCalendarEvents(rows);
    expect(out.map((e) => e.name)).toEqual(["A", "OPPD", "B"]);
    expect(out[1].capacityClass).toBe("work");
  });

  it("keeps the first of two counted (non-ignored) copies", () => {
    const first = cal("OPPD", "15:00", { id: "first", capacityClass: "work" });
    const second = cal("OPPD", "15:00", { id: "second", capacityClass: "fixed" });
    const out = dedupeCalendarEvents([first, second]);
    expect(out).toHaveLength(1);
    expect(out[0]).toBe(first);
  });

  it("treats a row without a capacity class as counted", () => {
    const bare = ev("OPPD", "15:00");
    const work = cal("OPPD", "15:00", { id: "work", capacityClass: "work" });
    const out = dedupeCalendarEvents([bare, work]);
    expect(out).toHaveLength(1);
    expect(out[0]).toBe(bare);
  });

  it("displayAnchored keeps the counted duplicate in the group's first position", () => {
    const rows = [
      cal("Capitals @ Hurricanes", "19:00", { start: "19:00", capacityClass: "fixed" }),
      cal("OPPD-Payment Risk Weekly Working Session", "15:00", {
        id: "OPPD (Trinoor)", calendarTitle: "🟡 Trinoor", capacityClass: "ignored",
      }),
      cal("OPPD-Payment Risk Weekly Working Session", "15:00", {
        id: "OPPD (Calendar)", calendarTitle: "Calendar", capacityClass: "work",
      }),
    ];
    const out = displayAnchored(rows, { sessionCalendars: true });
    expect(out.map((r) => r.name)).toEqual(["Capitals @ Hurricanes", "OPPD-Payment Risk Weekly Working Session"]);
    expect(out[1].capacityClass).toBe("work");
  });

  it("filters only calendar rows and leaves anchors alone", () => {
    const rows = [
      ev("Sudsing", "17:45", 30, "hard"),
      ev("🟡 MINT : Regular", "10:20"),
      ev("Pittering", "11:01"),
      ev("Pittering", "11:01"),
    ];
    expect(displayAnchored(rows).map((r) => r.name)).toEqual(["Sudsing", "Pittering"]);
  });

  it.each(["Session: focus", "🟡 Mint", "session: deep", "🟡 Mint Sessions"])(
    "treats the %s calendar as session items",
    (title) => {
      expect(isSessionCalendar(title)).toBe(true);
    },
  );

  it.each(["Calendar", "US Holidays", "🍯 A + M Busy Bees!", "Trinoor", null, undefined])(
    "keeps events from the %s calendar",
    (title) => {
      expect(isSessionCalendar(title)).toBe(false);
    },
  );

  it("hides events from session calendars (Pittering, Putz) but keeps others", () => {
    const rows = [
      { ...ev("🚶 Pittering", "09:50"), calendarTitle: "Session: focus" },
      { ...ev("☕ Putz", "11:59"), calendarTitle: "Session: focus" },
      { ...ev("Adam pickup Aldi", "13:45"), calendarTitle: "Calendar" },
    ];
    expect(displayAnchored(rows, { sessionCalendars: true }).map((r) => r.name)).toEqual(["Adam pickup Aldi"]);
    // Without the option (Plan tasks) they stay, to be counted as ignored.
    expect(displayAnchored(rows).map((r) => r.name)).toHaveLength(3);
  });
});
