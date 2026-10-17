/* PromptsCard.test.tsx: the Day Setup Live micro-adventure card shows the same
   pool provenance note and Capacities option suffix as the settings drawer. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/preact";

import type { MicroAdventure } from "../../model/types";
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
