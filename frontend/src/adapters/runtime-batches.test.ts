/* runtime-batches.test.ts — F1 typed surface for the B5 backend.

   Pins the batch adapter contract: POST shapes/paths, 409/422 error mapping
   through the shared ApiError path, wire projections for the new verbs and
   the dropped-today snapshot, and the fixture's deterministic partial
   failure / retry-only-failed / undo-conflict behavior.

   Api tests mock fetch; fixture tests are pure and offline. Nothing here
   touches a live endpoint, a source write, or the real runtime. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiAdapter, ApiError } from "./api";
import { FixtureAdapter } from "./fixture";
import {
  projectDroppedToday,
  projectRuntimeAction,
  projectRuntimeBatchResults,
  projectRuntimeBatchUndo,
} from "./wire";

interface Call {
  path: string;
  init?: RequestInit;
}

let calls: Call[];
let routes: Record<string, { status: number; body: unknown }>;

function route(path: string, body: unknown, status = 200): void {
  routes[path] = { status, body };
}

function postTo(path: string): Call {
  const call = calls.find((c) => c.path === path && c.init?.method === "POST");
  if (!call) throw new Error(`no POST to ${path}`);
  return call;
}

function bodyOf(call: Call): unknown {
  return JSON.parse(call.init!.body as string);
}

beforeEach(() => {
  calls = [];
  routes = {};
  route("/session-token", { token: "tok-123" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.split("?")[0];
      calls.push({ path: url, init });
      const r = routes[path];
      if (!r) return new Response("null", { status: 404 });
      return new Response(JSON.stringify(r.body), { status: r.status });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiAdapter runtime batches: request shapes", () => {
  it("POSTs {items:[{verb,target,args}]} to /runtime-batches", async () => {
    route("/runtime-batches", {
      batch_id: "rb-1",
      results: [
        { index: 0, verb: "done", target: "Press", status: "applied", error: null },
      ],
    });
    const adapter = new ApiAdapter("");
    const out = await adapter.applyRuntimeBatch([
      { verb: "done", target: "Press" },
      { verb: "hide_always", target: "Email", args: { always: true } },
    ]);

    expect(bodyOf(postTo("/runtime-batches"))).toEqual({
      items: [
        { verb: "done", target: "Press", args: {} },
        { verb: "hide_always", target: "Email", args: { always: true } },
      ],
    });
    expect(out.batchId).toBe("rb-1");
    expect(out.results).toEqual([
      { index: 0, verb: "done", target: "Press", status: "applied", error: null },
    ]);
  });

  it("POSTs an empty body to the retry subpath", async () => {
    route("/runtime-batches/rb%202/retry", { batch_id: "rb 2", results: [] });
    const adapter = new ApiAdapter("");
    await adapter.retryRuntimeBatch("rb 2");
    const call = postTo("/runtime-batches/rb%202/retry");
    expect(bodyOf(call)).toEqual({});
  });

  it("POSTs an empty body to the undo subpath", async () => {
    route("/runtime-batches/rb%203/undo", {
      batch_id: "rb 3",
      reverted: [],
      conflicts: [],
      non_reversible: [],
    });
    const adapter = new ApiAdapter("");
    const report = await adapter.undoRuntimeBatch("rb 3");
    expect(bodyOf(postTo("/runtime-batches/rb%203/undo"))).toEqual({});
    expect(report).toEqual({
      batchId: "rb 3",
      reverted: [],
      conflicts: [],
      nonReversible: [],
    });
  });
});

describe("ApiAdapter runtime batches: error mapping", () => {
  it("maps a 409 Day Setup refusal to ApiError(409) with the server detail", async () => {
    route(
      "/runtime-batches",
      { detail: "Day Setup not confirmed for 2026-10-16 — confirm Day Setup before applying runtime batches" },
      409,
    );
    const adapter = new ApiAdapter("");
    try {
      await adapter.applyRuntimeBatch([{ verb: "done", target: "Press" }]);
      throw new Error("expected a rejection");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      const err = e as ApiError;
      expect(err.status).toBe(409);
      expect(err.detail).toContain("Day Setup not confirmed");
      expect(err.message).toContain("Day Setup not confirmed");
      expect(err.path).toBe("/runtime-batches");
    }
  });

  it("maps a 422 bad-input / unknown batch to ApiError(422)", async () => {
    route("/runtime-batches/rb-404/retry", { detail: "unknown batch 'rb-404'" }, 422);
    const adapter = new ApiAdapter("");
    await expect(adapter.retryRuntimeBatch("rb-404")).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
    });
  });
});

describe("wire: batch result parsing", () => {
  it("keeps every closed status and the echoed target string", () => {
    const out = projectRuntimeBatchResults({
      batch_id: "rb-9",
      results: [
        { index: 0, verb: "done", target: "Press", status: "applied", error: null },
        {
          index: 1,
          verb: "restore_today",
          target: "Walk",
          status: "refused",
          error: "restore_today requires a dropped identity",
        },
        { index: 2, verb: "hide_always", target: "Email", status: "failed", error: "boom" },
        { index: 3, verb: "drop_from_plan", target: "Press", status: "duplicate", error: null },
      ],
    });

    expect(out.batchId).toBe("rb-9");
    expect(out.results.map((r) => r.status)).toEqual([
      "applied",
      "refused",
      "failed",
      "duplicate",
    ]);
    expect(out.results[1]).toEqual({
      index: 1,
      verb: "restore_today",
      target: "Walk",
      status: "refused",
      error: "restore_today requires a dropped identity",
    });
    expect(out.results[2].verb).toBe("hide_always");
  });

  it("treats an unknown status as a failure, never a silent success", () => {
    const out = projectRuntimeBatchResults({
      batch_id: "rb-x",
      results: [{ index: 0, verb: "done", target: "A", status: "weird" }],
    });
    expect(out.results[0].status).toBe("failed");
  });

  it("parses the undo report's reverted/conflicts/non_reversible lists", () => {
    const out = projectRuntimeBatchUndo({
      batch_id: "rb-9",
      reverted: [{ item: 1, verb: "done", target: "Walk", action_id: "ra-7" }],
      conflicts: [
        { item: 0, verb: "drop_from_plan", target: "Press", reason: "edited since" },
      ],
      non_reversible: [
        { item: 2, verb: "hide_always", target: "Email", reason: "no safe reverse" },
      ],
    });

    expect(out.batchId).toBe("rb-9");
    expect(out.reverted).toEqual([
      { item: 1, verb: "done", target: "Walk", actionId: "ra-7" },
    ]);
    expect(out.conflicts[0]).toMatchObject({
      item: 0,
      verb: "drop_from_plan",
      target: "Press",
      reason: "edited since",
    });
    expect(out.nonReversible[0]).toMatchObject({
      item: 2,
      verb: "hide_always",
      reason: "no safe reverse",
    });
  });
});

describe("wire: dropped-today snapshot and new verbs", () => {
  it("projects the optional {source,kind,minutes} snapshot and omits absent keys", () => {
    const projected = projectDroppedToday([
      {
        identity: "todoist:1",
        name: "Press",
        dropped_at: "2026-10-16T09:00:00",
        source: "todoist",
        kind: "recurring",
        minutes: 45,
      },
      { identity: "capacities:1", name: "Note", dropped_at: null, source: "capacities" },
      { identity: "todoist:2", name: "Bare", dropped_at: null },
    ]);

    expect(projected[0]).toEqual({
      identity: "todoist:1",
      name: "Press",
      droppedAt: "2026-10-16T09:00:00",
      source: "todoist",
      kind: "recurring",
      minutes: 45,
    });
    expect(projected[1]).toEqual({
      identity: "capacities:1",
      name: "Note",
      droppedAt: null,
      source: "capacities",
    });
    expect(projected[2]).toEqual({
      identity: "todoist:2",
      name: "Bare",
      droppedAt: null,
    });
    expect("source" in projected[2]).toBe(false);
    expect("kind" in projected[2]).toBe(false);
    expect("minutes" in projected[2]).toBe(false);
  });

  it("accepts the new wire verbs on the runtime-action projection", () => {
    expect(
      projectRuntimeAction({
        id: "ra-1",
        verb: "restore_today",
        target: "Press",
        status: "applied",
      }).verb,
    ).toBe("restore_today");
    expect(
      projectRuntimeAction({
        id: "ra-2",
        verb: "hide_always",
        target: "Email",
        status: "applied",
      }).verb,
    ).toBe("hide_always");
  });
});

describe("FixtureAdapter runtime batches", () => {
  it("reports a partial failure, then retries only the failed item", async () => {
    const adapter = new FixtureAdapter("ready");
    adapter.simulateBatchFailure(0);
    const first = await adapter.applyRuntimeBatch([
      { verb: "done", target: "Press" },
      { verb: "done", target: "Walk" },
    ]);

    expect(first.results.map((r) => r.status)).toEqual(["failed", "applied"]);
    expect(first.results[0].error).toMatch(/forced failure/);
    expect(first.results[1].error).toBeNull();
    expect(adapter.batchExecutionCount(first.batchId)).toBe(2);

    adapter.simulateBatchFailure(null);
    const retried = await adapter.retryRuntimeBatch(first.batchId);
    expect(retried.results.map((r) => r.status)).toEqual(["applied", "applied"]);
    // Only the failed item ran again: 2 on apply + 1 on retry.
    expect(adapter.batchExecutionCount(first.batchId)).toBe(3);
  });

  it("reverts the rest and reports the forced undo conflict", async () => {
    const adapter = new FixtureAdapter("ready");
    const batch = await adapter.applyRuntimeBatch([
      { verb: "done", target: "Press" },
      { verb: "done", target: "Walk" },
    ]);
    adapter.simulateBatchUndoConflict(1);
    const report = await adapter.undoRuntimeBatch(batch.batchId);

    expect(report.reverted.map((r) => r.item)).toEqual([0]);
    expect(report.reverted[0].actionId).toContain(batch.batchId);
    expect(report.conflicts.map((c) => c.item)).toEqual([1]);
    expect(report.conflicts[0].reason).toMatch(/conflict on item 1/);
    expect(report.nonReversible).toEqual([]);
  });

  it("reports already-reverted items as conflicts on a second undo", async () => {
    const adapter = new FixtureAdapter("ready");
    const batch = await adapter.applyRuntimeBatch([{ verb: "done", target: "Press" }]);
    await adapter.undoRuntimeBatch(batch.batchId);
    const second = await adapter.undoRuntimeBatch(batch.batchId);
    expect(second.reverted).toEqual([]);
    expect(second.conflicts).toEqual([
      { item: 0, verb: "done", target: "Press", reason: "already undone" },
    ]);
  });

  it("throws a bounded error for an unknown batch", async () => {
    const adapter = new FixtureAdapter("ready");
    await expect(adapter.retryRuntimeBatch("nope")).rejects.toThrow(/unknown batch/);
    await expect(adapter.undoRuntimeBatch("nope")).rejects.toThrow(/unknown batch/);
  });
});
