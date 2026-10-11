/* assigned-pill.test.tsx — The row's include checkbox reads as an
   "Assigned" / "Unassigned" pill (mockup Screen B). The native checkbox stays
   the control: same accessible name, same toggle, pill text follows state.

   Fixture adapters only: no live adapter, no billed call. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness } from "./test-harness";

function rowNamed(container: Element, name: string): HTMLElement {
  const row = Array.from(
    container.querySelectorAll<HTMLElement>(".qrow"),
  ).find((r) => r.querySelector(".qrow__name")?.textContent === name);
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

describe("row include control reads as an Assigned pill", () => {
  it("keeps the checkbox accessible by its full sentence", () => {
    const h = makeHarness("ready");
    const { container, getByLabelText } = h.ui(<Queue />);
    const box = getByLabelText("Include Review AWS module 4 in today's plan") as HTMLInputElement;
    expect(box.type).toBe("checkbox");
    expect(rowNamed(container, "Review AWS module 4").contains(box)).toBe(true);
  });

  // A toggle re-renders the list, so the row and checkbox are looked up again
  // after each click rather than held as references.
  it("shows Assigned when included and Unassigned when not, following the checkbox", async () => {
    const h = makeHarness("ready");
    const { container, getByLabelText } = h.ui(<Queue />);
    const box = () =>
      getByLabelText("Include Review AWS module 4 in today's plan") as HTMLInputElement;
    const pillText = () =>
      rowNamed(container, "Review AWS module 4").querySelector(".qrow__assign-text")?.textContent;
    const before = box().checked;
    expect(pillText()).toBe(before ? "Assigned" : "Unassigned");
    fireEvent.click(box());
    await waitFor(() => expect(pillText()).toBe(before ? "Unassigned" : "Assigned"));
    expect(box().checked).toBe(!before);
  });

  it("toggles from the pill text without opening the row", async () => {
    const h = makeHarness("ready");
    const { container, getByLabelText } = h.ui(<Queue />);
    const box = () =>
      getByLabelText("Include Review AWS module 4 in today's plan") as HTMLInputElement;
    const before = box().checked;
    const text = rowNamed(container, "Review AWS module 4").querySelector(".qrow__assign-text") as HTMLElement;
    fireEvent.click(text);
    await waitFor(() => expect(box().checked).toBe(!before));
    expect(
      rowNamed(container, "Review AWS module 4").querySelector(".qrow__assign-text")?.textContent,
    ).toBe(before ? "Unassigned" : "Assigned");
  });
});
