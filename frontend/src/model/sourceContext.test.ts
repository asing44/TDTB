/* T23: source chips must say what an item IS, not just where it lives. */
import { describe, expect, it } from "vitest";
import { sourceContext } from "./sourceContext";
import type { AssignedItem } from "./types";

function item(over: Partial<AssignedItem>): AssignedItem {
  return {
    id: "X", name: "X", path: null, source: "todoist", types: [],
    urgency: null, deadline: null, priorityScore: 0, blocks: 1,
    durationLabel: "30min", todoistId: null, labels: [],
    ...over,
  };
}

describe("sourceContext", () => {
  it("todoist: labels verbatim", () => {
    expect(sourceContext(item({
      source: "todoist", types: ["todoist"], labels: ["🍅30min", "errand"],
    }))).toBe("🍅30min · errand");
  });

  it("todoist with no labels stays bare", () => {
    expect(sourceContext(item({ source: "todoist", types: ["todoist"] }))).toBe("");
  });

  it("capacities: no labels, no folder noise — the KIND chip carries the type", () => {
    expect(sourceContext(item({ source: "capacities", types: ["custom-project"] }))).toBe("");
  });

  it("a row with a path but no labels never invents folder context", () => {
    expect(sourceContext(item({ path: "Some/Folder/Note.md" }))).toBe("");
  });
});
