/* overlap-note.test.tsx — 04-overlap-note (2026-10-16). TDD for the visible
   per-row grant note on the Plan tasks ("Today's work") rows: a row that
   shares time through an EXPLICIT overlap grant names its partner and reason;
   a row that merely abuts or collides in time WITHOUT a grant shows nothing.
   Fixture/wire projections only — no billed call, no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup } from "@testing-library/preact";

afterEach(cleanup);

import { Queue } from "./Queue";
import { makeHarness, type Harness } from "./test-harness";
import { projectSequenceResult, type Wire } from "../adapters/wire";

function rowNamed(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

/** Stage a sequence from a raw /sequence wire payload — the same projection
    the adapters use — with a current planning-config fingerprint. */
function stageFromWire(h: Harness, wire: Wire, planningConfigFingerprint = "pcfp"): void {
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

const grant = (
  primaryId: string,
  companionId: string,
  primaryInterval: { start: string; end: string },
  companionInterval: { start: string; end: string },
  reason: string,
) => ({
  primary_id: primaryId,
  companion_id: companionId,
  primary_interval: primaryInterval,
  companion_interval: companionInterval,
  reason,
  planning_config_fingerprint: "pcfp",
});

describe("overlap grant note on plan rows", () => {
  it("(a) names the partner and the grant reason on both rows", () => {
    const h = makeHarness("ready");
    stageFromWire(h, {
      sequence: [
        { id: "Magic Mirror", start: "09:00", end: "10:00" },
        { id: "Press", start: "09:30", end: "10:30" },
      ],
      warnings: [],
      overlap_grants: [
        grant("Magic Mirror", "Press", { start: "09:00", end: "10:00" }, { start: "09:30", end: "10:30" }, "bundled with Press"),
      ],
    });
    const { container } = h.ui(<Queue />);

    const mirror = rowNamed(container, "Magic Mirror").querySelector(".qrow__overlap")!;
    expect(mirror.textContent).toBe("Shares time with Press: bundled with Press");
    expect(mirror.getAttribute("title")).toContain("bundled with Press");

    const press = rowNamed(container, "Press").querySelector(".qrow__overlap")!;
    expect(press.textContent).toBe("Shares time with Magic Mirror: bundled with Press");
  });

  it("(b) a row that collides or abuts in time WITHOUT a grant shows no note", () => {
    const h = makeHarness("ready");
    stageFromWire(h, {
      sequence: [
        { id: "Magic Mirror", start: "09:00", end: "10:00" },
        { id: "Press", start: "09:30", end: "10:00" },
        { id: "Note Processing", start: "10:00", end: "10:30" },
      ],
      warnings: [],
      overlap_grants: [],
    });
    const { container } = h.ui(<Queue />);

    expect(rowNamed(container, "Magic Mirror").querySelector(".qrow__overlap")).toBeNull();
    expect(rowNamed(container, "Press").querySelector(".qrow__overlap")).toBeNull();
    // Note Processing only abuts Magic Mirror's end — no note either.
    expect(rowNamed(container, "Note Processing").querySelector(".qrow__overlap")).toBeNull();
  });

  it("(c) the wire projection carries the grant into the row", () => {
    const projected = projectSequenceResult({
      sequence: [],
      warnings: [],
      overlap_grants: [
        grant("Magic Mirror", "Press", { start: "09:00", end: "10:00" }, { start: "09:30", end: "10:30" }, "wire reason"),
      ],
    });
    expect(projected.overlapGrants).toHaveLength(1);
    expect(projected.overlapGrants[0]).toMatchObject({
      primaryId: "Magic Mirror",
      companionId: "Press",
      reason: "wire reason",
    });

    const h = makeHarness("ready");
    stageFromWire(h, {
      sequence: [
        { id: "Magic Mirror", start: "09:00", end: "10:00" },
        { id: "Press", start: "09:30", end: "10:30" },
      ],
      warnings: [],
      overlap_grants: [
        grant("Magic Mirror", "Press", { start: "09:00", end: "10:00" }, { start: "09:30", end: "10:30" }, "wire reason"),
      ],
    });
    const { container } = h.ui(<Queue />);
    expect(rowNamed(container, "Magic Mirror").textContent).toContain(
      "Shares time with Press: wire reason",
    );
  });

  it("(d) multiple grants on one row each render", () => {
    const h = makeHarness("ready");
    stageFromWire(h, {
      sequence: [
        { id: "Magic Mirror", start: "09:00", end: "10:00" },
        { id: "Press", start: "09:30", end: "10:30" },
        { id: "Rowe's T-shirt Redesign 2026", start: "09:15", end: "09:45" },
      ],
      warnings: [],
      overlap_grants: [
        grant("Magic Mirror", "Press", { start: "09:00", end: "10:00" }, { start: "09:30", end: "10:30" }, "bundled with Press"),
        grant("Magic Mirror", "Rowe's T-shirt Redesign 2026", { start: "09:00", end: "10:00" }, { start: "09:15", end: "09:45" }, "bundled with Rowe"),
      ],
    });
    const { container } = h.ui(<Queue />);

    // Sorted row order (by start, then id): Rowe's … (09:15), Magic Mirror
    // (09:00) — but Magic Mirror still carries both notes.
    const mirror = rowNamed(container, "Magic Mirror");
    const notes = Array.from(mirror.querySelectorAll(".qrow__overlap")).map((n) => n.textContent);
    expect(notes).toHaveLength(2);
    expect(notes.join(" | ")).toContain("Press: bundled with Press");
    expect(notes.join(" | ")).toContain("Rowe's T-shirt Redesign 2026: bundled with Rowe");
  });

  it("(e) a grant naming a row that is not on the plan degrades to no note", () => {
    const h = makeHarness("ready");
    stageFromWire(h, {
      sequence: [{ id: "Magic Mirror", start: "09:00", end: "10:00" }],
      warnings: [],
      overlap_grants: [
        grant("Magic Mirror", "ghost row", { start: "09:00", end: "10:00" }, { start: "09:30", end: "10:30" }, "ghost reason"),
      ],
    });
    const { container } = h.ui(<Queue />);
    expect(rowNamed(container, "Magic Mirror").querySelector(".qrow__overlap")).toBeNull();
  });
});
