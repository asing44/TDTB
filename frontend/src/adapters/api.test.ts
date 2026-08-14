/* api.test.ts — ApiAdapter against a mocked fetch serving the contract
   fixtures. Pins the request side (paths, token header, POST body shapes)
   and every error shape (403/409/422/429), plus the locked-decision-17
   fixed-input gate. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiAdapter, ApiError } from "./api";

import planInputs from "./contract-fixtures/plan-inputs.json";
import planInputsDegraded from "./contract-fixtures/plan-inputs-degraded.json";
import billedLedger from "./contract-fixtures/billed-ledger.json";
import capacityPreview from "./contract-fixtures/capacity-preview.json";
import sequenceOk from "./contract-fixtures/sequence-ok.json";
import sequence422 from "./contract-fixtures/sequence-422.json";
import sequence429 from "./contract-fixtures/sequence-429.json";
import validateOk from "./contract-fixtures/validate-ok.json";
import shadowDiff from "./contract-fixtures/shadow-diff.json";
import commitLiveOk from "./contract-fixtures/commit-live-ok.json";
import commit409 from "./contract-fixtures/commit-409.json";
import error403 from "./contract-fixtures/error-403.json";

interface Call {
  path: string;
  init?: RequestInit;
}

let calls: Call[];
let routes: Record<string, { status: number; body: unknown }>;

function route(path: string, body: unknown, status = 200): void {
  routes[path] = { status, body };
}

beforeEach(() => {
  calls = [];
  routes = {};
  route("/session-token", { token: "tok-123" });
  route("/plan-inputs", planInputs);
  route("/billed-ledger", billedLedger);
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

const CTX = {
  included: [
    { id: "Make", blocks: 4 },
    { id: "Sample Project", blocks: 1 },
  ],
  planningConfigFingerprint: (planInputs as any).planning_config_fingerprint,
  overlapGrants: [],
  pinnedRows: [],
};

function postBody(path: string): any {
  const call = calls.find((c) => c.path.startsWith(path) && c.init?.method === "POST")!;
  return JSON.parse(call.init!.body as string);
}

describe("reads", () => {
  it("loadPlanInputs projects and needs no token", async () => {
    const a = new ApiAdapter();
    const p = await a.loadPlanInputs();
    expect(p.assigned.length).toBe(4);
    expect(calls.map((c) => c.path)).toEqual(["/plan-inputs"]);
  });

  it("billedLedger maps verbatim", async () => {
    const l = await new ApiAdapter().billedLedger();
    expect(l).toEqual(billedLedger);
  });

  it("capacityPreview sends day_setup + explicit selected minutes", async () => {
    route("/capacity-preview", capacityPreview);
    const a = new ApiAdapter();
    await a.loadPlanInputs();
    const c = await a.capacityPreview(
      { anchor: null, eod: null, buffering: "standard", anchored: {}, captures: { intention: "", forMeegy: "", stoic: "" }, confirmed: true },
      [3, 0.5, 0, 2],
    );
    const url = calls.find((c2) => c2.path.startsWith("/capacity-preview"))!.path;
    const params = new URLSearchParams(url.split("?")[1]);
    expect(JSON.parse(params.get("selected")!)).toEqual([90, 15, 0, 60]); // 15m + all-day stay exact
    expect(c.availableForSelection).toBe((capacityPreview as any).available_for_selection);
  });
});

describe("token + POST bodies", () => {
  it("fetches the session token once and reuses it", async () => {
    route("/day-setup", { ok: true });
    const a = new ApiAdapter();
    const setup = { anchor: "07:30", eod: null, buffering: "standard" as const, anchored: {}, captures: { intention: "", forMeegy: "", stoic: "" }, confirmed: false };
    await a.saveDaySetup(setup);
    await a.saveDaySetup(setup);
    expect(calls.filter((c) => c.path === "/session-token").length).toBe(1);
    const post = calls.find((c) => c.path === "/day-setup")!;
    expect((post.init!.headers as any)["X-TDTB-Token"]).toBe("tok-123");
  });

  it("autoSequence carries day semantics, planning fingerprint, and pins", async () => {
    route("/sequence", sequenceOk);
    const a = new ApiAdapter();
    await a.loadPlanInputs();
    const r = await a.autoSequence(CTX);
    expect(r.sequence.length).toBe(5);
    const body = postBody("/sequence");
    expect(body.assigned.map((i: any) => i.name)).toEqual(["Make", "Sample Project"]);
    expect(body.assigned[0].blocks).toBe(4); // today-only override applied
    expect(body.config).toEqual((planInputs as any).config); // server-verbatim
    expect(body.anchored_blocks).toEqual((planInputs as any).anchored_blocks);
    expect(body.day_semantics).toEqual((planInputs as any).day_semantics);
    expect(body.planning_config_fingerprint).toBe((planInputs as any).planning_config_fingerprint);
    expect(body.pinned_rows).toEqual([]);
  });

  it("validateSequence sends rows + the same shaped assigned set", async () => {
    route("/validate-sequence", validateOk);
    const a = new ApiAdapter();
    await a.loadPlanInputs();
    const rows = [{ id: "Make", start: "10:00", end: "12:00", zone: null, kind: "work" as const }];
    const v = await a.validateSequence(rows, CTX);
    expect(v.ok).toBe(true);
    const body = postBody("/validate-sequence");
    expect(body.sequence).toEqual([{ id: "Make", start: "10:00", end: "12:00", zone: null }]);
    expect(body.assigned.length).toBe(2);
    expect(body.overlap_grants).toEqual([]);
    expect(body.planning_config_fingerprint).toBe((planInputs as any).planning_config_fingerprint);
    expect(body.pinned_rows).toEqual([]);
  });

  it("shadowCommit posts mode=shadow with a SHAPED digest (T6): excluded rows drop, overrides land", async () => {
    route("/commit", shadowDiff);
    const a = new ApiAdapter();
    await a.loadPlanInputs();
    const rows = [{ id: "Make", start: "10:00", end: "11:00", zone: null, kind: "work" as const }];
    const d = await a.shadowCommit(rows, CTX);
    expect(d.entries.length).toBeGreaterThan(0);
    const call = calls.find((c) => c.path.startsWith("/commit"))!;
    expect(call.path).toContain("mode=shadow");
    const body = postBody("/commit");
    // Only the ctx-included rows survive; blocks reflect the override.
    expect(body.digest.assigned.map((r: any) => [r.name, r.blocks])).toEqual([
      ["Make", 4],
      ["Sample Project", 1],
    ]);
    // Non-assigned digest keys pass through verbatim for backend compat.
    expect(body.digest.valid_date).toBe((planInputs as any).digest.valid_date);
    expect(body.digest.suggested).toEqual((planInputs as any).digest.suggested);
    expect(body.sequence).toEqual({ sequence: [{ id: "Make", start: "10:00", end: "11:00", zone: null }] });
    expect(body.overlap_grants).toEqual([]);
    expect(body.pinned_rows).toEqual([]);
    expect(body.planning_config_fingerprint).toBe((planInputs as any).planning_config_fingerprint);
  });

  it("liveCommit posts mode=live with the same shaped digest and maps the report", async () => {
    route("/commit", commitLiveOk);
    const a = new ApiAdapter();
    await a.loadPlanInputs();
    const r = await a.liveCommit([], CTX);
    expect(r.status).toBe("ok");
    expect(calls.find((c) => c.path.startsWith("/commit"))!.path).toContain("mode=live");
    const body = postBody("/commit");
    expect(body.digest.assigned.length).toBe(2);
  });

  it("POSTs before loadPlanInputs are refused client-side", async () => {
    const a = new ApiAdapter();
    await expect(a.autoSequence(CTX)).rejects.toThrow("plan inputs not loaded");
  });
});

describe("error shapes (pinned from real responses)", () => {
  it("403 missing token", async () => {
    route("/day-setup", error403, 403);
    const a = new ApiAdapter();
    const err = await a
      .saveDaySetup({ anchor: null, eod: null, buffering: "standard", anchored: {}, captures: { intention: "", forMeegy: "", stoic: "" }, confirmed: false })
      .catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(403);
    expect(err.message).toContain("X-TDTB-Token");
  });

  it("422 sequence validation failure carries hard_errors detail", async () => {
    route("/sequence", sequence422, 422);
    const a = new ApiAdapter();
    await a.loadPlanInputs();
    const err = await a.autoSequence(CTX).catch((e) => e);
    expect(err.status).toBe(422);
    expect(err.detail.hard_errors.length).toBeGreaterThan(0);
    // T12 qualification: the message now carries the hard_errors rather than
    // dropping them — a bare "sequence validation failed" left the run with a
    // hard block and no stated cause.
    expect(err.message).toContain("sequence validation failed");
    for (const hard of err.detail.hard_errors) {
      expect(err.message).toContain(hard);
    }
  });

  it("429 billed budget exhausted", async () => {
    route("/sequence", sequence429, 429);
    const a = new ApiAdapter();
    await a.loadPlanInputs();
    const err = await a.autoSequence(CTX).catch((e) => e);
    expect(err.status).toBe(429);
    expect(err.message).toMatch(/billed/i);
  });

  it("409 live commit single-flight", async () => {
    route("/commit", commit409, 409);
    const a = new ApiAdapter();
    await a.loadPlanInputs();
    const err = await a.liveCommit([], CTX).catch((e) => e);
    expect(err.status).toBe(409);
    expect(err.message).toContain("already in flight");
  });
});

describe("fixed-input gate (locked decision 17)", () => {
  it("readFixedInputs projects calendar + anchored on a healthy read", async () => {
    const a = new ApiAdapter();
    const f = await a.readFixedInputs();
    expect(f.calendar.map((c) => c.name)).toEqual(["Sample Meeting"]);
  });

  it("THROWS on a calendar degrade — never an unchanged fingerprint", async () => {
    route("/plan-inputs", planInputsDegraded);
    const a = new ApiAdapter();
    await expect(a.readFixedInputs()).rejects.toThrow(/fixed-input read degraded/);
  });
});

describe("explicit source refresh (locked decision 20)", () => {
  it("performs EXACTLY one GET /plan-inputs + one GET /billed-ledger — no POST, no /gather", async () => {
    const a = new ApiAdapter();
    const r = await a.refreshSources();
    expect(r.inputs.assigned.length).toBe(4);
    expect(r.fixed.anchoredSourceFingerprint).toBe(r.inputs.anchoredSourceFingerprint);
    expect(calls.map((c) => c.path.split("?")[0])).toEqual([
      "/plan-inputs",
      "/billed-ledger",
    ]);
    expect(calls.every((c) => (c.init?.method ?? "GET") === "GET")).toBe(true);
  });

  it("throws on a degraded calendar read — last good view stays authoritative", async () => {
    route("/plan-inputs", planInputsDegraded);
    await expect(new ApiAdapter().refreshSources()).rejects.toThrow(
      /source refresh degraded/,
    );
  });
});

describe("T12 qualification: token rotation + error detail", () => {
  it("refetches the token and retries once when a write 403s", async () => {
    /* A server restart rotates app.state.token, so a page open across the
       restart 403s on every write — including the billed Send — with no
       "session expired" affordance. 403 is rejected at the dependency, before
       _require_billed_budget, so the retry cannot double-charge. */
    const a = new ApiAdapter();
    route("/capacity-preview", capacityPreview);
    await a.loadPlanInputs();

    let attempts = 0;
    routes["/sequence"] = { status: 403, body: error403 };
    (globalThis.fetch as any).mockImplementation(async (url: string, init?: RequestInit) => {
      const path = url.split("?")[0];
      calls.push({ path: url, init });
      if (path === "/sequence") {
        attempts += 1;
        return attempts === 1
          ? new Response(JSON.stringify(error403), { status: 403 })
          : new Response(JSON.stringify(sequenceOk), { status: 200 });
      }
      if (path === "/session-token") {
        return new Response(JSON.stringify({ token: `tok-${attempts}` }), { status: 200 });
      }
      const r = routes[path];
      return r ? new Response(JSON.stringify(r.body), { status: r.status })
               : new Response("null", { status: 404 });
    });

    const out = await a.autoSequence(CTX as any);
    expect(attempts).toBe(2);
    expect(out).toBeTruthy();
    // the retry carried a freshly fetched token, not the stale one
    const seqCalls = calls.filter((c) => c.path.includes("/sequence"));
    const lastToken = (seqCalls.at(-1)!.init!.headers as any)["X-TDTB-Token"];
    expect(lastToken).not.toBe("tok-123");
  });

  it("stops after one retry — a persistent 403 still surfaces", async () => {
    const a = new ApiAdapter();
    await a.loadPlanInputs();
    route("/sequence", error403, 403);
    await expect(a.autoSequence(CTX as any)).rejects.toBeInstanceOf(ApiError);
  });

  it("surfaces server hard_errors in the thrown message", async () => {
    /* The run dead-ended on a bare "pinned-row validation failed"; the cause
       ("foreign pinned row 'LOOTS'") was only readable off the network tab. */
    const a = new ApiAdapter();
    await a.loadPlanInputs();
    route("/sequence", {
      detail: {
        message: "pinned-row validation failed",
        hard_errors: ["foreign pinned row 'LOOTS'", "foreign pinned row 'M2.5'"],
      },
    }, 422);
    await expect(a.autoSequence(CTX as any)).rejects.toThrow(
      /pinned-row validation failed: foreign pinned row 'LOOTS'; foreign pinned row 'M2\.5'/,
    );
  });
});
