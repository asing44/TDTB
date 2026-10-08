/* durationTags.test.ts — the frontend recognizer must agree with the backend
   (app/duration_tags.py). A disagreement is not cosmetic: isDurationTag feeds
   shared-work grouping in allocatorView, so a label only one side recognizes
   would group work the other side treats as unrelated metadata. */
import { describe, expect, it } from "vitest";
import { isDurationTag } from "./durationTags";

describe("duration tag recognition mirrors the backend", () => {
  it("recognizes the numeric forms", () => {
    expect(isDurationTag("dur45")).toBe(true);
    expect(isDurationTag("DUR30")).toBe(true);
    expect(isDurationTag("🚀10min")).toBe(true);
    expect(isDurationTag("🚀 10min")).toBe(true);
  });

  it("recognizes the operator's word-based labels", () => {
    expect(isDurationTag("🍅 Half-hour")).toBe(true);
    expect(isDurationTag("🍅 Half hour")).toBe(true);
    expect(isDurationTag("🏃‍♂️ Hour")).toBe(true);
    // 🐢 Multi-hour is recognized metadata carrying no minutes. Recognition is
    // what keeps it out of shared-work grouping, so it must be true even
    // though it contributes no duration.
    expect(isDurationTag("🐢 Multi-hour")).toBe(true);
  });

  it("tolerates emoji-form variance on the hour label", () => {
    // A dropped ZWJ or variation selector must not silently break matching.
    expect(isDurationTag("🏃 Hour")).toBe(true);
    expect(isDurationTag("🏃‍♂️Hour")).toBe(true);
    expect(isDurationTag("Hour")).toBe(true);
    expect(isDurationTag("  hour  ")).toBe(true);
  });

  it("never matches Multi-hour as Hour", () => {
    // Most-specific-first plus anchoring: both must resolve, neither may
    // cross-match into the other's shorter word.
    expect(isDurationTag("multi-hour")).toBe(true);
    expect(isDurationTag("MULTI-HOUR")).toBe(true);
    expect(isDurationTag("Multi-hour")).toBe(true);
  });

  it("does not match unrelated labels", () => {
    for (const label of [
      "someday",
      "hourly",
      "pre-hour",
      "24hour",
      "multi_hour",
      "Meeting",
      "",
      null,
      undefined,
    ]) {
      expect(isDurationTag(label)).toBe(false);
    }
  });
});
