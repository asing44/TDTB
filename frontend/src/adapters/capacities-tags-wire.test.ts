/* capacities-tags-wire.test.ts — Capacities tag chips on plan rows.

   The /plan-inputs digest already carries `capacities_tags`
   ([{space_id, tag_id, title}], plus `capacities_tags_error` when the property
   could not be read) on every Capacities row. Those titles are the only display
   metadata for a Capacities tag, so the wire boundary must turn them into the
   same `tags` field Todoist rows already use — TaskRow renders labels then
   item.tags through rowTags() as qrow__tags chips.

   Contract pinned here: an explicit `tags` field always wins unchanged, Todoist
   rows are untouched, and malformed `capacities_tags` degrade quietly (this
   wire layer never throws on bad payloads). */

import { describe, expect, it } from "vitest";
import { projectAssigned } from "./wire";
import { rowTags } from "../model/sourceContext";
import type { AssignedItem } from "../model/types";

/** A Capacities assigned row as the backend serializes it (relevant keys only). */
function capacitiesRow(over: Record<string, unknown> = {}) {
  return {
    name: "Refinish the hallway",
    path: "capacities://space-1/obj-1",
    source: "capacities",
    identity: "capacities:space-1:struct-a:obj-1",
    blocks: 2,
    ...over,
  };
}

function tagRef(title: unknown, tagId = "tag-1") {
  return { space_id: "space-1", tag_id: tagId, title };
}

/** The tags a row actually shows as chips (TaskRow uses rowTags directly). */
function chips(item: AssignedItem): string[] {
  const { visible, rest } = rowTags(item);
  return [...visible, ...rest];
}

describe("Capacities tags at the wire boundary", () => {
  it("projects capacities_tags titles as the row's tags and chips", () => {
    const item = projectAssigned(
      capacitiesRow({
        capacities_tags: [
          { space_id: "space-1", tag_id: "tag-home", title: "home" },
          { space_id: "space-1", tag_id: "tag-interior", title: "interior design" },
        ],
      }),
    );
    expect(item.tags).toEqual(["home", "interior design"]);
    // The outcome the user sees: the same chips Todoist labels render as.
    expect(chips(item)).toEqual(["home", "interior design"]);
  });

  it("keeps an explicit tags field exactly as-is, never merging capacities_tags", () => {
    const item = projectAssigned(
      capacitiesRow({
        tags: ["systems", "household"],
        capacities_tags: [tagRef("home"), tagRef("ignored", "tag-2")],
      }),
    );
    expect(item.tags).toEqual(["systems", "household"]);
  });

  it("leaves an empty explicit tags field empty instead of falling back", () => {
    // An explicit `[]` is a real answer (the row has no tags), not an absence,
    // so the Capacities titles must not leak in and reorder the row.
    const item = projectAssigned(
      capacitiesRow({ tags: [], capacities_tags: [tagRef("home")] }),
    );
    expect(item.tags).toEqual([]);
  });

  it("leaves Todoist rows unchanged", () => {
    const labeled = projectAssigned({
      name: "Review AWS module 4",
      path: "todoist://9001",
      source: "todoist",
      todoist_id: "9001",
      blocks: 1,
      labels: ["🍅 Half-hour"],
    });
    expect(labeled.labels).toEqual(["🍅 Half-hour"]);
    expect(labeled.tags).toEqual([]);
    expect(chips(labeled)).toEqual(["🍅 Half-hour"]);

    const tagged = projectAssigned({
      name: "Review AWS module 4",
      path: "todoist://9001",
      source: "todoist",
      todoist_id: "9001",
      blocks: 1,
      tags: ["Deep work"],
    });
    expect(tagged.tags).toEqual(["Deep work"]);
  });

  it("reports no tags for a Capacities row whose tags could not be read", () => {
    // `capacities_tags_error` means the property was unreadable; the row still
    // renders, it just carries no tag chips.
    const item = projectAssigned(
      capacitiesRow({
        capacities_tags: null,
        capacities_tags_error: "tags are title-only without typed identities",
      }),
    );
    expect(item.tags).toEqual([]);
    expect(item.name).toBe("Refinish the hallway");
  });
});

describe("malformed capacities_tags degrade quietly", () => {
  const malformed: Array<[string, unknown]> = [
    ["a string", "home"],
    ["an object", { title: "home" }],
    ["a number", 7],
    ["undefined", undefined],
    ["null", null],
  ];

  for (const [label, raw] of malformed) {
    it(`yields no tags when capacities_tags is ${label}`, () => {
      expect(() => projectAssigned(capacitiesRow({ capacities_tags: raw }))).not.toThrow();
      expect(projectAssigned(capacitiesRow({ capacities_tags: raw })).tags).toEqual([]);
    });
  }

  it("drops entries without a usable string title and keeps the rest", () => {
    const item = projectAssigned(
      capacitiesRow({
        capacities_tags: [
          tagRef("home", "tag-1"),
          { space_id: "space-1", tag_id: "tag-2" }, // no title key at all
          tagRef(42, "tag-3"),
          { space_id: "space-1", tag_id: "tag-4", title: null },
          "home", // not a reference at all
          null,
          undefined,
          [{ space_id: "space-1", tag_id: "tag-5", title: "nested" }],
          tagRef("interior design", "tag-6"),
        ],
      }),
    );
    expect(item.tags).toEqual(["home", "interior design"]);
  });

  it("drops empty and whitespace-only titles", () => {
    const item = projectAssigned(
      capacitiesRow({
        capacities_tags: [tagRef("", "tag-1"), tagRef("   ", "tag-2"), tagRef("\t\n", "tag-3")],
      }),
    );
    expect(item.tags).toEqual([]);
  });

  it("de-duplicates repeated titles and trims them for display", () => {
    const item = projectAssigned(
      capacitiesRow({
        capacities_tags: [
          tagRef("home", "tag-1"),
          tagRef("home", "tag-2"),
          tagRef(" home ", "tag-3"),
          tagRef("interior design", "tag-4"),
        ],
      }),
    );
    expect(item.tags).toEqual(["home", "interior design"]);
  });

  it("never throws on a payload with no capacities_tags key at all", () => {
    expect(() => projectAssigned(capacitiesRow())).not.toThrow();
    expect(projectAssigned(capacitiesRow()).tags).toEqual([]);
  });
});
