/* mint-overlap-toggle.test.tsx — GitHub issue asing44/TDTB#18, brief B3.
   Part 1 is the missing UI test for the per-row "May overlap Mint" grant
   toggle that landed in B2: it renders as a menuitemcheckbox in the row's More
   menu, flips the controller grant, and is disabled with a spelled-out reason
   on rows the grant cannot apply to. Part 2 covers the visible note: a row
   granted the operator overlap shows the same .qrow__overlap note #16 uses,
   naming the Mint session by its raw row id (a Mint row is not an assigned
   item, so the assigned-name fallback resolves to the id).

   Fixture adapters only: no live adapter, no billed call, no source write.
   The billed-path test spies the adapter's /sequence and /commit methods and
   requires zero calls. The frontend carries no /adjust affordance at all
   (static-UI guard), so there is no adjust method to spy. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness, type Harness } from "./test-harness";
import { projectSequenceResult, type Wire } from "../adapters/wire";

function rowFor(container: Element, name: string): HTMLElement {
  const rows = Array.from(container.querySelectorAll<HTMLElement>(".qrow"));
  const row = rows.find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

function openMore(container: Element, name: string): HTMLElement {
  const row = rowFor(container, name);
  const trigger = row.querySelector<HTMLButtonElement>(
    'button[aria-haspopup="menu"]',
  );
  if (!trigger) throw new Error("no More trigger on the row");
  fireEvent.click(trigger);
  const menu = row.querySelector<HTMLElement>('[role="menu"]');
  if (!menu) throw new Error("More menu is not open");
  return menu;
}

function itemByLabel(menu: Element, label: string): HTMLButtonElement | null {
  return menu.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
}

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

describe("#18 May overlap Mint toggle in the More menu", () => {
  it("(a) renders off by default and toggles on through the controller", () => {
    const h = makeHarness("ready");
    const spy = vi.spyOn(h.controller, "setMintOverlapOptIn");
    const { container } = h.ui(<Queue />);

    const menu = openMore(container, "Press");
    const item = itemByLabel(menu, "May overlap Mint for Press");
    expect(item).not.toBeNull();
    expect(item!.getAttribute("role")).toBe("menuitemcheckbox");
    expect(item!.getAttribute("aria-checked")).toBe("false");
    expect(item!.textContent).toContain("May overlap Mint");

    fireEvent.click(item!);

    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith("Press", true);
    const after = itemByLabel(menu, "May overlap Mint for Press");
    expect(after!.getAttribute("aria-checked")).toBe("true");
    expect(after!.textContent).toContain("Stop overlapping Mint");
  });

  it("(b) is disabled with the reason for an all-day row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const item = itemByLabel(
      openMore(container, "Charge GoPro"),
      "May overlap Mint for Charge GoPro",
    );
    expect(item).not.toBeNull();
    expect(item!.disabled).toBe(true);
    expect(item!.getAttribute("title")).toBe("All-day rows have no time to overlap");
    expect(item!.textContent).toContain("All-day rows have no time to overlap");
  });

  it("(b) is disabled with the reason for an excluded row", () => {
    const h = makeHarness("ready");
    h.store.dispatch({
      type: "OVERRIDE_SET",
      id: "Press",
      override: { included: false, blocks: null },
    });
    const { container } = h.ui(<Queue />);
    // An excluded row lives in the collapsed "Out today" lane; expand it first.
    fireEvent.click(container.querySelector(".band--drop") as HTMLButtonElement);
    const item = itemByLabel(
      openMore(container, "Press"),
      "May overlap Mint for Press",
    );
    expect(item).not.toBeNull();
    expect(item!.disabled).toBe(true);
    expect(item!.getAttribute("title")).toBe("Excluded today — include the row first");
  });

  it("(b) is disabled with the reason for a row named Mint …", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs.assigned.push({
        id: "Mint Afternoon",
        name: "Mint Afternoon",
        path: null,
        source: "todoist",
        types: ["task"],
        urgency: null,
        deadline: null,
        priorityScore: 10,
        blocks: 2,
        durationLabel: "1hr",
        todoistId: "6fx00xMINT",
      });
    });
    const { container } = h.ui(<Queue />);
    const item = itemByLabel(
      openMore(container, "Mint Afternoon"),
      "May overlap Mint for Mint Afternoon",
    );
    expect(item).not.toBeNull();
    expect(item!.disabled).toBe(true);
    expect(item!.getAttribute("title")).toBe("A Mint session is the wall itself");
  });

  it("(c) toggling fires no billed or write adapter call", async () => {
    const h = makeHarness("ready");
    const sequence = vi.spyOn(h.adapter, "autoSequence");
    const live = vi.spyOn(h.adapter, "liveCommit");
    const shadow = vi.spyOn(h.adapter, "shadowCommit");
    const { container } = h.ui(<Queue />);

    fireEvent.click(
      itemByLabel(openMore(container, "Press"), "May overlap Mint for Press")!,
    );
    await new Promise((resolve) => setTimeout(resolve, 150));

    expect(sequence).not.toHaveBeenCalled();
    expect(live).not.toHaveBeenCalled();
    expect(shadow).not.toHaveBeenCalled();
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
