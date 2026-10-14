/* always-exclude.test.tsx — Planning 2a: the Plan row's More (⋯) menu gains an
   "Always exclude" item for todoist and capacities rows. It is a destructive
   verb (the row stops being planned every day), so it arms on the first
   activation (the text becomes the confirm question plus the undo path),
   commits on the second, and cancels on Escape or when focus moves away. The
   commit runs through the controller's persistent exclusion methods and the
   row leaves the plan after the controller's own source refresh.

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, waitFor } from "@testing-library/preact";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import type { AssignedItem } from "../model/types";

const TODOIST_DETAIL =
  "Hides this task from planning every day. Undo in Connections > Hidden tasks.";
const CAPACITIES_DETAIL =
  "Hides this task from planning every day. Undo in Connections > Capacities > Known objects.";

function rowFor(container: Element, name: string): HTMLElement {
  const rows = Array.from(container.querySelectorAll<HTMLElement>(".qrow"));
  const row = rows.find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

function rowOrNull(container: Element, name: string): HTMLElement | null {
  try {
    return rowFor(container, name);
  } catch {
    return null;
  }
}

function moreTrigger(row: Element): HTMLButtonElement {
  const trigger = row.querySelector<HTMLButtonElement>(
    'button[aria-haspopup="menu"]',
  );
  if (!trigger) throw new Error("no More trigger on the row");
  return trigger;
}

function menuOf(row: Element): HTMLElement {
  const menu = row.querySelector<HTMLElement>('[role="menu"]');
  if (!menu) throw new Error("More menu is not open");
  return menu;
}

function itemByLabel(root: Element, label: string): HTMLButtonElement {
  const item = root.querySelector<HTMLButtonElement>(
    `button[aria-label="${label}"]`,
  );
  if (!item) throw new Error(`no menu item labelled ${label}`);
  return item;
}

function openMore(container: Element, name: string): HTMLElement {
  const row = rowFor(container, name);
  fireEvent.click(moreTrigger(row));
  return row;
}

function addCalendarRow(sc: { inputs: { assigned: AssignedItem[] } }): void {
  const base = sc.inputs.assigned.find((i) => i.name === "Press");
  if (!base) throw new Error("scenario is missing the Press row");
  sc.inputs.assigned.push({
    ...structuredClone(base),
    id: "Calendar standup",
    name: "Calendar standup",
    source: "calendar",
    types: [],
    todoistId: null,
    identity: null,
    capacitiesIdentity: null,
  });
}

describe("Always exclude: menu presence", () => {
  it("shows the item for todoist and capacities rows, with source-specific undo paths", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);

    const press = openMore(container, "Press");
    const pressItem = itemByLabel(press, "Always exclude: Press");
    expect(pressItem.getAttribute("role")).toBe("menuitem");
    expect(pressItem.textContent).toContain("Always exclude");
    expect(menuOf(press).textContent).toContain(TODOIST_DETAIL);

    const note = openMore(container, "Note Processing");
    itemByLabel(note, "Always exclude: Note Processing");
    expect(menuOf(note).textContent).toContain(CAPACITIES_DETAIL);
  });

  it("does not offer the item on calendar rows", () => {
    const h = makeHarness("ready", addCalendarRow);
    const { container } = h.ui(<Queue />);
    const calendar = openMore(container, "Calendar standup");
    expect(
      calendar.querySelector('[aria-label="Always exclude: Calendar standup"]'),
    ).toBeNull();
  });

  it("does not offer the item on a todoist row with no task id", () => {
    const h = makeHarness("ready", (sc) => {
      const press = sc.inputs.assigned.find((i) => i.name === "Press");
      if (!press) throw new Error("scenario is missing the Press row");
      press.todoistId = null;
    });
    const { container } = h.ui(<Queue />);
    const press = openMore(container, "Press");
    expect(
      press.querySelector('[aria-label="Always exclude: Press"]'),
    ).toBeNull();
  });
});

describe("Always exclude: armed confirm", () => {
  it("arms on the first activation with the confirm text and undo path", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = openMore(container, "Press");

    fireEvent.click(itemByLabel(press, "Always exclude: Press"));

    const armed = itemByLabel(press, "Confirm always exclude: Press");
    expect(armed.textContent).toContain('Confirm: always exclude "Press"?');
    expect(armed.textContent).toContain(TODOIST_DETAIL);
    // No controller call from arming alone.
    expect(rowOrNull(container, "Press")).not.toBeNull();
  });

  it("Escape cancels the arming and closes the menu", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = openMore(container, "Press");

    const item = itemByLabel(press, "Always exclude: Press");
    item.focus();
    fireEvent.click(item);
    expect(itemByLabel(press, "Confirm always exclude: Press")).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(press.querySelector('[role="menu"]')).toBeNull();

    fireEvent.click(moreTrigger(press));
    const reopened = itemByLabel(press, "Always exclude: Press");
    expect(reopened.textContent).toContain("Always exclude");
    expect(reopened.textContent).not.toContain("Confirm:");
  });

  it("cancels when focus moves away", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = openMore(container, "Press");

    const item = itemByLabel(press, "Always exclude: Press");
    item.focus();
    fireEvent.click(item);
    expect(itemByLabel(press, "Confirm always exclude: Press")).toBeTruthy();

    const other = menuOf(press).querySelector<HTMLElement>(
      '[role="menuitem"]:not([aria-label="Confirm always exclude: Press"])',
    );
    expect(other).not.toBeNull();
    act(() => other!.focus());
    expect(itemByLabel(press, "Always exclude: Press")).toBeTruthy();
  });
});

describe("Always exclude: commit", () => {
  it("commits a todoist row through alwaysExcludeTodoist with id and title, and the row leaves", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const calls: Array<{ todoistId: string; name: string }> = [];
    const original = h.controller.alwaysExcludeTodoist.bind(h.controller);
    vi.spyOn(h.controller, "alwaysExcludeTodoist").mockImplementation(
      async (input) => {
        calls.push({ ...input });
        return original(input);
      },
    );

    const press = openMore(container, "Press");
    fireEvent.click(itemByLabel(press, "Always exclude: Press"));
    fireEvent.click(itemByLabel(press, "Confirm always exclude: Press"));

    await waitFor(
      () => expect(calls).toEqual([{ todoistId: "6fx00xPRESS", name: "Press" }]),
      { timeout: 3000 },
    );
    await waitFor(() => expect(rowOrNull(container, "Press")).toBeNull(), {
      timeout: 3000,
    });
    const settings = await h.controller.loadTagExclusionSettings();
    expect(
      settings.todoist.some((entry) => entry.id === "6fx00xPRESS"),
    ).toBe(true);
  });

  it("commits a capacities row through alwaysExcludeCapacitiesObject with the canonical identity", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const calls: string[] = [];
    const original = h.controller.alwaysExcludeCapacitiesObject.bind(
      h.controller,
    );
    vi.spyOn(h.controller, "alwaysExcludeCapacitiesObject").mockImplementation(
      async (identity) => {
        calls.push(identity);
        return original(identity);
      },
    );

    const note = openMore(container, "Note Processing");
    const item = itemByLabel(note, "Always exclude: Note Processing");
    fireEvent.click(item);
    fireEvent.click(itemByLabel(note, "Confirm always exclude: Note Processing"));

    // The item is disabled while the save is in flight.
    expect(itemByLabel(note, "Always exclude: Note Processing").disabled).toBe(
      true,
    );

    await waitFor(
      () =>
        expect(calls).toEqual([
          "capacities:fixture-space:custom-project:note-processing",
        ]),
      { timeout: 3000 },
    );
    await waitFor(
      () =>
        expect(menuOf(note).querySelector('[role="status"]')?.textContent).toBe(
          "Always excluded: Note Processing.",
        ),
      { timeout: 3000 },
    );
    expect(
      itemByLabel(note, "Always exclude: Note Processing").disabled,
    ).toBe(false);
  });

  it("shows the error text and keeps the row when the save fails", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    vi.spyOn(h.controller, "alwaysExcludeTodoist").mockRejectedValueOnce(
      new Error("Tag exclusion settings changed since they were read"),
    );

    const press = openMore(container, "Press");
    fireEvent.click(itemByLabel(press, "Always exclude: Press"));
    fireEvent.click(itemByLabel(press, "Confirm always exclude: Press"));

    await waitFor(
      () =>
        expect(
          menuOf(press).querySelector('[role="status"]')?.textContent,
        ).toBe(
          "Could not always exclude: Tag exclusion settings changed since they were read",
        ),
      { timeout: 3000 },
    );
    expect(rowOrNull(container, "Press")).not.toBeNull();
    expect(
      itemByLabel(press, "Always exclude: Press").disabled,
    ).toBe(false);
  });
});

describe("Always exclude: keyboard flow", () => {
  it("is reachable with the menu's arrow-key roving focus", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const press = openMore(container, "Press");
    const items = Array.from(
      menuOf(press).querySelectorAll<HTMLElement>('[role="menuitem"]'),
    );
    expect(items.length).toBeGreaterThan(0);
    items[0].focus();

    let reached = false;
    for (let i = 0; i < items.length + 1; i += 1) {
      fireEvent.keyDown(document.activeElement as Element, {
        key: "ArrowDown",
      });
      if (
        (document.activeElement as HTMLElement).getAttribute("aria-label") ===
        "Always exclude: Press"
      ) {
        reached = true;
        break;
      }
    }
    expect(reached).toBe(true);

    // Enter on a focused native button commits the same way as a click; the
    // second activation is the commit.
    const item = document.activeElement as HTMLButtonElement;
    fireEvent.click(item);
    expect(itemByLabel(press, "Confirm always exclude: Press")).toBeTruthy();
  });
});
