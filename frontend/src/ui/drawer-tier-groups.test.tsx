/* drawer-tier-groups.test.tsx — U6: the Not in plan drawer's LOCAL rows are
   one collapsible disclosure per commitment tier, with source / object-type
   sub-groups inside, and a tier that holds nothing is not rendered at all.

   Fixture adapters only: no live adapter, no billed call, no source write. The
   rows arrive in the drawer through the same local path the operator uses —
   the E key — so this is the drawer a real cold plan day renders. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";

import { Queue } from "./Queue";
import { makeHarness, type Harness } from "./test-harness";
import { assignmentRows } from "../store/store";

afterEach(cleanup);

const TODAY_ROW = "Review AWS module 4"; // deadline = the fixture's valid date
const WEEK_ROW = "Magic Mirror"; // deadline two days out
const UNDATED_ROW = "Press"; // no date, no priority
const NOTE = "Note Processing"; // Capacities object, no date

/** Pin the fixture's facts so the ladder is a stated input, not something
    inferred from whatever dates the scenario carries today. */
function pin(inputs: { assigned: Array<{ id: string }> }, id: string, patch: object) {
  const item = inputs.assigned.find((i) => i.id === id) as
    | (object & { id: string })
    | undefined;
  if (!item) throw new Error(`no input row ${id}`);
  Object.assign(item, patch);
}

/** The drawer section, by its live id. */
function drawer(container: Element): HTMLElement {
  const section = container.querySelector<HTMLElement>("#unassigned-today");
  if (!section) throw new Error("no Not in plan drawer");
  return section;
}

function localGroup(container: Element): HTMLElement {
  const group = drawer(container).querySelector<HTMLElement>('[data-group="local"]');
  if (!group) throw new Error("no local group");
  return group;
}

function tierKeys(container: Element): string[] {
  return Array.from(localGroup(container).querySelectorAll<HTMLElement>("[data-tier]"))
    .map((el) => el.getAttribute("data-tier") ?? "");
}

function tier(container: Element, key: string): HTMLElement {
  const el = localGroup(container).querySelector<HTMLElement>(
    `[data-tier="${key}"]`,
  );
  if (!el) throw new Error(`no tier ${key}`);
  return el;
}

function head(container: Element, key: string): HTMLButtonElement {
  const btn = localGroup(container).querySelector<HTMLButtonElement>(
    `[data-tier-head="${key}"]`,
  );
  if (!btn) throw new Error(`no tier head ${key}`);
  return btn;
}

function nameIn(root: Element, name: string): HTMLElement | null {
  return (
    Array.from(root.querySelectorAll<HTMLElement>(".qrow__name")).find(
      (el) => el.textContent === name,
    ) ?? null
  );
}

/** A tier's one-click settle button. */
function addAll(container: Element, key: string): HTMLButtonElement {
  const btn = localGroup(container).querySelector<HTMLButtonElement>(
    `[data-tier-add-all="${key}"]`,
  );
  if (!btn) throw new Error(`no Add all for tier ${key}`);
  return btn;
}

function rowInPlan(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

/** Move rows out of today's plan the way the operator does: the E key. The
    milestone is the drawer's own membership write, not a synthetic state. */
async function removeFromPlan(h: Harness, container: Element, names: string[]) {
  for (const name of names) {
    const before = Object.keys(h.store.getState().planMembership).length;
    fireEvent.keyDown(rowInPlan(container, name), { key: "e" });
    await waitFor(() =>
      expect(Object.keys(h.store.getState().planMembership).length).toBe(before + 1),
    );
  }
  await waitFor(() => expect(drawer(container)).not.toBeNull());
}

describe("U6: local rows group by commitment first", () => {
  it("renders one tier per held row, in ladder order, hiding the empty tiers", async () => {
    const h = makeHarness("ready", (sc) => {
      pin(sc.inputs, TODAY_ROW, { deadline: "2026-07-18", urgency: null });
      pin(sc.inputs, WEEK_ROW, { deadline: "2026-07-20", urgency: null });
      pin(sc.inputs, UNDATED_ROW, { deadline: null, urgency: null });
      pin(sc.inputs, NOTE, { deadline: null, urgency: null });
    });
    const { container } = h.ui(<Queue />);

    await removeFromPlan(h, container, [TODAY_ROW, WEEK_ROW, UNDATED_ROW, NOTE]);

    // Overdue, fixed time and priority hold nothing, so they are not rendered.
    expect(tierKeys(container)).toEqual(["due-today", "due-7d", "no-date"]);

    expect(tier(container, "due-today").textContent).toContain("Due today");
    expect(tier(container, "due-today").textContent).toContain("1 row");
    expect(tier(container, "due-7d").textContent).toContain("Due in 7 days");
    expect(tier(container, "no-date").textContent).toContain("2 rows");

    // Each row sits in exactly one tier.
    expect(nameIn(tier(container, "due-today"), TODAY_ROW)).not.toBeNull();
    expect(nameIn(tier(container, "due-7d"), WEEK_ROW)).not.toBeNull();
    expect(nameIn(tier(container, "no-date"), UNDATED_ROW)).not.toBeNull();
    expect(nameIn(tier(container, "no-date"), NOTE)).not.toBeNull();

    // The tier that groups by date does not hide the date it grouped on.
    expect(
      nameIn(tier(container, "due-today"), TODAY_ROW)?.closest(".qrow")?.textContent,
    ).toContain("due today");
  });

  it("sub-groups each tier by source and object type, Todoist first", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    await removeFromPlan(h, container, [UNDATED_ROW, NOTE]);

    const noDate = tier(container, "no-date");
    const groups = Array.from(
      noDate.querySelectorAll<HTMLElement>("[data-source-group]"),
    );
    expect(groups.map((g) => g.getAttribute("data-source-group"))).toEqual([
      "todoist",
      "capacities:Capacities item",
    ]);
    expect(groups[0].querySelector(".unassigned-source__head")?.textContent).toBe(
      "Todoist1",
    );
    // Untitled structure falls back to the existing kindLabel wording.
    expect(groups[1].querySelector(".unassigned-source__head")?.textContent).toBe(
      "Capacities item1",
    );
    expect(nameIn(groups[0], UNDATED_ROW)).not.toBeNull();
    expect(nameIn(groups[1], NOTE)).not.toBeNull();
    // The switch stays on the row the group is about; it never moves to the head.
    expect(groups[0].querySelector('[role="switch"]')).not.toBeNull();
    expect(head(container, "no-date").querySelector('[role="switch"]')).toBeNull();
  });
});

/* U3a (revised 2026-10-27): selecting today is Adam's job, so a cold day drops
   every sourced row in here and the drawer becomes the day's front door. Each
   tier therefore carries one "Add all" that settles that tier in a single
   click — the same local membership write the per-row switch makes, never an
   adapter call. */
describe("U3a: a tier can be added to the plan in one click", () => {
  it("offers an Add all per tier, named with the tier and its row count", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    await removeFromPlan(h, container, [TODAY_ROW, WEEK_ROW, UNDATED_ROW, NOTE]);

    expect(addAll(container, "due-today").getAttribute("aria-label")).toBe(
      "Add all 1 Due today to plan",
    );
    expect(addAll(container, "due-7d").getAttribute("aria-label")).toBe(
      "Add all 1 Due in 7 days to plan",
    );
    expect(addAll(container, "no-date").getAttribute("aria-label")).toBe(
      "Add all 2 No date to plan",
    );
    // A tooltip states the scope: today's plan, this browser only.
    expect(addAll(container, "no-date").getAttribute("title")).toBe(
      "Add all 2 rows to today's plan — this browser only",
    );
    // It is a button beside the disclosure, never a second matrix of switches.
    expect(addAll(container, "no-date").querySelector('[role="switch"]')).toBeNull();
    expect(addAll(container, "no-date").textContent).toContain("Add all");
  });

  it("adds exactly that tier's rows and makes zero source calls", async () => {
    const h = makeHarness("ready");
    const calls: string[] = [];
    const realRuntimeAction = h.adapter.runtimeAction.bind(h.adapter);
    h.adapter.runtimeAction = async (verb, target, args) => {
      calls.push(`runtime:${verb}:${target}`);
      return realRuntimeAction(verb, target, args);
    };
    const realReassign = h.adapter.reassign.bind(h.adapter);
    h.adapter.reassign = async (identity: string) => {
      calls.push(`reassign:${identity}`);
      return realReassign(identity);
    };
    const { container } = h.ui(<Queue />);
    await removeFromPlan(h, container, [TODAY_ROW, WEEK_ROW, UNDATED_ROW, NOTE]);

    fireEvent.click(addAll(container, "no-date"));

    await waitFor(() => expect(nameIn(drawer(container), NOTE)).toBeNull());
    const rows = assignmentRows(h.store.getState());
    // The tier's own rows are in the plan …
    expect(rows.assigned.map((r) => r.name)).toEqual(
      expect.arrayContaining([UNDATED_ROW, NOTE]),
    );
    // … and the tiers he did not touch stay exactly where they were.
    expect(rows.unassigned.map((r) => r.name)).toHaveLength(2);
    expect(rows.unassigned.map((r) => r.name)).toEqual(
      expect.arrayContaining([TODAY_ROW, WEEK_ROW]),
    );
    expect(tierKeys(container)).toEqual(["due-today", "due-7d"]);
    // LOCAL only: the source was never asked, and nothing was written to it.
    expect(calls).toEqual([]);
  });

  it("empties the drawer once every tier has been added", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    await removeFromPlan(h, container, [TODAY_ROW, UNDATED_ROW]);

    for (const key of tierKeys(container)) {
      fireEvent.click(addAll(container, key));
    }

    await waitFor(() => expect(container.querySelector("#unassigned-today")).toBeNull());
    expect(h.store.getState().planMembership).toEqual({});
  });
});

describe("U6: a tier collapses and expands", () => {
  it("hides its rows, says so in words, and comes back", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    await removeFromPlan(h, container, [UNDATED_ROW]);

    const toggle = head(container, "no-date");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.getAttribute("aria-label")).toContain("expanded");
    expect(toggle.getAttribute("title")).toBe("Collapse");
    expect(toggle.querySelector("svg.unassigned-tier__chevron")).not.toBeNull();
    // The state is the chevron, not a status-pill word.
    expect(toggle.querySelector(".unassigned-tier__state")).toBeNull();
    // The section is already titled "Not in plan"; local rows do not repeat it.
    expect(nameIn(localGroup(container), UNDATED_ROW)?.closest("li")?.textContent).not.toContain(
      "Not in plan",
    );
    expect(nameIn(localGroup(container), UNDATED_ROW)).not.toBeNull();

    fireEvent.click(toggle);
    await waitFor(() =>
      expect(head(container, "no-date").getAttribute("aria-expanded")).toBe("false"),
    );
    const closed = head(container, "no-date");
    expect(closed.getAttribute("aria-label")).toContain("collapsed");
    expect(closed.getAttribute("title")).toBe("Expand");
    expect(closed.querySelector(".unassigned-tier__chevron--closed")).not.toBeNull();
    expect(closed.getAttribute("aria-label")).toContain("1 row hidden");
    // The head keeps the tier and its count; only the rows leave the page.
    expect(closed.textContent).toContain("No date");
    expect(closed.textContent).toContain("1 row hidden");
    expect(nameIn(localGroup(container), UNDATED_ROW)).toBeNull();

    fireEvent.click(head(container, "no-date"));
    await waitFor(() =>
      expect(head(container, "no-date").getAttribute("aria-expanded")).toBe("true"),
    );
    expect(nameIn(localGroup(container), UNDATED_ROW)).not.toBeNull();
  });

  it("collapses one tier without touching another", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    await removeFromPlan(h, container, [TODAY_ROW, UNDATED_ROW]);

    fireEvent.click(head(container, "no-date"));
    await waitFor(() =>
      expect(head(container, "no-date").getAttribute("aria-expanded")).toBe("false"),
    );
    expect(head(container, "due-today").getAttribute("aria-expanded")).toBe("true");
    expect(nameIn(tier(container, "due-today"), TODAY_ROW)).not.toBeNull();
    expect(nameIn(localGroup(container), UNDATED_ROW)).toBeNull();
  });
});
