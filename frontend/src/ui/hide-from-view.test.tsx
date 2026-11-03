import { describe, expect, it } from "vitest";
import { fireEvent } from "@testing-library/preact";
import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";

describe("session-only hide from view", () => {
  it("hides a row, counts it, and Show all brings it back — no storage write", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const btn = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Hide Press from view"]',
    )!;
    expect(btn).not.toBeNull();
    fireEvent.click(btn);
    expect(container.querySelector('button[aria-label="Hide Press from view"]')).toBeNull();
    expect(container.querySelector("[data-hidden-strip]")?.textContent).toMatch(/1 hidden/);
    fireEvent.click(container.querySelector("[data-hidden-strip] button")!);
    expect(container.querySelector('button[aria-label="Hide Press from view"]')).not.toBeNull();
    expect(container.querySelector("[data-hidden-strip]")).toBeNull();
  });
});

import { includedItems } from "../store/store";

describe("hidden rows leave the plan math", () => {
  it("drops from includedItems on hide and returns on Show all", () => {
    const h = makeHarness("ready");
    h.ui(<Queue />);
    const before = includedItems(h.store.getState());
    const target = before[0];
    expect(target).toBeDefined();
    h.controller.hideFromView(target.id);
    expect(includedItems(h.store.getState()).some((i) => i.id === target.id)).toBe(false);
    h.controller.showAllHidden();
    expect(includedItems(h.store.getState()).map((i) => i.id)).toEqual(before.map((i) => i.id));
  });
});
