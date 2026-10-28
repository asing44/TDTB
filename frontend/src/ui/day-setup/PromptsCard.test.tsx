/* PromptsCard.test.tsx: the Day Setup Live micro-adventure card shows the same
   pool provenance note and Capacities option suffix as the settings drawer,
   and the For Meegy and Stoic cards carry the skip-description toggles.

   Two mount helpers: `cardWithStore` renders inside the staged app harness (the
   store-backed path, where the skip toggles and the Edit pool control exist),
   and `cardWithoutStore` renders bare, with no store behind the card. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/preact";

import type { DescriptionSkips, MicroAdventure, PromptDisabled } from "../../model/types";
import { FixtureAdapter } from "../../adapters/fixture";
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
function cardWithStore(
  m: MicroAdventure,
  storage: Storage = fakeStorage(),
  skips?: DescriptionSkips,
  scenario: "ready" | "fresh" = "ready",
  disabled?: PromptDisabled,
) {
  const h = makeHarness(scenario, (sc) => {
    if (skips) sc.inputs.daySetup = { ...sc.inputs.daySetup, descriptionSkips: skips };
    if (disabled) sc.inputs.daySetup = { ...sc.inputs.daySetup, promptDisabled: disabled };
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

/** Mount the card bare, with no app store behind it. The store-backed controls
    (skip toggles, Edit pool) must be absent, not fatal. */
function cardWithoutStore(m: MicroAdventure) {
  const storage = { getItem: () => null, setItem: noop, removeItem: noop } as unknown as Storage;
  return render(
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
}

describe("PromptsCard Reminder copy", () => {
  it("labels the one-thing prompt Reminder with the reminder placeholder", () => {
    const { r } = cardWithStore(micro());
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
    const { r } = cardWithStore(micro());
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
    const { r } = cardWithStore(micro(), storage);
    fireEvent.click(r.getByRole("button", { name: "Edit prompts" }));
    expect(
      r.getByText(/Rename or remove your own prompts. Reminder, For Meegy and Stoic are built in/),
    ).toBeTruthy();
    expect(r.queryByText(/Intention, For Meegy and Stoic/)).toBeNull();
  });
});

describe("PromptsCard skip-description toggles", () => {
  it("renders one toggle on the For Meegy and Stoic cards, and none on Reminder", () => {
    const { r } = cardWithStore(micro());
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
    const { r } = cardWithStore(micro(), fakeStorage(), { forMeegy: true, stoic: false });
    expect((r.getByLabelText("Skip description for Meegy") as HTMLInputElement).checked).toBe(
      true,
    );
    expect((r.getByLabelText("Skip description for Stoic") as HTMLInputElement).checked).toBe(
      false,
    );
  });

  it("reads an absent skip map as unchecked", () => {
    const { r } = cardWithStore(micro());
    expect((r.getByLabelText("Skip description for Meegy") as HTMLInputElement).checked).toBe(
      false,
    );
    expect((r.getByLabelText("Skip description for Stoic") as HTMLInputElement).checked).toBe(
      false,
    );
  });

  it("toggling a key saves the whole map and keeps the other key's value", async () => {
    const { r, h } = cardWithStore(micro(), fakeStorage(), { forMeegy: true, stoic: false }, "fresh");
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
    const { r } = cardWithStore(micro());
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
    const { r } = cardWithStore(micro({ poolWarnings: ["First warning.", "Second warning."] }));
    const notes = r.container.querySelectorAll<HTMLElement>(".live-micro__pool-note");
    expect(notes).toHaveLength(1);
    expect(notes[0].getAttribute("role")).toBe("note");
    expect(notes[0].className).toContain("field__hint");
    expect(notes[0].textContent).toBe("First warning.");
    expect(notes[0].getAttribute("title")).toBe("First warning.\nSecond warning.");
    expect(notes[0].getAttribute("aria-label")).toBe("First warning. Second warning.");
  });

  it("renders no note without warnings", () => {
    const { r } = cardWithStore(micro());
    expect(r.container.querySelector(".live-micro__pool-note")).toBeNull();
  });

  it("marks cap- options as Capacities", () => {
    const { r } = cardWithStore(micro());
    const opts = Array.from(r.container.querySelectorAll("#live-pick option")).map(
      (o) => o.textContent,
    );
    expect(opts).toEqual(["Watch sunset", "Try pottery · Capacities"]);
  });
});

describe("PromptsCard include-in-commit switches", () => {
  it("puts one switch on the For Meegy and Stoic cards, and none on Reminder", () => {
    const { r } = cardWithStore(micro());
    const meegy = r.getByLabelText("Include For Meegy in commit") as HTMLInputElement;
    const stoic = r.getByLabelText("Include Stoic in commit") as HTMLInputElement;
    expect(meegy.getAttribute("role")).toBe("switch");
    expect(meegy.getAttribute("aria-checked")).toBe("true");
    expect(meegy.checked).toBe(true);
    expect(stoic.checked).toBe(true);
    expect(meegy.closest(".prompt-card")?.querySelector("#cap-meegy")).toBeTruthy();
    expect(stoic.closest(".prompt-card")?.querySelector("#cap-stoic")).toBeTruthy();
    const reminder = r.getByLabelText("Reminder").closest(".prompt-card") as HTMLElement;
    expect(reminder.querySelector('[role="switch"]')).toBeNull();
    // Copy lock: the same short copy sits on both cards.
    expect(r.getAllByText("Include in commit")).toHaveLength(2);
  });

  it("follows the day-setup echo", () => {
    const { r } = cardWithStore(micro(), fakeStorage(), undefined, "ready", {
      forMeegy: true,
      stoic: false,
    });
    const meegy = r.getByLabelText("Include For Meegy in commit") as HTMLInputElement;
    expect(meegy.checked).toBe(false);
    expect(meegy.getAttribute("aria-checked")).toBe("false");
    expect((r.getByLabelText("Include Stoic in commit") as HTMLInputElement).checked).toBe(true);
  });

  it("reads an absent map as both prompts included", () => {
    const { r } = cardWithStore(micro());
    expect((r.getByLabelText("Include For Meegy in commit") as HTMLInputElement).checked).toBe(
      true,
    );
    expect((r.getByLabelText("Include Stoic in commit") as HTMLInputElement).checked).toBe(true);
  });

  it("toggling one off saves the whole map and keeps the other prompt's value", async () => {
    const { r, h } = cardWithStore(micro(), fakeStorage(), undefined, "fresh");
    const save = vi.spyOn(h.controller, "savePromptDisabled");
    const draftsBefore = h.store.getState().daySetup.captures;
    fireEvent.click(r.getByLabelText("Include For Meegy in commit"));
    await vi.waitFor(() => {
      expect(save).toHaveBeenLastCalledWith({ forMeegy: true, stoic: false });
    });
    // Prompt-only: no capture text moves and the day is never confirmed.
    expect(h.store.getState().daySetup.captures).toEqual(draftsBefore);
    expect(h.store.getState().daySetup.confirmed).toBe(false);
    await vi.waitFor(() => {
      expect(
        (r.getByLabelText("Include For Meegy in commit") as HTMLInputElement).checked,
      ).toBe(false);
    });
  });

  it("mutes the textarea and disables the skip control while a prompt is off", () => {
    const { r } = cardWithStore(micro(), fakeStorage(), undefined, "ready", {
      forMeegy: true,
      stoic: false,
    });
    const meegy = r.getByLabelText("For Meegy") as HTMLTextAreaElement;
    const stoic = r.getByLabelText("Stoic") as HTMLTextAreaElement;
    // Still editable: the draft survives, it is just de-emphasised.
    expect(meegy.disabled).toBe(false);
    expect(meegy.className).toContain("trow__muted");
    expect(stoic.className).not.toContain("trow__muted");
    // Nothing is exported, so the skip-description control is inert.
    expect((r.getByLabelText("Skip description for Meegy") as HTMLInputElement).disabled).toBe(
      true,
    );
    expect((r.getByLabelText("Skip description for Stoic") as HTMLInputElement).disabled).toBe(
      false,
    );
  });

  it("renders no switch without the app store behind it", () => {
    const r = cardWithoutStore(micro());
    expect(r.queryByLabelText("Include For Meegy in commit")).toBeNull();
    expect(r.queryByLabelText("Include Stoic in commit")).toBeNull();
  });
});

describe("PromptsCard pool editor entry", () => {
  const storage = { getItem: () => null, setItem: noop, removeItem: noop } as unknown as Storage;
  const props = {
    liveCustom: "", onLiveCustom: noop, onPickPool: noop, onSetCustom: noop, onShuffle: noop,
    onResetAuto: noop, captures: { intention: "", forMeegy: "", stoic: "" }, onCapture: noop,
    onAutosave: noop, onPersistCustom: noop,
    promptSave: { phase: "idle", error: null, warnings: [] }, customStorage: storage,
  };

  it("has no Edit pool control without the app store behind it", () => {
    const r = cardWithoutStore(micro());
    expect(r.queryByRole("button", { name: "Edit pool" })).toBeNull();
  });

  it("opens the pool editor from the card, loading the pool, and returns focus on close", async () => {
    const h = makeHarness("ready");
    const load = vi.spyOn(FixtureAdapter.prototype, "loadMicroPool");
    const r = h.ui(<PromptsCard micro={micro()} {...props} />);
    const open = r.getByRole("button", { name: "Edit pool" });
    expect(open.getAttribute("aria-haspopup")).toBe("dialog");
    open.focus();
    fireEvent.click(open);
    expect(r.getByRole("dialog", { name: "Micro-adventure ideas" })).toBeTruthy();
    expect(load).toHaveBeenCalledTimes(1);
    await waitFor(() => expect((r.getByLabelText("Idea 1") as HTMLInputElement).value).not.toBe(""));
    fireEvent.keyDown(r.getByRole("dialog"), { key: "Escape" });
    expect(r.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(open);
  });
});
