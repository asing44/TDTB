/* scratch.integration.test.ts — T7 gate: the production ApiAdapter +
   Controller driven against the REAL FastAPI routes on a scratch server
   (scripts/scratch_integration.py boots it on :8790 with a synthetic vault,
   canned judgment, dead write surfaces — nothing billed, nothing external).

   Self-skips unless TDTB_SCRATCH_URL is set, so the normal `vitest run`
   stays hermetic. Run via:  ../app/.venv/bin/python scripts/scratch_integration.py */

import { describe, expect, it, vi } from "vitest";
import { ApiAdapter } from "./api";
import { createStore } from "../store/createStore";
import { Controller } from "../store/controller";
import { acceptableDefects, canLiveCommit, canShadow } from "../store/store";
import type { SequenceRow } from "../model/types";

// tsconfig has no node types (browser-targeted app code); vitest provides
// process at runtime.
declare const process: { env: Record<string, string | undefined> };
const URL = process.env.TDTB_SCRATCH_URL ?? "";

describe.runIf(URL !== "")("scratch integration — real routes end to end", () => {
  it("load → shape → sequence → exact-placement error → revalidate → shadow → arm → live", async () => {
    const store = createStore();
    const adapter = new ApiAdapter(URL);
    const controller = new Controller(adapter, store.dispatch, store.getState);

    // -- load (free reads) --------------------------------------------------
    await controller.load();
    let s = store.getState();
    expect(s.loadError).toBeNull();
    expect(s.validDate).toBeTruthy();
    const items = s.inputs!.assigned;
    expect(items.length).toBeGreaterThan(0);

    // -- day setup persists server-side ------------------------------------
    await controller.saveDaySetup({
      ...s.daySetup,
      anchor: "07:30",
      eod: "23:00",
      confirmed: true,
    });
    expect(store.getState().daySetup.confirmed).toBe(true);

    // -- today-only shaping: exclude last item, 3-block override on first ---
    const excludedId = items[items.length - 1].id;
    const shapedId = items[0].id;
    controller.setOverride(excludedId, false, null);
    controller.setOverride(shapedId, true, 3);
    // capacity refresh is a real GET /capacity-preview
    await vi.waitFor(() => expect(store.getState().capacity).not.toBeNull());

    // -- the ONE billed action (canned judgment on scratch) -----------------
    await controller.autoSequence();
    s = store.getState();
    expect(s.seqPhase).toBe("valid");
    expect(s.fingerprint).toBeTruthy();
    const work = (s.sequence ?? []).filter((r) => r.kind === "work");
    expect(work.some((r) => r.id === excludedId)).toBe(false);
    // duration override carried into the sequence payload → 3 blocks = 90m
    const shaped = work.find((r) => r.id === shapedId)!;
    const mins = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3));
    expect(mins(shaped.end) - mins(shaped.start)).toBe(90);
    const ledgerAfterSeq = s.ledger!.spent;

    // -- exact-place the final row before earlier rows without reordering the wire
    // list → real /validate-sequence hard-errors chronological structure and
    // names the row in the quoted repr format -------------------------------
    const victim = work[work.length - 1];
    controller.moveRow(victim.id, "09:00");
    await vi.waitFor(() => {
      const v = store.getState().validation;
      expect(v).not.toBeNull();
      expect(v!.ok).toBe(false);
    });
    s = store.getState();
    // Server-style quoted repr is preserved for deterministic validation UI.
    expect(
      s.validation!.hardErrors.some((e) => e.includes(`'${victim.id}'`)),
    ).toBe(true);
    expect(s.seqPhase).toBe("dirty");
    expect(s.shadowPhase).toBe("none");

    // -- deterministic fix: move back, revalidation re-earns "valid" --------
    controller.moveRow(victim.id, victim.start);
    await vi.waitFor(() => {
      const st = store.getState();
      expect(st.validation?.ok).toBe(true);
      expect(st.seqPhase).toBe("valid");
    });

    // -- shadow preview (writes nothing) ------------------------------------
    await controller.shadowPreview();
    s = store.getState();
    expect(s.shadowPhase).toBe("current");
    expect(s.shadow).not.toBeNull();

    // -- edit-after-shadow staleness: a move disarms + stales the preview ---
    const plus15 = (t: string) => {
      const m = mins(t) + 15;
      return `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
    };
    controller.moveRow(victim.id, plus15(victim.start));
    await vi.waitFor(() => expect(store.getState().shadowPhase).toBe("stale"));
    controller.moveRow(victim.id, victim.start); // restore
    await vi.waitFor(() => expect(store.getState().seqPhase).toBe("valid"));
    await controller.shadowPreview();
    expect(store.getState().shadowPhase).toBe("current");

    // -- two-gate live commit against in-memory writers ---------------------
    expect(canLiveCommit(store.getState())).toBe(false); // not armed
    controller.armLive();
    expect(canLiveCommit(store.getState())).toBe(true);
    await controller.requestLiveCommit();
    s = store.getState();
    // Real orchestration + writer code, in-memory surfaces: a clean day
    // must land fully verified, and the calendar surface must have actually
    // written + reconciled the T22 anchored Step E events (2026-07-24:
    // dead (None, None) commit clients let the missing-anchored-publish gap
    // ship — the calendar path was never exercised end to end).
    expect(s.commitPhase).toBe("done");
    expect(s.commitReport).not.toBeNull();
    expect(
      s.commitReport!.surfaces.find((x) => x.system === "calendar")?.status,
    ).toBe("ok");
    expect(
      s.commitReport!.surfaces.find((x) => x.system === "todoist")?.status,
    ).toBe("ok");
    expect(s.commitReport!.verifyFailures).toEqual([]);

    // -- billed ledger only moved for the explicit sequence call ------------
    const ledger = await adapter.billedLedger();
    expect(ledger.spent).toBe(ledgerAfterSeq);
  }, 30_000);

  it("readFixedInputs is stable → fingerprint drift path stays quiet on an unchanged scratch day", async () => {
    const adapter = new ApiAdapter(URL);
    const a = await adapter.readFixedInputs();
    const b = await adapter.readFixedInputs();
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it("explicit source refresh over real routes: GETs only, ledger untouched, staged state survives an unchanged day", async () => {
    const store = createStore();
    const adapter = new ApiAdapter(URL);
    const controller = new Controller(adapter, store.dispatch, store.getState);
    await controller.load();
    await controller.saveDaySetup({ ...store.getState().daySetup, confirmed: true });
    await controller.autoSequence();
    let s = store.getState();
    expect(s.seqPhase).toBe("valid");
    const spentBefore = s.ledger!.spent;
    const stagedIds = (s.sequence ?? []).filter((r) => r.kind === "work").map((r) => r.id);

    // Prove the wire surface: refresh reaches only the two read endpoints.
    const realFetch = globalThis.fetch;
    const seen: Array<{ path: string; method: string }> = [];
    globalThis.fetch = (async (input: any, init?: RequestInit) => {
      const u = String(input);
      seen.push({
        path: u.replace(URL, "").split("?")[0],
        method: init?.method ?? "GET",
      });
      return realFetch(input, init);
    }) as typeof fetch;
    try {
      await controller.refreshSources();
    } finally {
      globalThis.fetch = realFetch;
    }
    // The refresh itself is the two GET reads; the only other traffic in the
    // window is the documented free follow-up (capacity GET + deterministic
    // /validate-sequence). Never /gather, never a billed or write endpoint.
    expect(seen.slice(0, 2)).toEqual([
      { path: "/plan-inputs", method: "GET" },
      { path: "/billed-ledger", method: "GET" },
    ]);
    const allowed = new Set(["/plan-inputs", "/billed-ledger", "/capacity-preview", "/validate-sequence", "/session-token"]);
    expect(seen.filter((c) => !allowed.has(c.path))).toEqual([]);
    for (const banned of ["/gather", "/sequence", "/adjust", "/commit"]) {
      expect(seen.some((c) => c.path === banned)).toBe(false);
    }

    s = store.getState();
    expect(s.refresh.error).toBeNull();
    expect(s.refresh.lastRefreshed).not.toBeNull();
    // Unchanged scratch day (frozen clock): no drift, no billed movement,
    // compatible placements preserved.
    expect(s.driftNotice).toBeNull();
    expect(s.ledger!.spent).toBe(spentBefore);
    const afterIds = (s.sequence ?? []).filter((r) => r.kind === "work").map((r) => r.id);
    expect(afterIds).toEqual(stagedIds);
    // Deterministic revalidation follow-up re-earns "valid" (free endpoint).
    await vi.waitFor(() => {
      expect(store.getState().seqPhase).toBe("valid");
    });
  }, 30_000);

  it("good-enough override over real routes: server soft warning gates, accept unblocks, hard errors never do (LD 24)", async () => {
    const store = createStore();
    const adapter = new ApiAdapter(URL);
    const controller = new Controller(adapter, store.dispatch, store.getState);
    await controller.load();
    await controller.saveDaySetup({
      ...store.getState().daySetup,
      anchor: "07:30",
      eod: "23:00",
      confirmed: true,
    });
    await controller.autoSequence();
    let s = store.getState();
    expect(s.seqPhase).toBe("valid");
    const spentAfterSeq = s.ledger!.spent;
    const work = (s.sequence ?? []).filter((r) => r.kind === "work");
    const mins = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3));
    const lastByStart = [...work].sort((a, b) => mins(a.start) - mins(b.start)).pop()!;
    const dur = mins(lastByStart.end) - mins(lastByStart.start);
    const hhmm = (m: number) =>
      `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

    // -- exact-place the last row so it ends 23:30 — past the 23:00 effective EOD
    // but same-day → real /validate-sequence returns a SOFT past-EOD warning
    // as a {id, rule, detail} dict; the wire projects the human detail
    // verbatim (never "[object Object]") ------------------------------------
    controller.moveRow(lastByStart.id, hhmm(23 * 60 + 30 - dur));
    await vi.waitFor(() => {
      const v = store.getState().validation;
      expect(v).not.toBeNull();
      expect(v!.ok).toBe(true);
      expect(v!.warnings.length).toBeGreaterThan(0);
    });
    s = store.getState();
    expect(s.validation!.warnings.some((w) => w.includes("[object"))).toBe(false);
    expect(s.validation!.warnings.some((w) => /past EOD/i.test(w))).toBe(true);

    // -- acceptable defect gates preview by default; controller refuses too --
    expect(canShadow(s)).toBe(false);
    await controller.shadowPreview();
    expect(store.getState().shadowPhase).toBe("none");

    // -- explicit accept records verbatim findings and unblocks -------------
    store.dispatch({ type: "ACCEPT_DEFECTS" });
    s = store.getState();
    expect(s.acceptedDefects).toEqual(acceptableDefects(s));
    expect(canShadow(s)).toBe(true);
    await controller.shadowPreview();
    expect(store.getState().shadowPhase).toBe("current");
    store.dispatch({ type: "ARM_LIVE" });
    expect(canLiveCommit(store.getState())).toBe(true);

    // -- a subsequent edit revokes acceptance and re-gates ------------------
    controller.moveRow(lastByStart.id, hhmm(23 * 60 + 15 - dur));
    await vi.waitFor(() => expect(store.getState().validation).not.toBeNull());
    s = store.getState();
    expect(s.acceptedDefects).toBeNull();
    expect(s.shadowPhase).toBe("stale");
    expect(canLiveCommit(s)).toBe(false);

    // -- hard errors are never acceptable: placement before the 07:30 anchor -
    controller.moveRow(lastByStart.id, "07:00");
    await vi.waitFor(() => {
      const v = store.getState().validation;
      expect(v).not.toBeNull();
      expect(v!.ok).toBe(false);
    });
    store.dispatch({ type: "ACCEPT_DEFECTS" });
    s = store.getState();
    expect(s.acceptedDefects).toBeNull(); // refused — hard blocker stands
    expect(canShadow(s)).toBe(false);

    // -- nothing in the accept flow is billed -------------------------------
    const ledger = await adapter.billedLedger();
    expect(ledger.spent).toBe(spentAfterSeq);
  }, 30_000);

  it("day-setup save persists weekend allotment free, then an explicit Send pays once", async () => {
    const store = createStore();
    const adapter = new ApiAdapter(URL);
    const controller = new Controller(adapter, store.dispatch, store.getState);
    await controller.load();
    const spentBefore = store.getState().ledger!.spent;

    const realFetch = globalThis.fetch;
    const seen: Array<{ path: string; method: string }> = [];
    globalThis.fetch = (async (input: any, init?: RequestInit) => {
      const u = String(input);
      seen.push({
        path: u.replace(URL, "").split("?")[0],
        method: init?.method ?? "GET",
      });
      return realFetch(input, init);
    }) as typeof fetch;
    try {
      // T12 qualification: the save itself must reach /day-setup and
      // /capacity-preview only. The paid /sequence is a separate, explicit
      // user action from the action dock.
      await controller.saveDaySetup({
        ...store.getState().daySetup,
        dayPreset: "Weekend",
        workAllotmentMinutes: 60,
      });
      expect(seen.filter((call) => call.path === "/sequence")).toHaveLength(0);
      await controller.autoSequence();
    } finally {
      globalThis.fetch = realFetch;
    }

    const writes = seen.filter((call) => call.method === "POST");
    expect(writes.map((call) => call.path)).toEqual(["/day-setup", "/sequence"]);
    expect(seen.filter((call) => call.path === "/sequence")).toHaveLength(1);
    expect(seen.findIndex((call) => call.path === "/day-setup")).toBeLessThan(
      seen.findIndex((call) => call.path === "/capacity-preview"),
    );
    expect(seen.findIndex((call) => call.path === "/capacity-preview")).toBeLessThan(
      seen.findIndex((call) => call.path === "/sequence"),
    );

    const inputs = await adapter.loadPlanInputs();
    expect(inputs.daySetup.dayPreset).toBe("Weekend");
    expect(inputs.daySetup.workAllotmentMinutes).toBe(60);
    expect(inputs.daySemantics.selectedPreset?.name).toBe("Weekend");
    expect(inputs.daySemantics.effectiveAllotmentMinutes).toBe(60);
    expect(inputs.daySemantics.mintEnabled).toBe(true);
    expect(
      store.getState().seqPhase,
      store.getState().seqError ?? "regeneration did not become valid",
    ).toBe("valid");
    // The route was exercised exactly once, but the harness-level canned
    // judgment never calls RunContext, so no billed ledger slot is consumed.
    expect(store.getState().ledger!.spent).toBe(spentBefore);
  }, 30_000);

  it("validateSequence rejects an invalid interval with the quoted repr format", async () => {
    const adapter = new ApiAdapter(URL);
    const store = createStore();
    const controller = new Controller(adapter, store.dispatch, store.getState);
    await controller.load();
    const item = store.getState().inputs!.assigned[0];
    const rows: SequenceRow[] = [
      { id: item.id, start: "10:00", end: "09:30", zone: null, kind: "work" },
    ];
    const v = await adapter.validateSequence(rows, {
      included: [{ id: item.id, blocks: 1 }],
    });
    expect(v.ok).toBe(false);
    expect(v.hardErrors.some((e) => e.includes(`'${item.id}'`))).toBe(true);
  });
});
