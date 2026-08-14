/* T23: source chips must say what an item IS, not just where it lives. */
import { describe, expect, it } from "vitest";
import { sourceContext, sourceDetail, typeToken } from "./sourceContext";
import type { AssignedItem } from "./types";

function item(over: Partial<AssignedItem>): AssignedItem {
  return {
    id: "X", name: "X", path: null, source: "vault", types: [],
    urgency: null, deadline: null, priorityScore: 0, blocks: 1,
    durationLabel: "30min", todoistId: null, labels: [],
    ...over,
  };
}

describe("sourceContext", () => {
  it("vault interval: type + folder", () => {
    expect(sourceContext(item({
      source: "vault", types: ["press"],
      path: "50 - Operations/Intervals/Press.md",
    }))).toBe("press · Intervals");
  });

  it("vault task: folder dropped when it just restates the type", () => {
    expect(sourceContext(item({
      source: "vault", types: ["task"],
      path: "50 - Operations/Tasks/Fix thing.md",
    }))).toBe("task");
  });

  it("vault row with no path still shows its type", () => {
    expect(sourceContext(item({ source: "vault", types: ["project"] }))).toBe("project");
  });

  it("todoist: labels verbatim", () => {
    expect(sourceContext(item({
      source: "todoist", types: ["todoist"], labels: ["🍅30min", "errand"],
    }))).toBe("🍅30min · errand");
  });

  it("todoist with no labels stays bare", () => {
    expect(sourceContext(item({ source: "todoist", types: ["todoist"] }))).toBe("");
  });
});

/* 2026-07-27: the type became a chip, so it has to be separable from the rest
   of the source line without the two disagreeing. */
describe("typeToken / sourceDetail", () => {
  it("vault: the type leads and the folder is what's left", () => {
    const i = item({
      source: "vault", types: ["press"],
      path: "50 - Operations/Intervals/Press.md",
    });
    expect(typeToken(i)).toBe("press");
    expect(sourceDetail(i)).toBe("Intervals");
    // The two together still reconstruct the flat form the queue shows.
    expect(`${typeToken(i)} · ${sourceDetail(i)}`).toBe(sourceContext(i));
  });

  it("vault with no folder signal: chip only, nothing left over", () => {
    const i = item({ source: "vault", types: ["project"] });
    expect(typeToken(i)).toBe("project");
    expect(sourceDetail(i)).toBe("");
  });

  it("vault with no type at all: no chip, detail unchanged", () => {
    const i = item({ source: "vault", types: [], path: "50 - Operations/Projects/X.md" });
    expect(typeToken(i)).toBeNull();
    expect(sourceDetail(i)).toBe(sourceContext(i));
  });

  it("todoist: reports task — its `types` is the provenance marker, not a kind", () => {
    const i = item({ source: "todoist", types: ["todoist"], labels: ["errand"] });
    expect(typeToken(i)).toBe("task");
    // Labels are detail, not kind — the chip must not swallow them.
    expect(sourceDetail(i)).toBe("errand");
  });
});
