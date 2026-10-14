/* assigned-pill.test.tsx — The row's include control reads as an
   "Assigned" / "Unassigned" pill (Planning 2a, Plan tasks). Planning 2a
   replaced the native checkbox with a toggle button: aria-pressed carries the
   state, the button's accessible name names the flip, and the pill text follows
   state. The control still owns the same today-only inclusion, and the
   keyboard shortcut (E) is unchanged.

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
  it("keeps the status toggle accessible by its full sentence", () => {
    const h = makeHarness("ready");
    const { container, getByLabelText } = h.ui(<Queue />);
    const toggle = getByLabelText("Unassign Review AWS module 4 for today") as HTMLButtonElement;
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    expect(toggle.textContent).toBe("Assigned");
    expect(rowNamed(container, "Review AWS module 4").contains(toggle)).toBe(true);
  });

  // A toggle re-renders the list, so the row is looked up again after each
  // click rather than held as a reference. Unassigning also moves the row to
  // the trailing Out today lane, which starts collapsed (Planning 2a), so the
  // lane is expanded before reading the pill back.
  it("shows Assigned when included and Unassigned when not, following the toggle", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const pillText = (row: HTMLElement) =>
      row.querySelector(".qrow__assign-text")?.textContent;
    const row = () => rowNamed(container, "Review AWS module 4");
    expect(pillText(row())).toBe("Assigned");
    fireEvent.click(row().querySelector(".qrow__assign") as HTMLButtonElement);
    fireEvent.click(container.querySelector(".band--drop") as HTMLButtonElement);
    await waitFor(() => expect(pillText(row())).toBe("Unassigned"));
    expect(row().querySelector(".qrow__assign")?.getAttribute("aria-pressed")).toBe("false");
  });

  it("toggles from the pill text without opening the row", async () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const text = rowNamed(container, "Review AWS module 4").querySelector(
      ".qrow__assign-text",
    ) as HTMLElement;
    expect(text.textContent).toBe("Assigned");
    fireEvent.click(text);
    // The click flips today-only inclusion rather than opening the row's editor.
    expect(h.store.getState().overrides["Review AWS module 4"]).toEqual({
      included: false,
      blocks: null,
    });
    expect(h.store.getState().ui.editorItem).toBeNull();
    // Excluding moves the row into the collapsed Out today lane.
    fireEvent.click(container.querySelector(".band--drop") as HTMLButtonElement);
    await waitFor(() =>
      expect(
        rowNamed(container, "Review AWS module 4").querySelector(".qrow__assign-text")
          ?.textContent,
      ).toBe("Unassigned"),
    );
  });
});
