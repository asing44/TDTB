/* T19 — Live micro-adventure: wire projection, MICRO_SET reducer semantics,
   controller override flow, Live-label join, and the SetupDrawer controls. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

// globals:false → testing-library can't self-register its cleanup hook.
afterEach(cleanup);
import { makeHarness } from "./test-harness";
import { SetupDrawer } from "./SetupDrawer";
import { projectMicroAdventure } from "../adapters/wire";
import { isLiveName, liveDisplayName } from "../model/anchored";

describe("projectMicroAdventure", () => {
  it("degrades absent/partial wire to a plain-Live no-pick state", () => {
    for (const raw of [undefined, null, {}, { pick: { id: "x" } }]) {
      const m = projectMicroAdventure(raw as never);
      expect(m.pick).toBeNull();
      expect(m.source).toBe("auto");
      expect(m.pool).toEqual([]);
      expect(m.streak).toBe(0);
      expect(m.pendingConfirm).toBeNull();
    }
  });

  it("projects the full backend shape", () => {
    const m = projectMicroAdventure({
      pick: { id: "ma07", idea: "Watch sunset", category: "nature" },
      source: "override",
      live_pool: [
        { id: "ma07", idea: "Watch sunset", category: "nature" },
        { id: "bad" }, // malformed row dropped, never poisons the pool
      ],
      streak: 4,
      pending_confirm: { date: "2026-07-11", id: "ma03", idea: "Ride bike somewhere" },
    });
    expect(m.pick).toEqual({ id: "ma07", idea: "Watch sunset", category: "nature" });
    expect(m.source).toBe("override");
    expect(m.pool).toHaveLength(1);
    expect(m.streak).toBe(4);
    expect(m.pendingConfirm?.id).toBe("ma03");
  });
});

describe("projectMicroAdventure pool provenance", () => {
  it("defaults to config source and no warnings when fields are absent", () => {
    for (const raw of [undefined, {}, { pick: { id: "ma01", idea: "x" } }]) {
      const m = projectMicroAdventure(raw as never);
      expect(m.poolSource).toBe("config");
      expect(m.poolWarnings).toEqual([]);
    }
  });

  it("projects pool_source and pool_warnings strictly", () => {
    const m = projectMicroAdventure({
      pool_source: "config+capacities",
      pool_warnings: ["Capacities ideas were not used.", "", 7, "  second  "],
    } as never);
    expect(m.poolSource).toBe("config+capacities");
    expect(m.poolWarnings).toEqual(["Capacities ideas were not used.", "second"]);
    const bad = projectMicroAdventure({ pool_source: "elsewhere", pool_warnings: "nope" } as never);
    expect(bad.poolSource).toBe("config");
    expect(bad.poolWarnings).toEqual([]);
  });
});

describe("SetupDrawer pool warning note", () => {
  it("shows one note with the first warning and the full list in title/aria", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs.microAdventure = {
        ...sc.inputs.microAdventure,
        poolSource: "config",
        poolWarnings: ["First warning.", "Second warning."],
      };
    });
    h.store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const r = h.ui(<SetupDrawer />);
    const notes = r.container.querySelectorAll<HTMLElement>(".live-micro__pool-note");
    expect(notes).toHaveLength(1);
    expect(notes[0].textContent).toBe("First warning.");
    expect(notes[0].getAttribute("title")).toBe("First warning.\nSecond warning.");
    expect(notes[0].getAttribute("aria-label")).toBe("First warning. Second warning.");
  });

  it("renders nothing extra without warnings", () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const r = h.ui(<SetupDrawer />);
    expect(r.container.querySelector(".live-micro__pool-note")).toBeNull();
  });

  it("marks cap- options as Capacities", () => {
    const h = makeHarness("ready", (sc) => {
      const m = sc.inputs.microAdventure;
      sc.inputs.microAdventure = {
        ...m,
        pool: [...m.pool, { id: "cap-1", idea: "Try pottery", category: "craft" }],
      };
    });
    h.store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const r = h.ui(<SetupDrawer />);
    const opts = Array.from(r.container.querySelectorAll("#live-pick option")).map((o) => o.textContent);
    expect(opts).toContain("Try pottery · Capacities");
    expect(opts).toContain("Watch sunset");
  });
});

describe("liveDisplayName", () => {
  it("joins the pick onto Live blocks only", () => {
    const micro = { pick: { idea: "Watch sunset" } };
    expect(isLiveName("Live")).toBe(true);
    expect(isLiveName("⬜ Live")).toBe(true);
    expect(isLiveName("Lively")).toBe(false);
    expect(liveDisplayName("Live", micro)).toBe("Live · 🌱 Watch sunset");
    expect(liveDisplayName("Gym", micro)).toBe("Gym");
    expect(liveDisplayName("Live", { pick: null })).toBe("Live");
  });
});

describe("MICRO_SET reducer", () => {
  it("updates the pick and stales a current shadow (LD24 acceptance revoked)", () => {
    const h = makeHarness("commit-preview");
    expect(h.store.getState().shadowPhase).toBe("current");
    h.store.dispatch({
      type: "MICRO_SET",
      pick: { id: "ma02", idea: "Call a friend you haven't talked to in a while", category: "social" },
      source: "override",
    });
    const s = h.store.getState();
    expect(s.inputs?.microAdventure.pick?.id).toBe("ma02");
    expect(s.inputs?.microAdventure.source).toBe("override");
    expect(s.shadowPhase).not.toBe("current");
    expect(s.liveArmed).toBe(false);
    expect(s.acceptedDefects).toBeNull();
  });
});

describe("controller.setMicroAdventure", () => {
  it("persists then dispatches the override; null resets to the auto pool head", async () => {
    const h = makeHarness("ready");
    const { controller } = h as never as { controller: unknown };
    void controller;
    // harness exposes controller only through ui(); drive via a fresh render
    // is unnecessary — reach it from the harness store context instead.
    // makeHarness wires Controller with the FixtureAdapter (no-op saves).
    const ctx = h.ui(<div />);
    ctx.unmount();
    // Direct dispatch path is covered above; here exercise the public method.
    const { Controller } = await import("../store/controller");
    const { FixtureAdapter } = await import("../adapters/fixture");
    const c = new Controller(new FixtureAdapter("ready"), h.store.dispatch, h.store.getState);
    await c.setMicroAdventure({ id: "custom", idea: "Night swim", category: "custom" });
    expect(h.store.getState().inputs?.microAdventure).toMatchObject({
      pick: { id: "custom", idea: "Night swim" },
      source: "override",
    });
    await c.setMicroAdventure(null);
    const micro = h.store.getState().inputs!.microAdventure;
    expect(micro.source).toBe("auto");
    expect(micro.pick).toEqual(micro.pool[0]); // pool[0] is the LRU auto-pick
  });
});

describe("SetupDrawer Live section", () => {
  it("renders the pick, streak, and pool controls; Shuffle advances the pick", async () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const r = h.ui(<SetupDrawer />);
    expect(r.getByText(/Live micro-adventure/)).toBeTruthy();
    expect(r.getAllByText(/Watch sunset/).length).toBeGreaterThan(0);
    expect(r.getByText(/streak 3/)).toBeTruthy();
    const shuffle = r.getByRole("button", { name: "Shuffle" });
    fireEvent.click(shuffle);
    await new Promise((res) => setTimeout(res, 400)); // fixture latency
    const micro = h.store.getState().inputs!.microAdventure;
    expect(micro.pick?.id).toBe("ma02"); // next in pool after ma07
    expect(micro.source).toBe("override");
    expect(r.getByRole("button", { name: "Reset to auto" })).toBeTruthy();
  });

  it("custom idea input persists a custom override", async () => {
    const h = makeHarness("ready");
    h.store.dispatch({ type: "UI", patch: { setupOpen: true } });
    const r = h.ui(<SetupDrawer />);
    const input = r.getByLabelText("Custom idea") as HTMLInputElement;
    fireEvent.input(input, { target: { value: "Night swim" } });
    fireEvent.click(r.getByRole("button", { name: "Set custom" }));
    await new Promise((res) => setTimeout(res, 400));
    expect(h.store.getState().inputs!.microAdventure.pick).toEqual({
      id: "custom", idea: "Night swim", category: "custom",
    });
  });
});
