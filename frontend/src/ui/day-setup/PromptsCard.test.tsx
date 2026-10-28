/* PromptsCard.test.tsx: the Day Setup Live micro-adventure card shows the same
   pool provenance note and Capacities option suffix as the settings drawer,
   and the For Meegy and Stoic cards carry the skip-description toggles. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

import type { DescriptionSkips, MicroAdventure } from "../../model/types";
import { makeHarness } from "../test-harness";
import { PromptsCard } from "./PromptsCard";
import { writeCustomPrompts } from "./customPrompts";

afterEach(cleanup);

const noop = () => {};

/** A Map-backed Storage so a test can seed the browser-defined prompts. */
function fakeStorage(): Storage {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
    clear: () => data.clear(),
    key: (i: number) => Array.from(data.keys())[i] ?? null,
    get length() {
      return data.size;
    },
  } as Storage;
}

function micro(over: Partial<MicroAdventure> = {}): MicroAdventure {
  return {
    pick: { id: "ma01", idea: "Watch sunset", category: "nature" },
    pool: [
      { id: "ma01", idea: "Watch sunset", category: "nature" },
      { id: "cap-1", idea: "Try pottery", category: "craft" },
    ],
    streak: 0,
    source: "auto",
    poolSource: "config",
    poolWarnings: [],
    ...over,
  } as MicroAdventure;
}

/** Mount the card over a staged harness. The skip toggles read the day-setup
    echo from the store, so the card renders inside the app context. */
function card(
  m: MicroAdventure,
  storage: Storage = fakeStorage(),
  skips?: DescriptionSkips,
  scenario: "ready" | "fresh" = "ready",
) {
  const h = makeHarness(scenario, (sc) => {
    if (skips) sc.inputs.daySetup = { ...sc.inputs.daySetup, descriptionSkips: skips };
  });
  const r = h.ui(
    <PromptsCard
      micro={m}
      liveCustom=""
      onLiveCustom={noop}
      onPickPool={noop}
      onSetCustom={noop}
      onShuffle={noop}
      onResetAuto={noop}
      captures={{ intention: "", forMeegy: "", stoic: "" }}
      onCapture={noop}
      onAutosave={noop}
      onPersistCustom={noop}
      promptSave={{ phase: "idle", error: null, warnings: [] }}
      customStorage={storage}
    />,
  );
  return { r, h };
}

describe("PromptsCard Reminder copy", () => {
  it("labels the one-thing prompt Reminder with the reminder placeholder", () => {
    const { r } = card(micro());
    const field = r.getByLabelText("Reminder") as HTMLTextAreaElement;
    expect(field.closest(".prompt-card")).toBeTruthy();
    expect(field.getAttribute("placeholder")).toBe("One thing to remind myself today");
    // The rename is a copy change only: the wire key and its CSS class stay.
    expect(field.id).toBe("cap-intention");
    expect(field.className).toContain("cap-intention");
    expect(r.queryByLabelText("Intention")).toBeNull();
    expect(r.queryByPlaceholderText("One thing to focus on today")).toBeNull();
  });

  it("names Reminder in the built-in note when no custom prompts exist", () => {
    const { r } = card(micro());
    fireEvent.click(r.getByRole("button", { name: "Edit prompts" }));
    expect(
      r.getByText(/Reminder, For Meegy and Stoic are built in and can't be renamed or removed/),
    ).toBeTruthy();
    expect(r.queryByText(/Intention, For Meegy and Stoic/)).toBeNull();
  });

  it("names Reminder in the built-in note when custom prompts exist", () => {
    const storage = fakeStorage();
    writeCustomPrompts(
      [{ id: "custom-abc-1", question: "What am I grateful for?", answer: "" }],
      storage,
    );
    const { r } = card(micro(), storage);
    fireEvent.click(r.getByRole("button", { name: "Edit prompts" }));
    expect(
      r.getByText(/Rename or remove your own prompts. Reminder, For Meegy and Stoic are built in/),
    ).toBeTruthy();
    expect(r.queryByText(/Intention, For Meegy and Stoic/)).toBeNull();
  });
});

describe("PromptsCard skip-description toggles", () => {
  it("renders one toggle on the For Meegy and Stoic cards, and none on Reminder", () => {
    const { r } = card(micro());
    expect(r.getAllByRole("checkbox")).toHaveLength(2);
    const meegy = r.getByLabelText("Skip description for Meegy") as HTMLInputElement;
    const stoic = r.getByLabelText("Skip description for Stoic") as HTMLInputElement;
    expect(meegy.getAttribute("type")).toBe("checkbox");
    expect(meegy.closest(".capacities-setting-row")).toBeTruthy();
    expect(meegy.closest(".prompt-card")?.querySelector("#cap-meegy")).toBeTruthy();
    expect(stoic.closest(".prompt-card")?.querySelector("#cap-stoic")).toBeTruthy();
    const reminder = r.getByLabelText("Reminder").closest(".prompt-card") as HTMLElement;
    expect(reminder.querySelector('input[type="checkbox"]')).toBeNull();
  });

  it("follows the incoming server state", () => {
    const { r } = card(micro(), fakeStorage(), { forMeegy: true, stoic: false });
    expect((r.getByLabelText("Skip description for Meegy") as HTMLInputElement).checked).toBe(
      true,
    );
    expect((r.getByLabelText("Skip description for Stoic") as HTMLInputElement).checked).toBe(
      false,
    );
  });

  it("reads an absent skip map as unchecked", () => {
    const { r } = card(micro());
    expect((r.getByLabelText("Skip description for Meegy") as HTMLInputElement).checked).toBe(
      false,
    );
    expect((r.getByLabelText("Skip description for Stoic") as HTMLInputElement).checked).toBe(
      false,
    );
  });

  it("toggling a key saves the whole map and keeps the other key's value", async () => {
    const { r, h } = card(micro(), fakeStorage(), { forMeegy: true, stoic: false }, "fresh");
    const save = vi.spyOn(h.controller, "savePromptSkips");
    const draftsBefore = h.store.getState().daySetup.captures;
    fireEvent.click(r.getByLabelText("Skip description for Stoic"));
    await vi.waitFor(() => {
      expect(save).toHaveBeenLastCalledWith({ forMeegy: true, stoic: true });
    });
    // Prompt-only: no capture text moves and the day is never confirmed.
    expect(h.store.getState().daySetup.captures).toEqual(draftsBefore);
    expect(h.store.getState().daySetup.confirmed).toBe(false);
    await vi.waitFor(() => {
      expect((r.getByLabelText("Skip description for Stoic") as HTMLInputElement).checked).toBe(
        true,
      );
    });
  });

  it("explains that a skipped prompt still creates the task", () => {
    const { r } = card(micro());
    expect(
      r.getByText("Skipping still creates the task — only the description is left out."),
    ).toBeTruthy();
    // The existing export line stays as it is.
    expect(
      r.getByText("Filled prompts are added to your Todoist Inbox when you commit."),
    ).toBeTruthy();
  });
});

describe("PromptsCard pool note", () => {
  it("shows one note with the first warning and the full list in title/aria", () => {
    const { r } = card(micro({ poolWarnings: ["First warning.", "Second warning."] }));
    const notes = r.container.querySelectorAll<HTMLElement>(".live-micro__pool-note");
    expect(notes).toHaveLength(1);
    expect(notes[0].getAttribute("role")).toBe("note");
    expect(notes[0].className).toContain("field__hint");
    expect(notes[0].textContent).toBe("First warning.");
    expect(notes[0].getAttribute("title")).toBe("First warning.\nSecond warning.");
    expect(notes[0].getAttribute("aria-label")).toBe("First warning. Second warning.");
  });

  it("renders no note without warnings", () => {
    const { r } = card(micro());
    expect(r.container.querySelector(".live-micro__pool-note")).toBeNull();
  });

  it("marks cap- options as Capacities", () => {
    const { r } = card(micro());
    const opts = Array.from(r.container.querySelectorAll("#live-pick option")).map(
      (o) => o.textContent,
    );
    expect(opts).toEqual(["Watch sunset", "Try pottery · Capacities"]);
  });
});
