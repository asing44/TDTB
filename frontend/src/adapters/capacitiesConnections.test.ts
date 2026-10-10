/* capacitiesConnections.test.ts — wire projections + ApiAdapter boundary for
   the Connections surfaces (rules, refresh status, selections).

   Pins:
   - the exact real wire field names for GET/POST /capacities/rules, GET
     /capacities/refresh/status, and GET/POST /capacities/selections;
   - the fail-closed projections (a malformed save answer never reads as a
     successful activation; a malformed status never invents progress);
   - the ApiAdapter 409 conflict extractors and 503 fail-closed path;
   - that a canonical selection identity, once promoted into the server
     digest and re-read through /plan-inputs, reaches the Commit body — a
     selection is never a UI-local promotion. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiAdapter,
  ApiError,
  capacitiesRulesConflictOf,
  capacitiesSelectionsConflictOf,
} from "./api";
import {
  capacitiesRefreshStartToWire,
  capacitiesRuleSaveToWire,
  capacitiesSelectionsToWire,
  projectCapacitiesRefreshStatus,
  projectCapacitiesRuleSaveResponse,
  projectCapacitiesRules,
  projectCapacitiesSelections,
} from "./wire";

import planInputs from "./contract-fixtures/plan-inputs.json";
import commitLiveOk from "./contract-fixtures/commit-live-ok.json";

const CANONICAL = "capacities:space-1:custom-project:obj-1";

// -- real wire fixtures ------------------------------------------------------

const RULES_WIRE = {
  space_id: "space-1",
  revision: 4,
  configured: true,
  capabilities: {
    ops: ["after", "before", "eq", "exists", "gt", "in", "lt", "truthy"],
    value_ops: ["after", "before", "eq", "gt", "in", "lt"],
    presence_ops: ["exists", "truthy"],
    number_ops: ["gt", "lt"],
    date_ops: ["after", "before"],
    equality_ops: ["eq", "in"],
    value_kinds: ["boolean", "date", "entity", "label", "number", "richText", "text", "title", "url"],
    number_kinds: ["number"],
    date_kinds: ["date"],
    matches: false,
    schema_source: "structure_contract",
    contract_available: true,
  },
  structures: [
    {
      structure_id: "custom-project",
      active: { prop: "status-prop", op: "eq", values: ["in-progress"] },
      draft: { not: { prop: "title-prop", op: "exists" } },
      fallback_minutes: 45,
      mapped: true,
      schema: { "title-prop": "title", "status-prop": "label" },
      schema_available: true,
    },
    {
      structure_id: "orphan",
      active: null,
      draft: null,
      fallback_minutes: null,
      mapped: false,
      schema: {},
      schema_available: false,
    },
  ],
};

const REFRESH_WIRE = {
  configured: true,
  phase: "evaluating",
  outcome: null,
  mode: "refresh",
  scope: "all",
  job: {
    job_id: "abc123",
    mode: "refresh",
    scope: "all",
    phase: "evaluating",
    outcome: null,
    revision: 7,
    generation: 2,
    started_at: 1000,
    updated_at: 1001,
    finished_at: null,
    progress: { listed: 12, read: 5, types: { Project: 3 } },
    warnings: ["Capacities partial — 5 evaluated · 7 deferred"],
  },
  progress: { listed: 12, read: 5, types: { Project: 3 } },
  warnings: ["Capacities partial — 5 evaluated · 7 deferred"],
  coverage: {
    Project: { listing_checked_at: 999, members: 3, freshly_read: 2 },
  },
  snapshot: {
    present: true,
    generation: 2,
    revision: "rev-2",
    installed_at: 990,
    member_count: 3,
    type_check_times: { Project: 995 },
  },
};

const SELECTIONS_WIRE = {
  space_id: "space-1",
  revision: 6,
  rules_revision: 4,
  selections: [{ identity: CANONICAL, acknowledged: true, rules_revision: 4 }],
};

describe("capacities rules projection", () => {
  it("projects the exact real rules wire fields", () => {
    const rules = projectCapacitiesRules(RULES_WIRE);
    expect(rules.spaceId).toBe("space-1");
    expect(rules.revision).toBe(4);
    expect(rules.configured).toBe(true);
    expect(rules.capabilities.matches).toBe(false);
    expect(rules.capabilities.schemaSource).toBe("structure_contract");
    expect(rules.capabilities.contractAvailable).toBe(true);
    expect(rules.capabilities.presenceOps).toEqual(["exists", "truthy"]);
    expect(rules.structures[0]).toMatchObject({
      structureId: "custom-project",
      fallbackMinutes: 45,
      mapped: true,
      schemaAvailable: true,
    });
    // A leaf and a negation survive as the DSL shapes, not flattened strings.
    expect(rules.structures[0].active).toEqual({ prop: "status-prop", op: "eq", values: ["in-progress"] });
    expect(rules.structures[0].draft).toEqual({ not: { prop: "title-prop", op: "exists" } });
    // A null fallback stays null (the editor's "empty clears it" contract).
    expect(rules.structures[1].fallbackMinutes).toBeNull();
    expect(rules.structures[1].schemaAvailable).toBe(false);
  });

  it("projects a POST save answer as full GET plus the save outcome, fail-closed", () => {
    const response = projectCapacitiesRuleSaveResponse({
      ...RULES_WIRE,
      revision: 5,
      save: {
        structure_id: "custom-project",
        valid: false,
        reason: "references unknown property 'gone'",
        active: { prop: "status-prop", op: "eq", values: ["in-progress"] },
        draft: { prop: "gone", op: "eq", values: ["x"] },
        fallback_minutes: null,
        revision: 5,
      },
    });
    expect(response.revision).toBe(5);
    expect(response.save.valid).toBe(false);
    expect(response.save.reason).toContain("unknown property");
    // The prior active rule is preserved, not replaced by the invalid draft.
    expect(response.save.active).toEqual({ prop: "status-prop", op: "eq", values: ["in-progress"] });
    expect(response.save.draft).toEqual({ prop: "gone", op: "eq", values: ["x"] });
    expect(response.save.fallbackMinutes).toBeNull();

    // A malformed save block never reads as a successful activation.
    const malformed = projectCapacitiesRuleSaveResponse({ ...RULES_WIRE, save: "nope" });
    expect(malformed.save.valid).toBe(false);
    expect(malformed.save.active).toBeNull();
  });

  it("builds the POST body with the predicate verbatim and null-clearing fallback", () => {
    expect(
      capacitiesRuleSaveToWire({
        structureId: "custom-project",
        rule: { all: [{ prop: "title-prop", op: "exists" }] },
        fallbackMinutes: null,
        expectedRevision: 4,
      }),
    ).toEqual({
      structure_id: "custom-project",
      rule: { all: [{ prop: "title-prop", op: "exists" }] },
      fallback_minutes: null,
      expected_revision: 4,
    });
  });
});

describe("capacities refresh status projection", () => {
  it("projects the exact real status fields", () => {
    const status = projectCapacitiesRefreshStatus(REFRESH_WIRE);
    expect(status.configured).toBe(true);
    expect(status.phase).toBe("evaluating");
    expect(status.mode).toBe("refresh");
    expect(status.scope).toBe("all");
    expect(status.job?.jobId).toBe("abc123");
    expect(status.job?.progress).toEqual({ listed: 12, read: 5, types: { Project: 3 } });
    expect(status.progress.read).toBe(5);
    expect(status.coverage.Project).toEqual({
      listingCheckedAt: 999,
      members: 3,
      freshlyRead: 2,
    });
    expect(status.snapshot).toEqual({
      present: true,
      generation: 2,
      revision: "rev-2",
      installedAt: 990,
      memberCount: 3,
      typeCheckTimes: { Project: 995 },
    });
  });

  it("projects an unconfigured source as no job and an absent snapshot", () => {
    const status = projectCapacitiesRefreshStatus({
      configured: false,
      job: null,
      phase: null,
      outcome: null,
      progress: {},
      warnings: [],
      coverage: {},
      snapshot: {
        present: false,
        generation: 0,
        revision: null,
        installed_at: null,
        member_count: 0,
        type_check_times: {},
      },
    });
    expect(status.configured).toBe(false);
    expect(status.job).toBeNull();
    expect(status.mode).toBeNull();
    expect(status.snapshot.present).toBe(false);
    expect(status.progress).toEqual({ listed: 0, read: 0, types: {} });
  });

  it("builds the start body for the two distinct modes", () => {
    expect(capacitiesRefreshStartToWire("refresh", "all")).toEqual({ mode: "refresh", scope: "all" });
    expect(capacitiesRefreshStartToWire("rescan", "all")).toEqual({ mode: "rescan", scope: "all" });
  });
});

describe("capacities selections projection", () => {
  it("projects the exact real selections wire fields", () => {
    const selections = projectCapacitiesSelections(SELECTIONS_WIRE);
    expect(selections.spaceId).toBe("space-1");
    expect(selections.revision).toBe(6);
    expect(selections.rulesRevision).toBe(4);
    expect(selections.selections).toEqual([
      { identity: CANONICAL, acknowledged: true, rulesRevision: 4 },
    ]);
  });

  it("projects a source-less read with a null space and null rules revision", () => {
    const selections = projectCapacitiesSelections({
      space_id: null,
      revision: 0,
      rules_revision: null,
      selections: [],
    });
    expect(selections.spaceId).toBeNull();
    expect(selections.rulesRevision).toBeNull();
  });

  it("builds the merge body with the acknowledgement flag", () => {
    expect(
      capacitiesSelectionsToWire({
        expectedRevision: 6,
        select: [{ identity: CANONICAL, acknowledge: true }],
        deselect: ["capacities:space-1:custom-project:obj-9"],
      }),
    ).toEqual({
      expected_revision: 6,
      select: [{ identity: CANONICAL, acknowledge: true }],
      deselect: ["capacities:space-1:custom-project:obj-9"],
    });
  });
});

// -- ApiAdapter boundary -----------------------------------------------------

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

function postBody(path: string): any {
  const call = calls.find((c) => c.path.startsWith(path) && c.init?.method === "POST")!;
  return JSON.parse(call.init!.body as string);
}

describe("ApiAdapter connections boundary", () => {
  it("reads rules/refresh/selections without a session token and posts the exact bodies", async () => {
    route("/capacities/rules", RULES_WIRE);
    route("/capacities/refresh/status", REFRESH_WIRE);
    route("/capacities/refresh/start", REFRESH_WIRE);
    route("/capacities/refresh/cancel", REFRESH_WIRE);
    route("/capacities/selections", SELECTIONS_WIRE);
    const adapter = new ApiAdapter();

    expect((await adapter.loadCapacitiesRules()).revision).toBe(4);
    expect((await adapter.capacitiesRefreshStatus()).phase).toBe("evaluating");
    expect((await adapter.loadCapacitiesSelections()).revision).toBe(6);

    await adapter.saveCapacitiesRule({
      structureId: "custom-project",
      rule: { all: [{ prop: "title-prop", op: "exists" }] },
      fallbackMinutes: null,
      expectedRevision: 4,
    });
    expect(postBody("/capacities/rules")).toEqual({
      structure_id: "custom-project",
      rule: { all: [{ prop: "title-prop", op: "exists" }] },
      fallback_minutes: null,
      expected_revision: 4,
    });

    await adapter.startCapacitiesRefresh("rescan", "all");
    expect(postBody("/capacities/refresh/start")).toEqual({ mode: "rescan", scope: "all" });

    await adapter.saveCapacitiesSelections({
      expectedRevision: 6,
      select: [{ identity: CANONICAL, acknowledge: true }],
      deselect: [],
    });
    expect(postBody("/capacities/selections")).toEqual({
      expected_revision: 6,
      select: [{ identity: CANONICAL, acknowledge: true }],
      deselect: [],
    });
  });

  it("surfaces a real 409 as a typed conflict for rules and selections", async () => {
    route(
      "/capacities/rules",
      { detail: { code: "capacities_rules_conflict", message: "changed", expected_revision: 4, current_revision: 5 } },
      409,
    );
    route(
      "/capacities/selections",
      { detail: { code: "capacities_selections_conflict", message: "changed", expected_revision: 6, current_revision: 7 } },
      409,
    );
    const adapter = new ApiAdapter();

    const rulesError = await adapter
      .saveCapacitiesRule({ structureId: "custom-project", rule: { all: [] }, fallbackMinutes: null, expectedRevision: 4 })
      .catch((e) => e);
    expect(capacitiesRulesConflictOf(rulesError)).toEqual({ expectedRevision: 4, currentRevision: 5 });

    const selectionsError = await adapter
      .saveCapacitiesSelections({ expectedRevision: 6, select: [], deselect: [] })
      .catch((e) => e);
    expect(capacitiesSelectionsConflictOf(selectionsError)).toEqual({ expectedRevision: 6, currentRevision: 7 });

    // A different 409 code is NOT a revision conflict (fail-closed narrowing).
    const storage = new ApiError(409, { code: "capacities_rules_storage_error" }, "storage");
    expect(capacitiesRulesConflictOf(storage)).toBeNull();
  });

  it("fails closed on a 503 selections storage error", async () => {
    route(
      "/capacities/selections",
      { detail: { code: "capacities_selections_storage_error", message: "unavailable" } },
      503,
    );
    const adapter = new ApiAdapter();
    const error = await adapter
      .saveCapacitiesSelections({ expectedRevision: 0, select: [], deselect: [] })
      .catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect(capacitiesSelectionsConflictOf(error)).toBeNull();
  });

  it("carries a promoted canonical selection identity into the Commit body after re-reading plan inputs", async () => {
    const promoted = structuredClone(planInputs) as any;
    promoted.digest.assigned = [
      ...promoted.digest.assigned,
      { name: "Draft launch brief", identity: CANONICAL, source: "capacities", types: ["custom-project"] },
    ];
    route("/plan-inputs", promoted);
    route("/commit", commitLiveOk);
    const adapter = new ApiAdapter();

    // The selection was applied server-side; the fresh /plan-inputs read is
    // what surfaces the promoted row to this client.
    await adapter.loadPlanInputs();
    await adapter.liveCommit([], {
      included: [{ id: "Draft launch brief", blocks: 1 }],
      planningConfigFingerprint: promoted.planning_config_fingerprint,
    });

    const body = postBody("/commit");
    expect(body.digest.assigned.some((row: any) => row.identity === CANONICAL)).toBe(true);
  });
});
