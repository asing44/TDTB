/* The micro-adventure pool editor's data path: the two API calls project the
   wire shape exactly, and the controller keeps the loaded pool and the save
   status in the store (a failed load keeps the last good pool). */

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiAdapter, ApiError } from "../adapters/api";
import { FixtureAdapter } from "../adapters/fixture";
import { projectMicroPool } from "../adapters/wire";
import { makeHarness } from "../ui/test-harness";
import type { MicroPoolItem } from "../model/types";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const POOL: MicroPoolItem[] = [
  { id: "ma01", idea: "Watch sunset", category: "nature", effort: "low", active: true },
  { id: "ma02", idea: "Try pottery", category: "", effort: "", active: false },
];

describe("projectMicroPool", () => {
  it("projects every field, including inactive and empty-string ones", () => {
    expect(projectMicroPool({ ok: true, pool: POOL })).toEqual(POOL);
  });

  it("refuses a body without a pool array", () => {
    expect(() => projectMicroPool({ ok: true })).toThrow();
  });
});

describe("ApiAdapter micro-adventure pool", () => {
  function stub(responses: Record<string, { status: number; body: unknown }>) {
    const calls: { path: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ path: url, init });
        const r = responses[url] ?? { status: 404, body: null };
        return new Response(JSON.stringify(r.body), { status: r.status });
      }),
    );
    return calls;
  }

  it("GETs the config pool once WITH the session token", async () => {
    const calls = stub({
      "/session-token": { status: 200, body: { token: "tok-1" } },
      "/config/micro-adventure/pool": { status: 200, body: { ok: true, pool: POOL } },
    });
    expect(await new ApiAdapter().loadMicroPool()).toEqual(POOL);
    const reads = calls.filter((c) => c.path === "/config/micro-adventure/pool");
    expect(reads).toHaveLength(1);
    // A GET, not a POST — but a TOKEN-BEARING one: the server guards this
    // route with require_token, so a tokenless read 403s.
    expect(reads[0].init?.method).toBeUndefined();
    expect((reads[0].init!.headers as Record<string, string>)["X-TDTB-Token"]).toBe("tok-1");
  });

  it("re-fetches the token once after a 403 on the pool GET", async () => {
    const calls: { path: string; init?: RequestInit }[] = [];
    let tokenReads = 0;
    let poolReads = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ path: url, init });
        if (url === "/session-token") {
          tokenReads += 1;
          return new Response(JSON.stringify({ token: `tok-${tokenReads}` }), { status: 200 });
        }
        poolReads += 1;
        if (poolReads === 1) {
          return new Response(
            JSON.stringify({ detail: "missing or invalid X-TDTB-Token" }),
            { status: 403 },
          );
        }
        return new Response(JSON.stringify({ ok: true, pool: POOL }), { status: 200 });
      }),
    );
    expect(await new ApiAdapter().loadMicroPool()).toEqual(POOL);
    expect(tokenReads).toBe(2);
    const reads = calls.filter((c) => c.path === "/config/micro-adventure/pool");
    expect(reads).toHaveLength(2);
    expect((reads[1].init!.headers as Record<string, string>)["X-TDTB-Token"]).toBe("tok-2");
  });

  it("POSTs the full pool once with the token and projects the saved pool", async () => {
    const calls = stub({
      "/session-token": { status: 200, body: { token: "tok-1" } },
      "/config/micro-adventure/pool": { status: 200, body: { ok: true, pool: POOL } },
    });
    expect(await new ApiAdapter().saveMicroPool(POOL)).toEqual(POOL);
    const post = calls.find((c) => c.init?.method === "POST")!;
    expect((post.init!.headers as Record<string, string>)["X-TDTB-Token"]).toBe("tok-1");
    expect(JSON.parse(post.init!.body as string)).toEqual({ pool: POOL });
    expect(calls.filter((c) => c.init?.method === "POST")).toHaveLength(1);
  });

  it("surfaces the shadowed 409 as an ApiError carrying its code", async () => {
    stub({
      "/session-token": { status: 200, body: { token: "tok-1" } },
      "/config/micro-adventure/pool": {
        status: 409,
        body: { detail: { code: "micro_adventure_pool_shadowed", message: "config.json overrides it" } },
      },
    });
    const err = await new ApiAdapter().saveMicroPool(POOL).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect((err.detail as { code: string }).code).toBe("micro_adventure_pool_shadowed");
  });
});

describe("controller micro pool", () => {
  it("starts empty and idle", () => {
    const h = makeHarness("ready");
    expect(h.store.getState().microPool).toEqual({
      items: null, load: "idle", loadError: null, save: "idle", saveError: null, shadowed: false,
    });
  });

  it("loads the pool into state", async () => {
    const h = makeHarness("ready");
    vi.spyOn(FixtureAdapter.prototype, "loadMicroPool").mockResolvedValue(POOL);
    await h.controller.loadMicroPool();
    const mp = h.store.getState().microPool;
    expect(mp.items).toEqual(POOL);
    expect(mp.load).toBe("idle");
  });

  it("a failed load keeps the last good pool and names the failure", async () => {
    const h = makeHarness("ready");
    const load = vi.spyOn(FixtureAdapter.prototype, "loadMicroPool");
    load.mockResolvedValueOnce(POOL);
    await h.controller.loadMicroPool();
    load.mockRejectedValueOnce(new Error("offline"));
    await h.controller.loadMicroPool();
    const mp = h.store.getState().microPool;
    expect(mp.items).toEqual(POOL);
    expect(mp.load).toBe("failed");
    expect(mp.loadError).toContain("offline");
  });

  it("saves the full pool and records the saved pool", async () => {
    const h = makeHarness("ready");
    const save = vi.spyOn(FixtureAdapter.prototype, "saveMicroPool").mockResolvedValue(POOL);
    await h.controller.saveMicroPool(POOL);
    expect(save).toHaveBeenCalledWith(POOL);
    const mp = h.store.getState().microPool;
    expect(mp.items).toEqual(POOL);
    expect(mp.save).toBe("saved");
    expect(mp.saveError).toBeNull();
  });

  it("a failed save keeps the last good pool and reports the error", async () => {
    const h = makeHarness("ready");
    vi.spyOn(FixtureAdapter.prototype, "loadMicroPool").mockResolvedValue(POOL);
    await h.controller.loadMicroPool();
    vi.spyOn(FixtureAdapter.prototype, "saveMicroPool").mockRejectedValue(
      new ApiError(422, { code: "micro_adventure_pool_invalid", message: "idea is blank" }, "idea is blank"),
    );
    await h.controller.saveMicroPool([{ ...POOL[0], idea: " " }]);
    const mp = h.store.getState().microPool;
    expect(mp.items).toEqual(POOL);
    expect(mp.save).toBe("failed");
    expect(mp.saveError).toBe("idea is blank");
    expect(mp.shadowed).toBe(false);
  });

  it("flags the shadowed 409 so the UI can say the edit has no effect", async () => {
    const h = makeHarness("ready");
    vi.spyOn(FixtureAdapter.prototype, "saveMicroPool").mockRejectedValue(
      new ApiError(409, { code: "micro_adventure_pool_shadowed", message: "overridden" }, "overridden"),
    );
    await h.controller.saveMicroPool(POOL);
    const mp = h.store.getState().microPool;
    expect(mp.save).toBe("failed");
    expect(mp.shadowed).toBe(true);
  });
});
