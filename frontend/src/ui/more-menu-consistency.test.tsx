/* more-menu-consistency.test.tsx — Planning 2a (2026-10-14): the Plan row's
   More (⋯) menu renders every entry as ONE boxed row (same class, border,
   radius, padding, title/detail type) in a fixed group order — Adjust time,
   Allow move, Unschedule, Remove from planning, Always exclude, then Delete
   last behind a hairline divider — and the armed confirm of a destructive
   item never changes that geometry.

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";
import type { Scenario } from "../fixtures/scenarios";

const APP_CSS = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

/** Body of the LAST rule whose selector is exactly `selector`. */
function lastRuleBody(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`(?:^|\\n)\\s*${escaped}\\s*\\{([^}]*)\\}`, "g");
  const hits = Array.from(APP_CSS.matchAll(re), (m) => m[1]);
  if (hits.length === 0) throw new Error(`selector not found in app.css: ${selector}`);
  return hits[hits.length - 1];
}

function pressOf(sc: Scenario) {
  const press = sc.inputs.assigned.find((i) => i.name === "Press");
  if (!press) throw new Error("scenario is missing the Press row");
  return press;
}

/** Todoist + native start: the row that carries the Allow move checkbox and
    the today-only time verbs. */
function withTimedPress(sc: Scenario): void {
  const press = pressOf(sc);
  press.scheduledStart = "19:00";
  press.isRecurring = false;
}

function rowFor(container: Element, name: string): HTMLElement {
  const rows = Array.from(container.querySelectorAll<HTMLElement>(".qrow"));
  const row = rows.find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

function menuItems(menu: Element): HTMLElement[] {
  return Array.from(
    menu.querySelectorAll<HTMLElement>(
      '[role="menuitem"], [role="menuitemcheckbox"]',
    ),
  );
}

function openMore(container: Element, name: string): HTMLElement {
  const row = rowFor(container, name);
  const trigger = row.querySelector<HTMLButtonElement>(
    'button[aria-haspopup="menu"]',
  );
  if (!trigger) throw new Error("no More trigger on the row");
  fireEvent.click(trigger);
  const menu = row.querySelector<HTMLElement>('[role="menu"]');
  if (!menu) throw new Error("More menu is not open");
  return menu;
}

function labels(menu: Element): (string | null)[] {
  return menuItems(menu).map((b) => b.getAttribute("aria-label"));
}

function expectOneItemClass(menu: Element): void {
  const items = menuItems(menu);
  expect(items.length).toBeGreaterThan(0);
  for (const item of items) {
    expect(item.tagName).toBe("BUTTON");
    expect(item.classList.contains("row-more__item")).toBe(true);
    expect(item.classList.contains("alloc-verb")).toBe(true);
  }
}

describe("More menu: one item style and a fixed group order", () => {
  it("orders the native-timed row's items and boxes the checkbox indicator", () => {
    const h = makeHarness("ready", withTimedPress);
    const { container } = h.ui(<Queue />);
    const menu = openMore(container, "Press");
    expectOneItemClass(menu);
    expect(labels(menu)).toEqual([
      "Allow scheduler to move existing time for Press",
      "Unschedule Press",
      "Remove from planning and update source: Press",
      "Always exclude: Press",
      "Delete permanently: Press",
    ]);
    // The checkbox indicator stays inside the boxed item.
    const checkbox = menuItems(menu)[0];
    expect(checkbox.getAttribute("role")).toBe("menuitemcheckbox");
    expect(checkbox.querySelector(".row-more__item-title")).not.toBeNull();
    expect(checkbox.querySelector(".row-more__item-check")).not.toBeNull();
  });

  it("puts Adjust time first on a row without a fixed start", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const menu = openMore(container, "Press");
    expectOneItemClass(menu);
    expect(labels(menu)).toEqual([
      "Adjust time for Press",
      "Remove from planning and update source: Press",
      "Always exclude: Press",
      "Delete permanently: Press",
    ]);
  });

  it("separates Delete from the rest with a hairline divider", () => {
    const h = makeHarness("ready", withTimedPress);
    const { container } = h.ui(<Queue />);
    const menu = openMore(container, "Press");
    const divider = menu.querySelector<HTMLElement>(".row-more__divider");
    expect(divider).not.toBeNull();
    expect(divider!.getAttribute("role")).toBe("separator");
    expect(divider!.nextElementSibling?.getAttribute("aria-label")).toBe(
      "Delete permanently: Press",
    );
    expect(divider!.previousElementSibling?.getAttribute("aria-label")).toBe(
      "Always exclude: Press",
    );
  });

  it("arms Delete and Always exclude in the same box, changing colour only", () => {
    const h = makeHarness("ready", withTimedPress);
    const { container } = h.ui(<Queue />);
    const menu = openMore(container, "Press");
    fireEvent.click(
      menu.querySelector<HTMLElement>('[aria-label="Delete permanently: Press"]')!,
    );
    const armedDelete = menu.querySelector<HTMLElement>(
      '[aria-label="Confirm delete permanently: Press"]',
    );
    expect(armedDelete).not.toBeNull();
    expect(armedDelete!.classList.contains("row-more__item")).toBe(true);
    expect(armedDelete!.classList.contains("alloc-verb--armed")).toBe(true);
    // Armed Delete is not disabled: the danger colour, never the dim disabled
    // treatment, marks the confirm state.
    expect(armedDelete!.hasAttribute("disabled")).toBe(false);

    fireEvent.click(
      menu.querySelector<HTMLElement>('[aria-label="Always exclude: Press"]')!,
    );
    const armedExclude = menu.querySelector<HTMLElement>(
      '[aria-label="Confirm always exclude: Press"]',
    );
    expect(armedExclude).not.toBeNull();
    expect(armedExclude!.classList.contains("row-more__item")).toBe(true);
    expect(armedExclude!.classList.contains("alloc-verb--armed")).toBe(true);
  });
});

describe("More menu: CSS contract for the boxed row", () => {
  const item = lastRuleBody(".row-more__menu .row-more__item");

  it("paints one box: hairline border, shared radius, one padding", () => {
    expect(item).toMatch(/border:\s*0\.5px solid var\(--t-border\)/);
    expect(item).toMatch(/border-radius:\s*var\(--radius-sm\)/);
    expect(item).toMatch(/padding:\s*8px 10px/);
    expect(item).toMatch(/min-width:\s*0/);
  });

  it("keeps the title 14px/500 and the description 12px muted", () => {
    expect(item).toMatch(/font-size:\s*14px/);
    expect(item).toMatch(/font-weight:\s*500/);
    expect(item).toMatch(/text-align:\s*left/);
    const detail = lastRuleBody(
      ".row-more__menu .row-more__item > .row-more__item-detail",
    );
    expect(detail).toMatch(/font-size:\s*12px/);
    expect(detail).toMatch(/font-weight:\s*400/);
    expect(detail).toMatch(/color:\s*var\(--t-muted\)/);
  });

  it("gives every item the same hover, active and focus-visible state", () => {
    const hover = lastRuleBody(".row-more__menu .row-more__item:hover");
    expect(hover).toMatch(/background:\s*var\(--t-surface-2\)/);
    expect(lastRuleBody(".row-more__menu .row-more__item:active")).toBeTruthy();
    expect(
      lastRuleBody(".row-more__menu .row-more__item:focus-visible"),
    ).toMatch(/outline:/);
  });

  it("scopes the danger colour to the armed confirm, not the resting item", () => {
    // The base box owns the resting text colour, so Delete is not dimmed while
    // it is actionable.
    expect(item).toMatch(/color:\s*var\(--t-text\)/);
    const armed = lastRuleBody(
      ".row-more__menu .row-more__item.alloc-verb--armed:hover",
    );
    expect(armed).toMatch(/color:\s*var\(--c-overflow\)/);
    expect(armed).toMatch(/border-color:\s*var\(--c-overflow\)/);
    expect(armed).toMatch(/padding:\s*8px 10px/);
  });
});
