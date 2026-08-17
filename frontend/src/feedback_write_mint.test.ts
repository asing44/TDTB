/* feedback_write_mint.test.ts — FEEDBACK-27 (2026-08-14): frontend half of
   the write-path and Mint reliability fixtures.

   Deterministic, fake-only fixtures that mirror the backend suite
   (app/tests/test_feedback_write_mint.py) on the cockpit side:

   - Press due fixture (FEEDBACK-23): the reported Press write compared
     intent 19:00 against the raw UTC wall clock 23:00 of a fixed Todoist
     due. The equivalent 23:00Z / America/New_York encoding projects clean
     (no verify failure), while a true different instant renders 12-hour
     ("7 PM" / "11 PM") with the canonical raw ISO + timezone kept as the
     machine field — never raw 24h visible text.
   - Setup-gate fixture (FEEDBACK-24): a skeleton runstate echo without the
     day_setup_confirmed flag stays unconfirmed in the store and never
     reaches a commit/runtime writer.
   - Mint-capacity fixture (FEEDBACK-25): the 300-minute Mint configuration
     carries capacity.mint 10 and daySemantics.effectiveAllotmentMinutes
     300 — no hardcoded 2-block fallback — and mintWalls resolves the 10
     selected session intervals.
   - Mint-overlap fixture (FEEDBACK-25): the free-gap scan never stages a
     dropped row over selected Mint time; rows no gap can hold are reported
     as explicit infeasibility.
   - Controller end-to-end: with only Mint rows returned by the server, the
     dropped-work overflow lays rows around Mint walls (zero overlap) and
     never calls liveCommit/shadowCommit/runtimeAction/undoRuntimeAction. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup } from "@testing-library/preact";
import { Fragment, h } from "preact";

afterEach(cleanup);

/* Mirror App.tsx's drawer lifecycle without JSX (this fixture file is a .ts
   target): the approval drawer mounts only while open. */
function DrawerHost() {
  const s = useAppState();
  return h(
    Fragment,
    null,
    h(ActionDock, null),
    s.ui.approvalOpen ? h(ApprovalDrawer, null) : null,
  );
}

import { createStore } from "./store/createStore";
import { Controller } from "./store/controller";
import { FixtureAdapter } from "./adapters/fixture";
import { projectCommitReport } from "./adapters/wire";
import { mintWalls, planOverflow } from "./model/overflow";
import { makeHarness } from "./ui/test-harness";
import { useAppState } from "./ui/context";
import { ActionDock } from "./ui/ActionDock";
import { ApprovalDrawer } from "./ui/ApprovalDrawer";
import type {
  AssignedItem,
  Capacity,
  PlanInputs,
  SequenceRow,
} from "./model/types";

function toMin(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

function fmt(min: number): string {
  const h = Math.floor(min / 60);
  const m = min % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

function item(name: string, blocks: number): AssignedItem {
  return {
    id: name, name, path: null, source: "vault", types: [], urgency: null,
    deadline: null, priorityScore: 0, blocks, durationLabel: "", todoistId: null,
    labels: [],
  };
}

const blocksOf = (i: AssignedItem) => i.blocks;

function harness() {
  const store = createStore();
  const adapter = new FixtureAdapter("ready");
  const controller = new Controller(adapter, store.dispatch, store.getState);
  return { store, adapter, controller };
}

/* ---------------------------------------------------------------------- */
/* Shared Mint-300 fixture — a configured 300-minute Mint allotment day     */
/* ---------------------------------------------------------------------- */

/** The reported 300-minute Mint configuration: capacity reserves 10 blocks
    (300 / 30) — never the hardcoded 2-block fallback. */
export function mint300Capacity(): Capacity {
  return {
    total: 20, // 08:00 -> 18:00 = 20 x 30-min blocks
    fixed: 0,
    anchored: 0,
    habits: 0,
    mint: 10, // effectiveAllotmentMinutes 300 / 30
    selected: 8,
    buffer: 0,
    free: 2,
    overassigned: false,
    availableForSelection: 2,
    remaining: "2 blk free",
    ratio: "18 / 20 blk",
    legend:
      "Fixed 0 · Anchored 0 · Habits 0 · Mint 10 · Selected 8 · Buffer 0 · Free 2 · Total 20",
    counters: "deep: 1 / 4 · mixed: 2 / 3",
    workBusy: 0,
    workOverflow: 0,
  };
}

export const MINT300_ASSIGNED: AssignedItem[] = [
  { id: "Deep Work", name: "Deep Work", path: null, source: "vault", types: ["project"], urgency: null, deadline: null, priorityScore: 80, blocks: 4, durationLabel: "2hr", todoistId: null },
  { id: "Admin", name: "Admin", path: null, source: "vault", types: ["task"], urgency: null, deadline: null, priorityScore: 30, blocks: 2, durationLabel: "1hr", todoistId: null },
  { id: "Review", name: "Review", path: null, source: "vault", types: ["task"], urgency: null, deadline: null, priorityScore: 50, blocks: 2, durationLabel: "1hr", todoistId: null },
];

export function mint300Inputs(): PlanInputs {
  return {
    validDate: "2026-07-13",
    assigned: MINT300_ASSIGNED.map((a) => ({ ...a })),
    unassignedCandidates: [],
    staleAssigned: [],
    droppedToday: [],
    anchored: [],
    anchoredSourceFingerprint: "mint300-anchored-v1",
    habitsNote: "no habits outstanding",
    time: {
      now: "07:55",
      anchor: "08:00",
      effectiveEod: "18:00",
      eodNote: null,
      configEod: "18:00",
      totalBlocks: 20,
    },
    capacity: mint300Capacity(),
    daySetup: {
      anchor: "08:00",
      eod: "18:00",
      buffering: "off",
      anchored: {},
      captures: { intention: "300-minute Mint day", forMeegy: "", stoic: "" },
      confirmed: true,
    },
    daySemantics: {
      availablePresets: [],
      selectedPreset: null,
      resolutionSource: "default",
      enabledZones: ["Trinoor Hours"],
      effectiveAllotmentMinutes: 300,
      defaultAllotmentMinutes: 300,
      mintEnabled: true,
      warnings: [],
      errors: [],
      overlapPermissionsRaw: "",
    },
    planningConfigFingerprint: "mint300-planning-v1",
    sourceWarnings: [],
    sourceCounts: { vault: 3, todoist: 0, calendar: 0 },
    sourceHealth: "ok",
    microAdventure: {
      pick: { id: "ma01", idea: "Stretch", category: "health" },
      source: "auto",
      pool: [{ id: "ma01", idea: "Stretch", category: "health" }],
      streak: 0,
      pendingConfirm: null,
    },
  };
}

/** The server proposal for the Mint-300 day: 10 selected Mint session rows
    (08:30 -> 13:00) plus three placed work rows around them. Rows carry the
    wire markers mintWalls reads (wire.mint_session / source schedulable). */
export function mint300Sequence(): SequenceRow[] {
  const mint = Array.from({ length: 10 }, (_, i) => {
    const start = 8 * 60 + 30 + i * 30;
    return {
      id: `Mint Morning · ${fmt(start)}`,
      start: fmt(start),
      end: fmt(start + 30),
      zone: "work_hours",
      kind: "work",
      wire: {
        source: "schedulable",
        mint_session: true,
        mint_session_id: `mint:morning:${fmt(start)}`,
      },
    } as SequenceRow;
  });
  return [
    ...mint,
    { id: "Deep Work", start: "13:30", end: "15:30", zone: "any", kind: "work" },
    { id: "Admin", start: "15:30", end: "16:30", zone: "any", kind: "work" },
    { id: "Review", start: "16:30", end: "17:30", zone: "any", kind: "work" },
  ];
}

/* ---------------------------------------------------------------------- */
/* Press due fixture — FEEDBACK-23                                          */
/* ---------------------------------------------------------------------- */

describe("FEEDBACK-27 Press due fixture", () => {
  it("equivalent 23:00Z / 19:00 local encoding projects clean (no failure)", () => {
    const report = projectCommitReport({
      ok: true,
      surfaces: { todoist: { status: "ok", note: "1 no-op" } },
      verify_failures: [],
      verify_details: [],
    });
    expect(report.status).toBe("ok");
    expect(report.verifyFailures).toEqual([]);
    expect(report.verifyDetails).toEqual([]);
  });

  it("a true different instant renders 12-hour with canonical machine fields", () => {
    const mk = makeHarness("commit-preview");
    mk.store.dispatch({ type: "ARM_LIVE" });
    mk.store.dispatch({ type: "COMMIT_START" });
    mk.store.dispatch({
      type: "COMMIT_DONE",
      report: {
        status: "failed",
        surfaces: [
          { system: "todoist", status: "failed", detail: "due mismatch" },
        ],
        verifyFailures: [
          "todoist: 'Press' due mismatch (intent 7 PM, live 11 PM)",
        ],
        verifyDetails: [
          {
            kind: "due",
            name: "Press",
            intent: "19:00",
            live: "23:00",
            liveRaw: "2026-07-12T23:00:00Z",
            liveTimezone: "America/New_York",
            reason: "mismatch",
            message: "todoist: 'Press' due mismatch (intent 7 PM, live 11 PM)",
          },
        ],
      },
    });
    // Mirror App.tsx's drawer lifecycle: drawers mount only while open.
    const r = mk.ui(h(DrawerHost, null));
    // Visible text is 12-hour with AM/PM, never raw 24h.
    expect(
      r.getByText(/Press — due verification: intent 7 PM, live 11 PM/),
    ).toBeTruthy();
    expect(r.queryByText(/19:00/)).toBeNull();
    expect(r.queryByText(/23:00/)).toBeNull();
    // Canonical raw ISO + timezone retained as the machine field (hover).
    const titled = r.container.querySelector(
      '[title*="America/New_York"]',
    ) as HTMLElement;
    expect(titled).toBeTruthy();
    expect(titled.getAttribute("title")).toContain("2026-07-12T23:00:00Z");
  });
});

/* ---------------------------------------------------------------------- */
/* Setup-gate fixture — FEEDBACK-24                                         */
/* ---------------------------------------------------------------------- */

describe("FEEDBACK-27 setup-gate fixture (frontend)", () => {
  it("skeleton runstate echo without the flag stays unconfirmed and never reaches a writer", async () => {
    const { store, adapter, controller } = harness();
    vi.spyOn(adapter, "loadPlanInputs").mockResolvedValue({
      ...structuredClone(adapter.scenario.inputs),
      ...mint300Inputs(),
      assigned: [],
      daySetup: {
        ...mint300Inputs().daySetup,
        confirmed: false,
        captures: { intention: "", forMeegy: "", stoic: "" },
      },
    });
    const liveCommit = vi.spyOn(adapter, "liveCommit");
    const shadowCommit = vi.spyOn(adapter, "shadowCommit");
    const runtimeAction = vi.spyOn(adapter, "runtimeAction");
    const undoRuntimeAction = vi.spyOn(adapter, "undoRuntimeAction");

    await controller.load();

    // FEEDBACK-24: confirmation is flag-driven — echoed skeleton keys with
    // no day_setup_confirmed read as unconfirmed.
    expect(store.getState().daySetup.confirmed).toBe(false);
    // Nothing in load -> setup -> sequence may reach an external writer.
    expect(liveCommit).not.toHaveBeenCalled();
    expect(shadowCommit).not.toHaveBeenCalled();
    expect(runtimeAction).not.toHaveBeenCalled();
    expect(undoRuntimeAction).not.toHaveBeenCalled();
  });
});

/* ---------------------------------------------------------------------- */
/* Mint-capacity fixture — FEEDBACK-25 (300 minutes, no 2-block fallback)   */
/* ---------------------------------------------------------------------- */

describe("FEEDBACK-27 Mint 300 capacity fixture", () => {
  it("carries 300-minute Mint configuration: 10 blocks, never 2", () => {
    const inputs = mint300Inputs();
    expect(inputs.daySemantics.effectiveAllotmentMinutes).toBe(300);
    expect(inputs.daySemantics.mintEnabled).toBe(true);
    expect(inputs.capacity.mint).toBe(10); // 300 / 30
    expect(inputs.capacity.mint).not.toBe(2); // no hardcoded fallback
  });

  it("mintWalls resolves the 10 selected Mint intervals, not 2", () => {
    const walls = mintWalls(mint300Sequence());
    expect(walls).toHaveLength(10);
    expect(walls[0]).toEqual({ start: 8 * 60 + 30, end: 9 * 60 });
    expect(walls[9]).toEqual({ start: 13 * 60, end: 13 * 60 + 30 }); // 13:00-13:30
  });
});

/* ---------------------------------------------------------------------- */
/* Mint-overlap fixture — FEEDBACK-25 (selected Mint intervals are walls)   */
/* ---------------------------------------------------------------------- */

describe("FEEDBACK-27 Mint overlap is infeasible in the free-gap scan", () => {
  it("moves a dropped row that would straddle a Mint interval after it", () => {
    const walls = mintWalls(mint300Sequence());
    const plan = planOverflow([item("Deep Work", 2)], "08:00", blocksOf, walls);
    // Mint occupies 08:30-13:30, so the next free gap opens at 13:30.
    expect(plan.rows.map((r) => [r.id, r.start, r.end])).toEqual([
      ["Deep Work", "13:30", "14:30"],
    ]);
    expect(plan.infeasible).toEqual([]);
  });

  it("reports rows that cannot fit around Mint as explicit infeasibility", () => {
    const walls = mintWalls(mint300Sequence());
    // Room around 10 Mint blocks (08:30-13:30): 08:00-08:30 (1 block) plus
    // 13:30-24:00 (21 blocks) = 22 blocks. 28 blocks demanded -> at least
    // six rows are explicitly infeasible and NOTHING is staged over a Mint
    // interval.
    const plan = planOverflow(
      Array.from({ length: 7 }, (_, i) => item(`Overflow ${i}`, 4)),
      "08:00",
      blocksOf,
      walls,
    );
    expect(plan.infeasible.length).toBeGreaterThan(0);
    for (const r of plan.rows) {
      const rs = toMin(r.start);
      const re = toMin(r.end);
      for (const w of walls) {
        expect(
          rs >= w.end || re <= w.start,
          `${r.id} (${r.start}-${r.end}) must not overlap Mint ${w.start}-${w.end}`,
        ).toBe(true);
      }
    }
  });
});

/* ---------------------------------------------------------------------- */
/* Controller end-to-end — Mint walls + zero writers                        */
/* ---------------------------------------------------------------------- */

describe("FEEDBACK-27 Mint walls + zero writers through the controller", () => {
  it("overflow never stages a row over selected Mint time and never reaches a writer", async () => {
    const { store, adapter, controller } = harness();
    // Server returns ONLY the 10 Mint rows — every work row is dropped and
    // the free-gap scan must lay them around Mint walls (never over them).
    const proposal = {
      sequence: mint300Sequence().slice(0, 10),
      warnings: [],
      overlapGrants: [],
    };
    // Over-demand day: 28 blocks of work around the 10-block Mint wall.
    const over = mint300Inputs();
    over.assigned = Array.from({ length: 7 }, (_, i) =>
      item(`Overflow ${i}`, 4),
    );
    over.capacity = {
      ...over.capacity,
      selected: 28,
      free: 20 - 10 - 28,
      overassigned: true,
      remaining: "⚠ over · 18 blk",
    };
    vi.spyOn(adapter, "loadPlanInputs").mockResolvedValue({
      ...structuredClone(adapter.scenario.inputs),
      ...over,
    });
    vi.spyOn(adapter, "autoSequence").mockResolvedValue(proposal);
    const liveCommit = vi.spyOn(adapter, "liveCommit");
    const shadowCommit = vi.spyOn(adapter, "shadowCommit");
    const runtimeAction = vi.spyOn(adapter, "runtimeAction");
    const undoRuntimeAction = vi.spyOn(adapter, "undoRuntimeAction");

    await controller.load();
    await controller.saveDaySetup({
      ...store.getState().daySetup,
      confirmed: true,
    });
    await controller.autoSequence();

    const s = store.getState();
    const rows = s.sequence ?? [];
    const walls = mintWalls(rows);
    expect(walls).toHaveLength(10);

    for (const r of rows.filter((row) => row.kind === "work")) {
      const rs = toMin(r.start);
      const re = toMin(r.end);
      // The Mint row itself IS the wall — it may occupy its own interval.
      if (walls.some((w) => w.start === rs && w.end === re)) continue;
      for (const w of walls) {
        expect(
          rs >= w.end || re <= w.start,
          `${r.id} (${r.start}-${r.end}) must not overlap Mint ${w.start}-${w.end}`,
        ).toBe(true);
      }
    }

    // 28 blocks of demand vs 22 free blocks around 10 Mint blocks: the
    // overflow reports explicit infeasibility — never silent placement.
    const warnings = s.validation?.warnings ?? [];
    expect(warnings.some((w) => w.includes("overflow infeasible"))).toBe(true);

    // Zero real writer calls: load -> setup -> sequence never reaches them.
    expect(liveCommit).not.toHaveBeenCalled();
    expect(shadowCommit).not.toHaveBeenCalled();
    expect(runtimeAction).not.toHaveBeenCalled();
    expect(undoRuntimeAction).not.toHaveBeenCalled();
  }, 15000);
});

/* ---------------------------------------------------------------------- */
/* FEEDBACK-28 August 17 incident fixture — stale saved Mint vs OPPD wall   */
/* ---------------------------------------------------------------------- */

describe("FEEDBACK-28 August 17 incident fixture (frontend)", () => {
  /* The August 17 failure: Day Setup saved Mint 15:00-15:30, the OPPD fixed
     wall began at 15:00, and the frontend sent the wall-conflicting Mint row
     in the day-setup payload before the billed judgment. The frontend guard
     is: the /day-setup payload (the only request that can carry Mint rows to
     the server) is filtered against current effective fixed/work walls
     before it is emitted, and the sanitized selection is what the store keeps
     for the next judgment. Backend revalidation remains authoritative. */
  const OPPD_SESSIONS = [
    { id: "mint:morning:08:30", name: "Mint Morning · 08:30", slot: "Morning", start: "08:30", end: "09:00" },
    { id: "mint:afternoon:13:30", name: "Mint Afternoon · 13:30", slot: "Afternoon", start: "13:30", end: "14:00" },
    { id: "mint:afternoon:15:00", name: "Mint Afternoon · 15:00", slot: "Afternoon", start: "15:00", end: "15:30" },
  ];

  function august17Inputs(adapter: FixtureAdapter) {
    return {
      ...structuredClone(adapter.scenario.inputs),
      anchored: [
        ...adapter.scenario.inputs.anchored.filter((a) => a.kind !== "calendar"),
        {
          id: "OPPD meter read", name: "OPPD meter read", kind: "calendar",
          start: "15:00", end: "15:30", durationMin: 30, overlapAllowed: false,
          on: true, skipToday: false, calendarId: "oppd", calendarTitle: "OPPD",
          capacityClass: "fixed",
        },
      ],
      daySemantics: {
        ...adapter.scenario.inputs.daySemantics,
        mintEnabled: true,
        effectiveAllotmentMinutes: 60,
        mintSessions: OPPD_SESSIONS,
      },
      daySetup: {
        ...adapter.scenario.inputs.daySetup,
        workAllotmentMinutes: 60,
        schedulable: { minting: { on: true, n: 2, sessions: [OPPD_SESSIONS[1].id, OPPD_SESSIONS[2].id] } },
      },
    };
  }

  it("filters the stale 15:00-15:30 Mint row out of the payload before judgment", async () => {
    const { store, adapter, controller } = harness();
    vi.spyOn(adapter, "loadPlanInputs").mockResolvedValue(august17Inputs(adapter) as never);
    const save = vi.spyOn(adapter, "saveDaySetup");
    const sequence = vi.spyOn(adapter, "autoSequence");
    const liveCommit = vi.spyOn(adapter, "liveCommit");
    const shadowCommit = vi.spyOn(adapter, "shadowCommit");
    const runtimeAction = vi.spyOn(adapter, "runtimeAction");
    const undoRuntimeAction = vi.spyOn(adapter, "undoRuntimeAction");

    await controller.load();
    await controller.saveDaySetup({
      ...store.getState().daySetup,
      confirmed: true,
      workAllotmentMinutes: 60,
      schedulable: { minting: { on: true, sessions: [OPPD_SESSIONS[1].id, OPPD_SESSIONS[2].id] } },
    });

    // The payload that reaches the server carries only the wall-free session.
    const sent = save.mock.calls[0][0];
    expect(sent.schedulable!.minting.sessions).toEqual([OPPD_SESSIONS[1].id]);
    expect(sent.schedulable!.minting.on).toBe(true);
    expect(sent.schedulable!.minting.n).toBe(1);
    expect(sent.workAllotmentMinutes).toBe(30);
    // The sanitized selection is what the store keeps for the next judgment.
    const minting = store.getState().daySetup.schedulable?.minting;
    expect(minting?.sessions).toEqual([OPPD_SESSIONS[1].id]);
    // No judgment fired from the save path, and no writer was ever reached.
    expect(sequence).not.toHaveBeenCalled();
    expect(liveCommit).not.toHaveBeenCalled();
    expect(shadowCommit).not.toHaveBeenCalled();
    expect(runtimeAction).not.toHaveBeenCalled();
    expect(undoRuntimeAction).not.toHaveBeenCalled();
  }, 15000);
});
