/* PromptsCard.test.tsx: the Day Setup Live micro-adventure card shows the same
   pool provenance note and Capacities option suffix as the settings drawer. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/preact";

import type { MicroAdventure } from "../../model/types";
import { FixtureAdapter } from "../../adapters/fixture";
import { makeHarness } from "../test-harness";
import { PromptsCard } from "./PromptsCard";

afterEach(cleanup);

const noop = () => {};

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

function card(m: MicroAdventure) {
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

describe("PromptsCard pool note", () => {
  it("shows one note with the first warning and the full list in title/aria", () => {
    const r = card(micro({ poolWarnings: ["First warning.", "Second warning."] }));
    const notes = r.container.querySelectorAll<HTMLElement>(".live-micro__pool-note");
    expect(notes).toHaveLength(1);
    expect(notes[0].getAttribute("role")).toBe("note");
    expect(notes[0].className).toContain("field__hint");
    expect(notes[0].textContent).toBe("First warning.");
    expect(notes[0].getAttribute("title")).toBe("First warning.\nSecond warning.");
    expect(notes[0].getAttribute("aria-label")).toBe("First warning. Second warning.");
  });

  it("renders no note without warnings", () => {
    const r = card(micro());
    expect(r.container.querySelector(".live-micro__pool-note")).toBeNull();
  });

  it("marks cap- options as Capacities", () => {
    const r = card(micro());
    const opts = Array.from(r.container.querySelectorAll("#live-pick option")).map(
      (o) => o.textContent,
    );
    expect(opts).toEqual(["Watch sunset", "Try pottery · Capacities"]);
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
    const r = card(micro());
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
