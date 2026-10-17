/* mint-overlap-toggle.test.tsx — GitHub issue asing44/TDTB#18, brief B3.
   Part 1 is the per-row "May overlap Mint" grant toggle: it renders on the
   expanded row's line 1 (RowExpanded), stages on the row draft, and commits
   only through Save. It is disabled with a spelled-out reason on rows the
   grant cannot apply to. Part 2 covers the visible note: a row granted the
   operator overlap shows the same .qrow__overlap note #16 uses, naming the
   Mint session by its raw row id (a Mint row is not an assigned item, so the
   assigned-name fallback resolves to the id).

   RE-HOMED 2026-10-16 (task-actions F7): the toggle used to be a
   menuitemcheckbox inside the retired per-row More menu, which applied it
   immediately. It is now an inline checkbox whose change is staged and whose
   commit is Save, so the assertions below track the staging/commit split. Two
   retired variants are recorded in design/task-actions/retired-tests.md: the
   excluded-row variant (the expanded panel has no exclusion gate) and the
   "row named Mint …" variant (row-expanded.test.tsx pins the same Mint-session
   reason through daySemantics.mintSessions).

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

/** The "May overlap Mint" checkbox on the expanded row's line 1. */
const mint = (row: Element) =>
  Array.from(row.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')).find(
    (i) => i.closest("label")?.textContent?.includes("May overlap Mint"),
  )!;

/** The written reason the checkbox points at with aria-describedby. */
function mintReason(row: Element): string | null {
  const id = mint(row).getAttribute("aria-describedby");
  if (!id) return null;
  return row.querySelector(`#${id}`)?.textContent ?? null;
}

const btn = (row: Element, text: string) =>
  Array.from(row.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === text,
  ) ?? null;

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

describe("#18 May overlap Mint toggle on the expanded row", () => {
  it("(a) renders off by default and commits on Save only", () => {
    const h = makeHarness("ready");
    const spy = vi.spyOn(h.controller, "setMintOverlapOptIn");
    const { container } = h.ui(<Queue />);

    let row = expand(container, "Press");
    const box = mint(row);
    expect(box.checked).toBe(false);
    expect(box.disabled).toBe(false);
    expect(mintReason(row)).toBeNull();
    expect(row.textContent).toContain("May overlap Mint");

    fireEvent.click(box);
    // Staging is local: no grant reaches the controller before Save.
    expect(spy).not.toHaveBeenCalled();
    expect(h.store.getState().drafts["Press"].mintOverlap).toBe(true);

    row = rowFor(container, "Press");
    fireEvent.click(btn(row, "Save")!);

    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith("Press", true);
    expect(h.store.getState().mintOverlapOptIns["Press"]).toBe(true);
    expect(mint(rowFor(container, "Press")).checked).toBe(true);
  });

  it("(b) is disabled with the written reason for an all-day row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = expand(container, "Charge GoPro");
    expect(mint(row).disabled).toBe(true);
    expect(mintReason(row)).toBe(EXCLUSION_REASONS.mintAllDay);
  });

  it("(b) is disabled with the written reason for an all-day draft", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    let row = expand(container, "Press");
    expect(mint(row).disabled).toBe(false);
    fireEvent.click(
      row.querySelector<HTMLInputElement>('input[type="radio"][value="allday"]')!,
    );
    row = rowFor(container, "Press");
    expect(mint(row).disabled).toBe(true);
    expect(mintReason(row)).toBe(EXCLUSION_REASONS.mintAllDay);
  });

  it("(c) staging and saving fire no billed or write adapter call", async () => {
    const h = makeHarness("ready");
    const sequence = vi.spyOn(h.adapter, "autoSequence");
    const live = vi.spyOn(h.adapter, "liveCommit");
    const shadow = vi.spyOn(h.adapter, "shadowCommit");
    const batch = vi.spyOn(h.adapter, "applyRuntimeBatch");
    const { container } = h.ui(<Queue />);

    const row = expand(container, "Press");
    fireEvent.click(mint(row));
    fireEvent.click(btn(rowFor(container, "Press"), "Save")!);
    await new Promise((resolve) => setTimeout(resolve, 150));

    expect(sequence).not.toHaveBeenCalled();
    expect(live).not.toHaveBeenCalled();
    expect(shadow).not.toHaveBeenCalled();
    expect(batch).not.toHaveBeenCalled();
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
