/* drawerGroups.test.ts — U6: the Not in plan drawer groups its LOCAL rows by
   commitment first, then by source / object type.

   The tier ladder is the whole contract, so every rung and every reversal is
   pinned here: overdue beats due today beats a fixed time beats the next seven
   days beats a p1/p2 flag beats no date at all. Pure selector: no render, no
   adapter, no store. */

import { describe, expect, it } from "vitest";

import {
  DRAWER_TIERS,
  drawerTierGroups,
  drawerTierOf,
  hasFixedTime,
  type DrawerFacts,
} from "./drawerGroups";
import type { AssignmentRow } from "../store/store";

const TODAY = "2026-07-18";

function facts(over: Partial<DrawerFacts> = {}): DrawerFacts {
  return {
    source: "todoist",
    deadline: null,
    urgency: null,
    scheduledStart: null,
    kind: null,
    ...over,
  };
}

function row(name: string, over: Partial<AssignmentRow> = {}): AssignmentRow {
  return {
    id: name,
    identity: `todoist:${name}`,
    name,
    source: "todoist",
    kind: null,
    minutes: 30,
    actionId: null,
    recurring: false,
    assigned: false,
    origin: "local",
    isNewArrival: false,
    ...over,
  };
}

/** Facts keyed by row id, the shape UnassignedToday builds from the source
    items it already holds. */
function factsBy(entries: Array<[string, Partial<DrawerFacts>]>) {
  const map = new Map(entries.map(([id, f]) => [id, facts(f)]));
  return (r: AssignmentRow) => map.get(r.id) ?? facts({ source: r.source });
}

describe("drawerTierOf: one rung per row", () => {
  it("puts a passed deadline in overdue", () => {
    expect(drawerTierOf(facts({ deadline: "2026-07-17" }), TODAY)).toBe("overdue");
  });

  it("puts today's deadline in due today", () => {
    expect(drawerTierOf(facts({ deadline: TODAY }), TODAY)).toBe("due-today");
  });

  it("puts a native scheduled start in fixed time", () => {
    expect(drawerTierOf(facts({ scheduledStart: "09:30" }), TODAY)).toBe("fixed-time");
  });

  it("treats a calendar row as a fixed time with no start of its own", () => {
    expect(drawerTierOf(facts({ source: "calendar" }), TODAY)).toBe("fixed-time");
  });

  it("puts a deadline inside the week in due in 7 days", () => {
    expect(drawerTierOf(facts({ deadline: "2026-07-19" }), TODAY)).toBe("due-7d");
    expect(drawerTierOf(facts({ deadline: "2026-07-25" }), TODAY)).toBe("due-7d");
  });

  it("puts an undated p1 in priority", () => {
    expect(drawerTierOf(facts({ urgency: "4" }), TODAY)).toBe("priority");
    expect(drawerTierOf(facts({ urgency: "3" }), TODAY)).toBe("priority");
  });

  it("puts an undated p3, or no urgency at all, in no date", () => {
    expect(drawerTierOf(facts({ urgency: "2" }), TODAY)).toBe("no-date");
    expect(drawerTierOf(facts(), TODAY)).toBe("no-date");
  });
});

describe("drawerTierOf: precedence when a row qualifies twice", () => {
  it("sends a row that is both timed AND overdue to the overdue tier", () => {
    expect(
      drawerTierOf(facts({ deadline: "2026-07-16", scheduledStart: "08:00" }), TODAY),
    ).toBe("overdue");
  });

  it("sends a row that is both timed AND due today to the due today tier", () => {
    expect(
      drawerTierOf(facts({ deadline: TODAY, scheduledStart: "08:00" }), TODAY),
    ).toBe("due-today");
  });

  it("keeps a timed row with a near deadline in fixed time", () => {
    expect(
      drawerTierOf(facts({ deadline: "2026-07-20", scheduledStart: "11:00" }), TODAY),
    ).toBe("fixed-time");
  });

  it("lets a stated date outrank a p1 flag", () => {
    expect(
      drawerTierOf(facts({ deadline: "2026-07-21", urgency: "4" }), TODAY),
    ).toBe("due-7d");
    expect(drawerTierOf(facts({ deadline: TODAY, urgency: "4" }), TODAY)).toBe(
      "due-today",
    );
  });

  it("does not treat a p1 on another source as a Todoist priority int", () => {
    // normalizeUrgency only tiers Todoist ints; a literal on a Capacities row
    // stays untiered, so the row falls through to no date.
    expect(
      drawerTierOf(facts({ source: "capacities", urgency: "4" }), TODAY),
    ).toBe("no-date");
  });
});

describe("hasFixedTime", () => {
  it("accepts only a 24h HH:MM native start", () => {
    expect(hasFixedTime(facts({ scheduledStart: "00:00" }))).toBe(true);
    expect(hasFixedTime(facts({ scheduledStart: "23:59" }))).toBe(true);
    expect(hasFixedTime(facts({ scheduledStart: "9:30" }))).toBe(false);
    expect(hasFixedTime(facts({ scheduledStart: "24:00" }))).toBe(false);
    expect(hasFixedTime(facts({ scheduledStart: "9am" }))).toBe(false);
    expect(hasFixedTime(facts({ scheduledStart: null }))).toBe(false);
  });
});

describe("drawerTierGroups: source and object type inside a tier", () => {
  it("sub-groups by source, then by Capacities structure title", () => {
    const rows = [
      row("Ship invoice"),
      row("File receipts", { source: "todoist" }),
      row("Kitchen deep clean", { source: "capacities", kind: "custom-project" }),
      row("Read chapter 4", { source: "capacities", kind: "0d194525" }),
      row("Standup", { source: "calendar" }),
    ];
    const groups = drawerTierGroups(
      rows,
      factsBy([
        ["Ship invoice", { source: "todoist" }],
        ["File receipts", { source: "todoist" }],
        ["Kitchen deep clean", { source: "capacities", kind: "Projects" }],
        ["Read chapter 4", { source: "capacities", kind: "Reading" }],
        ["Standup", { source: "calendar" }],
      ]),
      TODAY,
    );

    // The calendar row is a fixed time by definition, so the ladder opens with
    // that tier; everything else here has no date and no priority.
    expect(groups.map((g) => g.key)).toEqual(["fixed-time", "no-date"]);
    expect(groups[0].sources.map((g) => [g.key, g.rows.length])).toEqual([
      ["calendar", 1],
    ]);
    const tier = groups[1];
    expect(tier.count).toBe(4);
    // Todoist first, then Capacities structures alphabetically.
    expect(tier.sources.map((g) => g.key)).toEqual([
      "todoist",
      "capacities:Projects",
      "capacities:Reading",
    ]);
    expect(tier.sources.map((g) => g.label)).toEqual([
      "Todoist",
      "Projects",
      "Reading",
    ]);
    expect(tier.sources[0].rows.map((r) => r.name)).toEqual([
      "Ship invoice",
      "File receipts",
    ]);
    expect(tier.sources[1].rows.length).toBe(1);
  });

  it("falls back to the bare source label when a Capacities row has no title", () => {
    const groups = drawerTierGroups(
      [row("Untitled", { source: "capacities" })],
      factsBy([["Untitled", { source: "capacities", kind: null }]]),
      TODAY,
    );
    expect(groups[0].sources.map((g) => [g.key, g.label])).toEqual([
      ["capacities", "Capacities"],
    ]);
  });

  it("counts every row exactly once across tiers and sub-groups", () => {
    const rows = [
      row("Late"),
      row("Today"),
      row("Timed"),
      row("This week"),
      row("Flagged"),
      row("Someday"),
    ];
    const groups = drawerTierGroups(
      rows,
      factsBy([
        ["Late", { deadline: "2026-07-10" }],
        ["Today", { deadline: TODAY }],
        ["Timed", { scheduledStart: "07:15" }],
        ["This week", { deadline: "2026-07-22" }],
        ["Flagged", { urgency: "4" }],
        ["Someday", {}],
      ]),
      TODAY,
    );
    expect(groups.map((g) => g.key)).toEqual([
      "overdue",
      "due-today",
      "fixed-time",
      "due-7d",
      "priority",
      "no-date",
    ]);
    const total = groups.reduce(
      (n, g) => n + g.sources.reduce((m, sg) => m + sg.rows.length, 0),
      0,
    );
    expect(total).toBe(rows.length);
    expect(groups.map((g) => g.count)).toEqual([1, 1, 1, 1, 1, 1]);
  });
});

describe("drawerTierGroups: empty tiers are hidden", () => {
  it("renders only the tiers that hold rows", () => {
    const groups = drawerTierGroups(
      [row("Someday"), row("Later still")],
      factsBy([
        ["Someday", {}],
        ["Later still", { deadline: "2026-12-01" }],
      ]),
      TODAY,
    );
    expect(groups.map((g) => g.key)).toEqual(["no-date"]);
    expect(groups[0].count).toBe(2);
  });

  it("returns nothing at all for no rows", () => {
    expect(drawerTierGroups([], () => facts(), TODAY)).toEqual([]);
  });

  it("orders tiers by the ladder, whatever order the rows arrive in", () => {
    const rows = [row("C"), row("A"), row("B")];
    const groups = drawerTierGroups(
      rows,
      factsBy([
        ["A", {}],
        ["B", { deadline: TODAY }],
        ["C", { deadline: "2026-07-01" }],
      ]),
      TODAY,
    );
    const ladder = DRAWER_TIERS.map((t) => t.key);
    expect(groups.map((g) => g.key)).toEqual(["overdue", "due-today", "no-date"]);
    // …and that order is a subsequence of the declared ladder.
    const positions = groups.map((g) => ladder.indexOf(g.key));
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it("keeps the incoming row order inside one sub-group", () => {
    const groups = drawerTierGroups(
      [row("First"), row("Second"), row("Third")],
      factsBy([["First", {}], ["Second", {}], ["Third", {}]]),
      TODAY,
    );
    expect(groups[0].sources[0].rows.map((r) => r.name)).toEqual([
      "First",
      "Second",
      "Third",
    ]);
  });
});
