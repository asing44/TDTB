import { describe, expect, it } from "vitest";
import { fingerprintFixedInputs } from "./fingerprint";
import type { FixedInputs } from "./types";

const base: FixedInputs = {
  anchoredSourceFingerprint: "raw-a",
  calendar: [
    { name: "Standup", start: "09:15", durationMin: 30 },
    { name: "PHEP sync", start: "14:45", durationMin: 45 },
  ],
  anchored: [
    { name: "Morning Routine", start: "07:45", durationMin: 80, on: true, skipToday: false },
    { name: "Sudsing", start: "17:45", durationMin: 30, on: true, skipToday: false },
  ],
};

describe("fingerprintFixedInputs", () => {
  it("is order-independent", () => {
    const shuffled: FixedInputs = {
      anchoredSourceFingerprint: "raw-a",
      calendar: [base.calendar[1], base.calendar[0]],
      anchored: [base.anchored[1], base.anchored[0]],
    };
    expect(fingerprintFixedInputs(shuffled)).toBe(fingerprintFixedInputs(base));
  });

  it("changes when a calendar event is added", () => {
    const drifted: FixedInputs = {
      ...base,
      calendar: [...base.calendar, { name: "Dentist", start: "11:00", durationMin: 45 }],
    };
    expect(fingerprintFixedInputs(drifted)).not.toBe(fingerprintFixedInputs(base));
  });

  it("changes when a fixed time moves", () => {
    const moved: FixedInputs = {
      ...base,
      calendar: [{ ...base.calendar[0], start: "10:00" }, base.calendar[1]],
    };
    expect(fingerprintFixedInputs(moved)).not.toBe(fingerprintFixedInputs(base));
  });

  it("changes when an anchored block is skipped for today", () => {
    const skipped: FixedInputs = {
      ...base,
      anchored: [{ ...base.anchored[0], skipToday: true }, base.anchored[1]],
    };
    expect(fingerprintFixedInputs(skipped)).not.toBe(fingerprintFixedInputs(base));
  });

  it("keeps raw anchored-source drift separate from effective inputs", () => {
    const rawDrifted: FixedInputs = { ...base, anchoredSourceFingerprint: "raw-b" };
    expect(fingerprintFixedInputs(rawDrifted)).toBe(fingerprintFixedInputs(base));
    expect(rawDrifted.anchoredSourceFingerprint).not.toBe(base.anchoredSourceFingerprint);
  });
});

describe("T28 calendar plan-participation bit", () => {
  it("dismissing a calendar event changes the fingerprint", () => {
    const dismissed: FixedInputs = {
      ...base,
      calendar: [{ ...base.calendar[0], attending: false }, base.calendar[1]],
    };
    expect(fingerprintFixedInputs(dismissed)).not.toBe(fingerprintFixedInputs(base));
  });

  it("explicit attending:true equals absent", () => {
    const explicit: FixedInputs = {
      ...base,
      calendar: base.calendar.map((c) => ({ ...c, attending: true })),
    };
    expect(fingerprintFixedInputs(explicit)).toBe(fingerprintFixedInputs(base));
  });
});
