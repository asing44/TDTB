/* dayFrame — the shared day frame. Setup (Set up day screen) and Plan
   (Already placed) must resolve the frame the same way; a fixture where the
   server's effectiveEod differs from the configured End field proves it. */

import { describe, expect, it } from "vitest";
import { dayFrame } from "./dayFrame";
import { FEEDBACK_TIME } from "../fixtures/feedbackScenario";

describe("dayFrame", () => {
  it("uses the configured End field so Setup and Plan agree when effectiveEod differs", () => {
    // FEEDBACK_TIME is the reported day: effectiveEod 23:00, configEod 23:45.
    expect(FEEDBACK_TIME.effectiveEod).not.toBe(FEEDBACK_TIME.configEod);
    const plan = dayFrame(FEEDBACK_TIME);
    const setup = dayFrame(FEEDBACK_TIME, { anchor: null, eod: null });
    expect(setup).toEqual(plan);
    expect(plan).toEqual({ start: "17:15", end: "23:45" });
  });

  it("lets the Set up draft End win until it is saved", () => {
    expect(dayFrame(FEEDBACK_TIME, { eod: "22:00" })).toEqual({
      start: "17:15",
      end: "22:00",
    });
  });

  it("lets the Set up draft anchor win for the frame start", () => {
    expect(dayFrame(FEEDBACK_TIME, { anchor: "07:30" }).start).toBe("07:30");
  });
});
