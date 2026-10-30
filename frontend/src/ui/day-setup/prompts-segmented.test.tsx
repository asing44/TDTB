/* prompts-segmented.test.tsx: the Impromptu | Write it control on the Morning
   prompts cards. For Meegy and Stoic map it onto the saved skip flag; Reminder
   has no flag, so the control is presentational and only the typed text moves. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, within } from "@testing-library/preact";
import { useState } from "preact/hooks";

import type { MicroAdventure } from "../../model/types";
import { makeHarness } from "../test-harness";
import { PromptsCard } from "./PromptsCard";

afterEach(cleanup);

const noop = () => {};
const storage = { getItem: () => null, setItem: noop, removeItem: noop } as unknown as Storage;
const MICRO = {
  pick: { id: "ma01", idea: "Watch sunset", category: "nature" },
  pool: [{ id: "ma01", idea: "Watch sunset", category: "nature" }],
  streak: 0,
  source: "auto",
  poolSource: "config",
  poolWarnings: [],
} as unknown as MicroAdventure;

/** Holds the capture drafts so typing into Reminder really changes the props. */
function Card(props: { intention?: string; onAutosave?: () => void }) {
  const [captures, setCaptures] = useState({
    intention: props.intention ?? "",
    forMeegy: "",
    stoic: "",
  });
  return (
    <PromptsCard
      micro={MICRO}
      liveCustom=""
      onLiveCustom={noop}
      onPickPool={noop}
      onSetCustom={noop}
      onShuffle={noop}
      onResetAuto={noop}
      captures={captures}
      onCapture={(key, value) => setCaptures({ ...captures, [key]: value })}
      onAutosave={props.onAutosave ?? noop}
      onPersistCustom={noop}
      promptSave={{ phase: "idle", error: null, warnings: [] }}
      customStorage={storage}
    />
  );
}

function seg(r: ReturnType<typeof render>, name: string) {
  const group = within(r.getByRole("group", { name: `${name} description` }));
  return {
    impromptu: group.getByRole("button", { name: "Impromptu" }),
    write: group.getByRole("button", { name: "Write it" }),
  };
}

describe("Meegy and Stoic segmented control", () => {
  it("Impromptu sets the skip flag and swaps the field for the default copy", async () => {
    const h = makeHarness("fresh");
    const save = vi.spyOn(h.controller, "savePromptSkips");
    const r = h.ui(<Card />);
    expect(r.getByLabelText("For Meegy")).toBeTruthy();
    fireEvent.click(seg(r, "For Meegy").impromptu);
    await vi.waitFor(() => {
      expect(save).toHaveBeenLastCalledWith({ forMeegy: true, stoic: false });
    });
    await vi.waitFor(() => expect(r.queryByLabelText("For Meegy")).toBeNull());
    expect(r.getAllByText("Impromptu \u2014 left as the default")).toHaveLength(2);
    expect(h.store.getState().daySetup.descriptionSkips).toEqual({ forMeegy: true, stoic: false });
  });

  it("Write it clears the flag and shows the textarea again", async () => {
    const h = makeHarness("fresh");
    const save = vi.spyOn(h.controller, "savePromptSkips");
    const r = h.ui(<Card />);
    fireEvent.click(seg(r, "Stoic").impromptu);
    await vi.waitFor(() => expect(r.queryByLabelText("Stoic")).toBeNull());
    fireEvent.click(seg(r, "Stoic").write);
    await vi.waitFor(() => {
      expect(save).toHaveBeenLastCalledWith({ forMeegy: false, stoic: false });
    });
    await vi.waitFor(() => expect(r.getByLabelText("Stoic")).toBeTruthy());
    expect(h.store.getState().daySetup.descriptionSkips).toEqual({ forMeegy: false, stoic: false });
  });

  it("the include switch still works and keeps its accessible name", async () => {
    const h = makeHarness("fresh");
    const save = vi.spyOn(h.controller, "savePromptDisabled");
    const r = h.ui(<Card />);
    fireEvent.click(r.getByRole("switch", { name: "Include Stoic in commit" }));
    await vi.waitFor(() => {
      expect(save).toHaveBeenLastCalledWith({ forMeegy: false, stoic: true });
    });
    await vi.waitFor(() => expect(seg(r, "Stoic").impromptu.hasAttribute("disabled")).toBe(true));
    // The draft survives while the prompt is off.
    expect(r.getByLabelText("Stoic")).toBeTruthy();
  });
});

describe("Reminder segmented control", () => {
  it("starts on Impromptu with no draft and carries no saved flag", () => {
    const h = makeHarness("fresh");
    const save = vi.spyOn(h.controller, "savePromptSkips");
    const r = h.ui(<Card />);
    expect(seg(r, "Reminder").impromptu.getAttribute("aria-pressed")).toBe("true");
    expect(r.queryByLabelText("Reminder")).toBeNull();
    fireEvent.click(seg(r, "Reminder").write);
    expect(r.getByLabelText("Reminder")).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
  });

  it("starts on Write it when a draft exists", () => {
    const r = makeHarness("fresh").ui(<Card intention="Call the vet" />);
    expect(seg(r, "Reminder").write.getAttribute("aria-pressed")).toBe("true");
    expect((r.getByLabelText("Reminder") as HTMLTextAreaElement).value).toBe("Call the vet");
  });

  it("Impromptu clears the typed text and autosaves", () => {
    const autosave = vi.fn();
    const r = makeHarness("fresh").ui(<Card intention="Call the vet" onAutosave={autosave} />);
    fireEvent.click(seg(r, "Reminder").impromptu);
    expect(r.queryByLabelText("Reminder")).toBeNull();
    expect(autosave).toHaveBeenCalledTimes(1);
    fireEvent.click(seg(r, "Reminder").write);
    expect((r.getByLabelText("Reminder") as HTMLTextAreaElement).value).toBe("");
  });
});
