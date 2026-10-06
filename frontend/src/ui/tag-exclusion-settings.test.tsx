/* tag-exclusion-settings.test.tsx — the tag exclusion drawer. Stable
   identities only; titles are display metadata. Saved ids absent from the
   advisory catalog stay listed and removable, and absence is only read as
   "deleted" when the inventory reports itself complete. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { TagExclusionSettingsDrawer } from "./TagExclusionSettingsDrawer";
import { makeHarness } from "./test-harness";
import type { TagExclusionIdentity, TagExclusionSettings } from "../model/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const TAG_A = "5a25370b-f9a0-40cf-bc3a-0cab4744913c";
const TAG_B = "0d194525-c5a1-4af5-bb62-202b83006b5e";
const TAG_C = "11111111-2222-3333-4444-555555555555";

function identity(tagId: string): TagExclusionIdentity {
  return { source: "capacities", spaceId: "space-1", tagId };
}

function settingsFixture(
  overrides: Partial<TagExclusionSettings> = {},
): TagExclusionSettings {
  return {
    version: 1,
    revision: 0,
    persisted: false,
    tags: [],
    catalog: {
      status: "complete",
      spaceId: "space-1",
      tags: [
        { id: TAG_A, title: "habituals" },
        { id: TAG_B, title: "chores" },
      ],
      warnings: [],
    },
    ...overrides,
  };
}

function open(load: TagExclusionSettings) {
  const h = makeHarness("ready");
  vi.spyOn(h.controller, "loadTagExclusionSettings").mockResolvedValue(load);
  h.store.dispatch({ type: "UI", patch: { tagExclusionSettingsOpen: true } });
  const rendered = h.ui(<TagExclusionSettingsDrawer />);
  return { h, rendered };
}

describe("TagExclusionSettingsDrawer", () => {
  it("lists searchable #title checkboxes with ids secondary", async () => {
    const { rendered } = open(settingsFixture());

    expect(rendered.getByRole("status").textContent).toContain("Loading tag exclusions");
    await waitFor(() => expect(rendered.getByText("#habituals")).toBeTruthy());
    expect(rendered.getByText("#chores")).toBeTruthy();
    // Ids are secondary text, always rendered.
    expect(rendered.getByText(TAG_A)).toBeTruthy();
    expect(rendered.getByText(TAG_B)).toBeTruthy();
    expect(rendered.getByText("Tag catalog: complete")).toBeTruthy();

    const search = rendered.getByLabelText("Search tags") as HTMLInputElement;
    fireEvent.input(search, { target: { value: "chores" } });
    expect(rendered.queryByText("#habituals")).toBeNull();
    expect(rendered.getByText("#chores")).toBeTruthy();

    // Searching by id also works.
    fireEvent.input(search, { target: { value: TAG_A.slice(0, 8) } });
    expect(rendered.getByText("#habituals")).toBeTruthy();
    expect(rendered.queryByText("#chores")).toBeNull();
  });

  it("always disambiguates duplicate titles with a short id", async () => {
    const { rendered } = open(
      settingsFixture({
        catalog: {
          status: "complete",
          spaceId: "space-1",
          tags: [
            { id: TAG_A, title: "habituals" },
            { id: TAG_C, title: "habituals" },
          ],
          warnings: [],
        },
      }),
    );

    await waitFor(() => expect(rendered.getByText("#habituals (5a25370b)")).toBeTruthy());
    expect(rendered.getByText("#habituals (11111111)")).toBeTruthy();
  });

  it("keeps saved ids absent from the catalog listed as unresolved and removable", async () => {
    const load = settingsFixture({
      persisted: true,
      revision: 4,
      tags: [identity(TAG_C)],
      catalog: {
        status: "unavailable",
        spaceId: null,
        tags: [],
        warnings: ["Capacities tag catalog unavailable (provider down)"],
      },
    });
    const { h, rendered } = open(load);

    await waitFor(() => expect(rendered.getByText("Unresolved saved tag")).toBeTruthy());
    expect(rendered.getByText("Unavailable/unresolved")).toBeTruthy();
    expect(rendered.getByText(/Tag catalog: unavailable/)).toBeTruthy();
    expect(rendered.getByText(/provider down/)).toBeTruthy();

    const checkbox = rendered.getByRole("checkbox", {
      name: `Exclude saved tag (${TAG_C})`,
    }) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);

    const save = vi
      .spyOn(h.controller, "saveTagExclusionSettings")
      .mockResolvedValue({ settings: load, refreshError: null });
    fireEvent.click(rendered.getByRole("button", { name: "Save tag exclusions" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toEqual({ expectedRevision: 4, tags: [] });
  });

  it("labels an absent saved id as gone only when the inventory is complete", async () => {
    const { rendered } = open(
      settingsFixture({
        persisted: true,
        revision: 1,
        tags: [identity(TAG_C)],
      }),
    );

    await waitFor(() => expect(rendered.getByText("Unresolved saved tag")).toBeTruthy());
    expect(rendered.getByText("No longer exists in Capacities")).toBeTruthy();
    expect(rendered.queryByText("Unavailable/unresolved")).toBeNull();
  });

  it("saves the full replacement against the loaded revision and adopts the response", async () => {
    const load = settingsFixture({
      persisted: true,
      revision: 2,
      tags: [identity(TAG_A)],
    });
    const { h, rendered } = open(load);
    await waitFor(() => expect(rendered.getByText("#habituals")).toBeTruthy());
    expect(rendered.getByText("Saved revision 2")).toBeTruthy();

    fireEvent.click(
      rendered.getByRole("checkbox", { name: `Exclude #chores (${TAG_B})` }),
    );
    const save = vi.spyOn(h.controller, "saveTagExclusionSettings").mockResolvedValue({
      settings: settingsFixture({
        persisted: true,
        revision: 3,
        tags: [identity(TAG_A), identity(TAG_B)],
      }),
      refreshError: null,
    });

    fireEvent.click(rendered.getByRole("button", { name: "Save tag exclusions" }));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    // The draft sorts by stable identity: (source, space, tag id).
    expect(save.mock.calls[0][0]).toEqual({
      expectedRevision: 2,
      tags: [identity(TAG_B), identity(TAG_A)],
    });
    await waitFor(() => expect(rendered.getByText("Saved revision 3")).toBeTruthy());
    expect(rendered.getByText("Saved.")).toBeTruthy();
  });

  it("reports a failed planning refresh instead of showing stale eligibility", async () => {
    const load = settingsFixture({ persisted: true, revision: 1 });
    const { h, rendered } = open(load);
    await waitFor(() => expect(rendered.getByText("#habituals")).toBeTruthy());

    vi.spyOn(h.controller, "saveTagExclusionSettings").mockResolvedValue({
      settings: settingsFixture({ persisted: true, revision: 2 }),
      refreshError: "source refresh degraded — calendar read degraded",
    });

    fireEvent.click(rendered.getByRole("button", { name: "Save tag exclusions" }));

    await waitFor(() =>
      expect(rendered.getByText(/Saved; planning refresh failed/)).toBeTruthy(),
    );
  });

  it("retains the draft on a save error and requires an explicit reload", async () => {
    const load = settingsFixture({ persisted: true, revision: 1 });
    const { h, rendered } = open(load);
    await waitFor(() => expect(rendered.getByText("#habituals")).toBeTruthy());

    fireEvent.click(
      rendered.getByRole("checkbox", { name: `Exclude #chores (${TAG_B})` }),
    );
    vi.spyOn(h.controller, "saveTagExclusionSettings").mockRejectedValue(
      new Error("Tag exclusion settings changed since they were read; reload and retry."),
    );

    fireEvent.click(rendered.getByRole("button", { name: "Save tag exclusions" }));

    await waitFor(() => expect(rendered.getByText("Save failed")).toBeTruthy());
    // Draft retained: the toggle the user made is still checked.
    const chores = rendered.getByRole("checkbox", {
      name: `Exclude #chores (${TAG_B})`,
    }) as HTMLInputElement;
    expect(chores.checked).toBe(true);
    // Saving again is blocked until an explicit reload.
    const saveButton = rendered.getByRole("button", {
      name: "Save tag exclusions",
    }) as HTMLButtonElement;
    expect(saveButton.disabled).toBe(true);
    expect(rendered.getByRole("button", { name: "Reload settings" })).toBeTruthy();
  });

  it("shows a load failure with a reload affordance", async () => {
    const h = makeHarness("ready");
    vi.spyOn(h.controller, "loadTagExclusionSettings").mockRejectedValue(
      new Error("request failed (500)"),
    );
    h.store.dispatch({ type: "UI", patch: { tagExclusionSettingsOpen: true } });
    const rendered = h.ui(<TagExclusionSettingsDrawer />);

    await waitFor(() =>
      expect(rendered.getByText("Tag exclusions could not be loaded")).toBeTruthy(),
    );
    expect(rendered.getByRole("button", { name: "Reload settings" })).toBeTruthy();
  });
});
