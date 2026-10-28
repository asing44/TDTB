/* mint-overlap-toggle.test.tsx — GitHub issue asing44/TDTB#18, brief B3.
   Part 1 is the per-row "May overlap Mint" grant: after the 2026-10-28 row
   redesign it is a permanent aria-pressed toggle button on the COLLAPSED row
   (the expanded panel no longer carries it) and it applies immediately through
   controller.applyRowEdit. It is disabled with a spelled-out reason on rows the
   grant cannot apply to. Part 2 covers the visible note: a row granted the
   operator overlap shows the same .qrow__overlap note #16 uses, naming the
   Mint session by its raw row id (a Mint row is not an assigned item, so the
   assigned-name fallback resolves to the id).

   Fixture adapters only: no live adapter, no billed call, no source write.
   The billed-path test spies the adapter's /sequence and /commit methods and
   requires zero calls. The frontend carries no /adjust affordance at all
   (static-UI guard), so there is no adjust method to spy. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness, type Harness } from "./test-harness";
import { EXCLUSION_REASONS } from "../model/batchEligibility";
import { projectSequenceResult, type Wire } from "../adapters/wire";

function rowFor(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

/** Expand a row and return its refreshed element. */
function expand(container: Element, name: string): HTMLElement {
  fireEvent.click(rowFor(container, name).querySelector('button[aria-label="Expand"]')!);
  return rowFor(container, name);
}

/** The "May overlap Mint" toggle on the COLLAPSED row. */
const mint = (row: Element) =>
  row.querySelector<HTMLButtonElement>('button[aria-label^="May overlap Mint:"]')!;

/** The written reason a blocked toggle carries as its title. */
const mintReason = (row: Element) => mint(row).getAttribute("title");

const allDayToggle = (row: Element) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === "All-day",
  )!;

/** Stage a sequence from a raw /sequence wire payload — the same projection
    the adapters use — with a current planning-config fingerprint. */
function stageFromWire(
  h: Harness,
  wire: Wire,
  planningConfigFingerprint = "pcfp",
): void {
  const projected = projectSequenceResult(wire);
  const s = h.store.getState();
  h.store.dispatch({
    type: "SEQUENCE_OK",
    sequence: projected.sequence,
    warnings: projected.warnings,
    overlapGrants: projected.overlapGrants,
    fingerprint: "fp",
    anchoredSourceFingerprint: s.inputs!.anchoredSourceFingerprint,
    planningConfigFingerprint,
    ledger: s.ledger!,
  });
}

describe("#18 May overlap Mint toggle on the collapsed row", () => {
  it("(a) renders off by default and applies at once, with no Save step", () => {
    const h = makeHarness("ready");
    const spy = vi.spyOn(h.controller, "setMintOverlapOptIn");
    const { container } = h.ui(<Queue />);

    const row = rowFor(container, "Press");
    const button = mint(row);
    expect(button.tagName).toBe("BUTTON");
    expect(button.getAttribute("aria-pressed")).toBe("false");
    expect(button.disabled).toBe(false);
    expect(button.getAttribute("aria-label")).toBe("May overlap Mint: Press");
    expect(button.textContent?.trim()).toBe("Mint");
    expect(mintReason(row)).toContain("off");

    fireEvent.click(button);
    // Applied immediately: one local opt-in, no staging, no Save.
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith("Press", true);
    expect(h.store.getState().mintOverlapOptIns["Press"]).toBe(true);
    const after = rowFor(container, "Press");
    expect(mint(after).getAttribute("aria-pressed")).toBe("true");
    expect(h.store.getState().drafts["Press"]).toBeUndefined();
  });

  it("(a2) presses off to withdraw the grant", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    fireEvent.click(mint(rowFor(container, "Press")));
    expect(h.store.getState().mintOverlapOptIns["Press"]).toBe(true);

    fireEvent.click(mint(rowFor(container, "Press")));
    expect(h.store.getState().mintOverlapOptIns["Press"]).toBeFalsy();
    expect(mint(rowFor(container, "Press")).getAttribute("aria-pressed")).toBe("false");
  });

  it("(b) is disabled with the written reason for an all-day row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowFor(container, "Charge GoPro");
    expect(mint(row).disabled).toBe(true);
    expect(mint(row).getAttribute("aria-pressed")).toBe("false");
    expect(mintReason(row)).toBe(EXCLUSION_REASONS.mintAllDay);
  });

  it("(b) is disabled with the written reason once the row goes all-day", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    expect(mint(row).disabled).toBe(false);
    fireEvent.click(allDayToggle(row));
    row = rowFor(container, "Press");
    expect(mint(row).disabled).toBe(true);
    expect(mintReason(row)).toBe(EXCLUSION_REASONS.mintAllDay);
  });

  it("(c) applying the grant fires no billed or write adapter call", async () => {
    const h = makeHarness("ready");
    const sequence = vi.spyOn(h.adapter, "autoSequence");
    const live = vi.spyOn(h.adapter, "liveCommit");
    const shadow = vi.spyOn(h.adapter, "shadowCommit");
    const batch = vi.spyOn(h.adapter, "applyRuntimeBatch");
    const { container } = h.ui(<Queue />);

    fireEvent.click(mint(rowFor(container, "Press")));
    await new Promise((resolve) => setTimeout(resolve, 150));

    expect(sequence).not.toHaveBeenCalled();
    expect(live).not.toHaveBeenCalled();
    expect(shadow).not.toHaveBeenCalled();
    expect(batch).not.toHaveBeenCalled();
  });

  it("(d) is not duplicated inside the expanded panel", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Press");
    const toggles = row.querySelectorAll('button[aria-label^="May overlap Mint:"]');
    expect(toggles.length).toBe(1);
    expect(row.querySelector(".trow__panel")?.contains(toggles[0])).toBe(false);
  });
});

describe("#18 May overlap Mint note on plan rows", () => {
  const MINT_WIRE = [
    { id: "Press", start: "15:15", end: "16:15" },
    { id: "Mint Afternoon", start: "15:00", end: "17:00", mint_session: true },
  ];

  it("(vii) shows the note on the granted row, naming the Mint partner", () => {
    const h = makeHarness("ready");
    stageFromWire(h, { sequence: MINT_WIRE, warnings: [], overlap_grants: [] });
    h.store.dispatch({ type: "MINT_OVERLAP_SET", id: "Press", allow: true });
    const { container } = h.ui(<Queue />);

    const note = rowFor(container, "Press").querySelector(".qrow__overlap")!;
    expect(note).not.toBeNull();
    expect(note.getAttribute("role")).toBe("note");
    expect(note.textContent).toBe(
      "Shares time with Mint Afternoon: May overlap Mint (operator)",
    );
    expect(note.getAttribute("title")).toContain("May overlap Mint (operator)");
  });

  it("(viii) shows no note when the grant is off", () => {
    const h = makeHarness("ready");
    stageFromWire(h, { sequence: MINT_WIRE, warnings: [], overlap_grants: [] });
    const { container } = h.ui(<Queue />);

    expect(rowFor(container, "Press").querySelector(".qrow__overlap")).toBeNull();
  });
});
