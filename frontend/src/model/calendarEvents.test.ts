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
