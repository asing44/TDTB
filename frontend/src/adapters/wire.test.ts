/* wire.test.ts — pins every wire→model mapping against the contract fixtures
   captured from the REAL FastAPI routes (scripts/capture_contract_fixtures.py).
   If a backend shape changes, re-capture and these tests say exactly which
   mapping moved (T5 gate: API contract fixtures). */

import { describe, expect, it } from "vitest";
import {
  blocksLabel,
  calendarWarnings,
  daySetupToWire,
  durationMinutes,
  projectAssigned,
  projectCommitReport,
  projectDaySetup,
  projectDaySemantics,
  projectFixedInputs,
  projectPlanInputs,
  projectSequenceResult,
  projectShadow,
  projectValidation,
  rowToWire,
  shapeAssignedWire,
  to24h,
} from "./wire";

import planInputs from "./contract-fixtures/plan-inputs.json";
import planInputsWithSetup from "./contract-fixtures/plan-inputs-with-setup.json";
import planInputsDegraded from "./contract-fixtures/plan-inputs-degraded.json";
import planInputsDayPreset from "./contract-fixtures/plan-inputs-day-preset.json";
import planInputsAllotmentOmitted from "./contract-fixtures/plan-inputs-allotment-omitted.json";
import planInputsAllotmentNull from "./contract-fixtures/plan-inputs-allotment-null.json";
import planInputsAllotmentZero from "./contract-fixtures/plan-inputs-allotment-zero.json";
import planInputsMalformed from "./contract-fixtures/plan-inputs-malformed.json";
import planInputsFingerprintChanged from "./contract-fixtures/plan-inputs-fingerprint-changed.json";
import sequenceOk from "./contract-fixtures/sequence-ok.json";
import validateOk from "./contract-fixtures/validate-ok.json";
import validateFail from "./contract-fixtures/validate-fail.json";
import validateWarn from "./contract-fixtures/validate-warn.json";
import shadowDiff from "./contract-fixtures/shadow-diff.json";
import commitLiveOk from "./contract-fixtures/commit-live-ok.json";
import commitLivePartial from "./contract-fixtures/commit-live-partial.json";

describe("time parsing", () => {
  it("parses 12h and 24h clock strings", () => {
    expect(to24h("7:45 AM")).toBe("07:45");
    expect(to24h("12:00 PM")).toBe("12:00");
    expect(to24h("12:15 AM")).toBe("00:15");
    expect(to24h("8:30 PM")).toBe("20:30");
    expect(to24h("09:15")).toBe("09:15");
    expect(to24h("—")).toBeNull();
    expect(to24h(null)).toBeNull();
  });

  it("parses duration strings", () => {
    expect(durationMinutes("80m")).toBe(80);
    expect(durationMinutes("1h20m")).toBe(80);
    expect(durationMinutes("2h")).toBe(120);
    expect(durationMinutes(45)).toBe(45);
    expect(durationMinutes("—")).toBeNull();
  });

  it("labels blocks", () => {
    expect(blocksLabel(3)).toBe("1hr 30min");
    expect(blocksLabel(2)).toBe("1hr");
    expect(blocksLabel(1)).toBe("30min");
    expect(blocksLabel(0.5)).toBe("15min");
    expect(blocksLabel(2.5)).toBe("1hr 15min");
    expect(blocksLabel(0)).toBe("All day");
  });
});

describe("projectPlanInputs (contract: plan-inputs.json)", () => {
  it("preserves fixed recurring Todoist placement metadata", () => {
    const item = projectAssigned({
      name: "M2.5",
      source: "todoist",
      todoist_id: "meds",
      blocks: 0.5,
      is_recurring: true,
      scheduled_start: "12:00",
    });
    expect(item.isRecurring).toBe(true);
    expect(item.scheduledStart).toBe("12:00");
  });

  const p = projectPlanInputs(planInputs);

  it("projects assigned-only — suggested never crosses the boundary", () => {
    expect(p.assigned.length).toBe(4);
    expect(JSON.stringify(p)).not.toContain("suggested");
  });

  it("maps vault rows with T4-resolved blocks", () => {
    const press = p.assigned.find((i) => i.name === "Sample Press")!;
    expect(press.source).toBe("vault");
    expect(press.blocks).toBe(3); // press duration_min 75 → 3 blocks
    expect(press.durationLabel).toBe("1hr 30min");
    expect(press.path).toBe("50 - Operations/Projects/Sample Press.md");
    expect(press.todoistId).toBeNull();
  });

  it("preserves sequencing metadata on assigned rows", () => {
    const item = projectAssigned({
      name: "Career Ops",
      relates_to: "[[Professional Development]]",
      tags: ["systems"],
    });
    expect(item.relatesTo).toBe("[[Professional Development]]");
    expect(item.labels).toEqual([]);
  });

  it("carries todoist_id for todoist rows (copy-prompt update-by-id contract)", () => {
    const t = p.assigned.find((i) => i.source === "todoist")!;
    expect(t.todoistId).toBe("9001");
  });

  it("maps todoist rows: source, null path, native-duration blocks", () => {
    const t = p.assigned.find((i) => i.name === "Sample Todoist Task")!;
    expect(t.source).toBe("todoist");
    expect(t.path).toBeNull();
    expect(t.blocks).toBe(3); // 90m native
    expect(t.urgency).toBe("3");
  });

  it("id = name (name-keyed sequence identity)", () => {
    for (const i of p.assigned) expect(i.id).toBe(i.name);
  });

  it("maps anchored config rows to 24h times and kinds", () => {
    const mr = p.anchored.find((a) => a.name === "Morning Routine")!;
    expect(mr.kind).toBe("hard");
    expect(mr.start).toBe("07:45");
    expect(mr.durationMin).toBe(80);
    expect(mr.overlapAllowed).toBe(false);
    const live = p.anchored.find((a) => a.name === "Live")!;
    expect(live.kind).toBe("template"); // window + overlap_allowed
    expect(live.overlapAllowed).toBe(true);
    const cal = p.anchored.find((a) => a.name === "Sample Meeting")!;
    expect(cal.kind).toBe("calendar");
    expect(cal.start).toBe("09:15");
    expect(cal.durationMin).toBe(30); // End − Start
  });

  it("preserves calendar identity and capacity classification", () => {
    const projected = projectPlanInputs({
      ...(planInputs as any),
      anchored_blocks: [
        {
          Block: "Work sync",
          Start: "09:30",
          End: "10:20",
          source: "calendar",
          calendar_id: "cal-work",
          calendar_title: "Trinoor",
          capacity_class: "work",
        },
      ],
      capacity: {
        ...(planInputs as any).capacity,
        work_busy: 2,
        work_overflow: 0,
      },
    });
    expect(projected.anchored[0]).toMatchObject({
      calendarId: "cal-work",
      calendarTitle: "Trinoor",
      capacityClass: "work",
    });
    expect(projected.capacity.workBusy).toBe(2);
    expect(projected.capacity.workOverflow).toBe(0);
  });

  it("carries the raw anchored fingerprint and zero-block setup override", () => {
    const projected = projectPlanInputs({
      ...(planInputs as any),
      anchored_source_fingerprint: "raw-anchor-v1",
      day_setup: {
        anchored: [{ id: "Morning Routine", on: true, skip_today: false, time: "08:00", blocks: 0 }],
      },
    });
    expect(projected.anchoredSourceFingerprint).toBe("raw-anchor-v1");
    expect(projected.daySetup.anchored["Morning Routine"].blocks).toBe(0);
  });

  it("maps the time frame", () => {
    expect(p.time.anchor).toMatch(/^\d{2}:\d{2}$/);
    expect(p.time.configEod).toBe("23:45");
    expect(p.time.totalBlocks).toBeGreaterThan(0);
  });

  it("renders capacity server-verbatim", () => {
    expect(p.capacity.total).toBe((planInputs as any).capacity.total);
    expect(p.capacity.remaining).toBe((planInputs as any).capacity.remaining);
    expect(p.capacity.legend).toBe((planInputs as any).capacity.legend);
    expect(p.capacity.availableForSelection).toBe(
      (planInputs as any).capacity.available_for_selection,
    );
  });

  it("valid date + source counts + health", () => {
    expect(p.validDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(p.sourceCounts).toEqual({ vault: 3, todoist: 1, calendar: 1 });
    expect(p.sourceHealth).toBe("ok");
    expect(p.daySetup.confirmed).toBe(false); // no runstate saved yet
  });

  it("FEEDBACK-24: skeleton echo with keys but no flag is NOT confirmed", () => {
    const projected = projectPlanInputs({
      ...(planInputs as any),
      day_setup: { schedulable: {}, work_allotment_minutes: null, anchor: "09:00" },
      day_setup_confirmed: false,
    });
    expect(projected.daySetup.confirmed).toBe(false);
    expect(projected.daySetup.anchor).toBe("09:00");
  });

  it("FEEDBACK-24: explicit flag confirms even an empty setup echo", () => {
    const projected = projectPlanInputs({
      ...(planInputs as any),
      day_setup: {},
      day_setup_confirmed: true,
    });
    expect(projected.daySetup.confirmed).toBe(true);
  });
});

describe("projectPlanInputs with saved Day Setup (plan-inputs-with-setup.json)", () => {
  const p = projectPlanInputs(planInputsWithSetup);

  it("echoes the persisted setup as confirmed", () => {
    expect(p.daySetup.confirmed).toBe(true);
    expect(p.daySetup.anchor).toBe("07:30");
    expect(p.daySetup.eod).toBe("23:00");
    expect(p.daySetup.buffering).toBe("standard");
    expect(p.daySetup.anchored["Live"]).toEqual({
      on: true,
      skipToday: false,
      time: null,
      blocks: null,
    });
    expect(p.daySetup.captures).toEqual({
      intention: "Sample intention",
      forMeegy: "Sample nicety",
      stoic: "Sample stoic",
    });
  });
});

describe("degraded sources (plan-inputs-degraded.json)", () => {
  const p = projectPlanInputs(planInputsDegraded);

  it("surfaces warnings and degraded health", () => {
    expect(p.sourceWarnings.length).toBeGreaterThan(0);
    expect(p.sourceHealth).toBe("degraded");
  });

  it("flags calendar warnings for the fixed-input gate", () => {
    expect(calendarWarnings(p.sourceWarnings).length).toBeGreaterThan(0);
  });
});

describe("T18b additive read contracts", () => {
  it("projects preset metadata, integer-minute allotment, Mint capacity, and config fingerprint", () => {
    const fixtures = [
      planInputs,
      planInputsAllotmentOmitted,
      planInputsDayPreset,
      planInputsAllotmentNull,
      planInputsAllotmentZero,
      planInputsMalformed,
    ] as any[];

    for (const fixture of fixtures) {
      expect(fixture.day_semantics).toBeDefined();
      expect(fixture.planning_config_fingerprint).toMatch(/^[0-9a-f]{64}$/);
      const projected = projectPlanInputs(fixture);
      expect(projected.planningConfigFingerprint).toBe(fixture.planning_config_fingerprint);
      expect(projected.daySemantics.availablePresets.length).toBeGreaterThan(0);
      expect(Number.isInteger(projected.daySemantics.effectiveAllotmentMinutes)).toBe(true);
    }
    expect(projectPlanInputs(planInputsDayPreset).daySemantics.selectedPreset?.name).toBe("Weekend");
    expect(projectPlanInputs(planInputsAllotmentZero).daySemantics.effectiveAllotmentMinutes).toBe(0);
    expect(projectPlanInputs(planInputs).capacity.mint).toBe((planInputs as any).capacity.mint);
  });

  it("consumes fingerprint-change evidence into the read model", () => {
    expect(planInputsFingerprintChanged.planning_config_fingerprint).not.toBe(
      planInputs.planning_config_fingerprint,
    );
    expect(projectPlanInputs(planInputsFingerprintChanged).planningConfigFingerprint).not.toBe(
      projectPlanInputs(planInputs).planningConfigFingerprint,
    );
  });
});

describe("fixed inputs + fingerprint source", () => {
  it("splits calendar commitments from anchored blocks", () => {
    const f = projectFixedInputs(planInputs);
    expect(f.calendar.map((c) => c.name)).toEqual(["Sample Meeting"]);
    expect(f.anchored.map((a) => a.name)).toContain("Morning Routine");
    expect(f.anchored.every((a) => typeof a.on === "boolean")).toBe(true);
  });
});

// FEEDBACK-04 (2026-08-14): quarantined (known-but-unreviewed) calendar rows
// must stay excluded on the read model. The old projection collapsed any
// non-work/non-ignored class to "fixed" — quarantined rows displayed as fixed,
// walled overflow, and entered the fixed-input fingerprint although the server
// excludes them from planning entirely (frozen contract 17).
describe("FEEDBACK-04 event classification", () => {
  const classified = {
    ...(planInputs as any),
    anchored_blocks: [
      {
        Block: "Cooking", Start: "20:30", End: "21:00",
        source: "calendar", calendar_id: "cal-cooking",
        calendar_title: "Personal", capacity_class: "fixed",
      },
      {
        Block: "Trivia Night", Start: "19:00", End: "20:00",
        source: "calendar", calendar_id: "cal-trivia",
        calendar_title: "Trivia", capacity_class: "work",
      },
      {
        Block: "Steelers Game", Start: "20:00", End: "22:00",
        source: "calendar", calendar_id: "cal-sports",
        calendar_title: "Sports", capacity_class: "quarantined",
      },
    ],
  };

  it("projects quarantined rows as quarantined — never Fixed", () => {
    const rows = projectPlanInputs(classified).anchored.filter(
      (a) => a.kind === "calendar",
    );
    expect(rows.map((a) => [a.name, a.capacityClass])).toEqual([
      ["Cooking", "fixed"],
      ["Trivia Night", "work"],
      ["Steelers Game", "quarantined"],
    ]);
  });

  it("excludes quarantined rows from fixed-input calendar commitments", () => {
    const f = projectFixedInputs(classified);
    expect(f.calendar.map((c) => c.name)).toEqual(["Cooking", "Trivia Night"]);
  });
});

describe("sequence + validation projections", () => {
  it("maps /sequence rows (server-injected rows included) as work rows", () => {
    const r = projectSequenceResult(sequenceOk);
    expect(r.sequence.length).toBe(5);
    expect(r.sequence.every((row) => row.kind === "work")).toBe(true);
    expect(r.sequence[0]).toMatchObject({ id: "Make", start: "10:00", end: "11:00" });
    expect(r.warnings).toEqual([]);
    expect(r.overlapGrants).toEqual([]);
  });

  it("projects exact overlap grants without weakening their identity", () => {
    const grant = {
      primary_id: "Make",
      companion_id: "Morning Routine",
      primary_interval: { start: "07:50", end: "08:10" },
      companion_interval: { start: "07:45", end: "09:05" },
      reason: "intentional companion work",
      planning_config_fingerprint: "fp-current",
    };
    const r = projectSequenceResult({ sequence: [], overlap_grants: [grant], warnings: [] });
    expect(r.overlapGrants[0]).toEqual({
      primaryId: "Make",
      companionId: "Morning Routine",
      primaryInterval: { start: "07:50", end: "08:10" },
      companionInterval: { start: "07:45", end: "09:05" },
      reason: "intentional companion work",
      planningConfigFingerprint: "fp-current",
    });
  });

  it("marks backdrop rows as zone kind", () => {
    const r = projectSequenceResult({
      sequence: [{ id: "🟡 Trinoor : AM", start: "09:00", end: "12:00", zone: "work_hours", backdrop: true }],
      warnings: [],
    });
    expect(r.sequence[0].kind).toBe("zone");
  });

  it("maps validate ok/fail verbatim", () => {
    expect(projectValidation(validateOk)).toEqual({ ok: true, hardErrors: [], warnings: [] });
    const fail = projectValidation(validateFail);
    expect(fail.ok).toBe(false);
    expect(fail.hardErrors.length).toBeGreaterThan(0);
  });

  it("projects dict soft warnings to their verbatim detail strings (validate-warn.json)", () => {
    // The server validator emits warnings as {id, rule|kind, detail} dicts;
    // the cockpit renders (and accepts, LD 24) the human detail — never
    // "[object Object]".
    const v = projectValidation(validateWarn);
    expect(v.ok).toBe(true);
    expect(v.warnings).toEqual([
      "⚠ past EOD — ends 23:30, effective EOD 23:00",
      "1 task(s) scheduled within the 'Deep Work' window — place its floating block in a free gap",
      "placed at 09:00, outside evening window 18:00-22:00",
    ]);
    for (const w of v.warnings) expect(w).not.toContain("[object");
  });
});

describe("projectShadow (shadow-diff.json)", () => {
  const d = projectShadow(shadowDiff);

  it("flattens manifest entries with classification", () => {
    expect(d.entries.length).toBeGreaterThan(0);
    for (const e of d.entries) {
      expect(["todoist", "vault", "calendar"]).toContain(e.system);
      expect(e.name.length).toBeGreaterThan(0);
      expect(
        ["would-create", "would-update", "no-op", "conflict", "unavailable"],
      ).toContain(e.classification);
    }
  });

  it("carries unavailable surfaces + counts", () => {
    expect(Array.isArray(d.unavailableSurfaces)).toBe(true);
    const total = Object.values(d.counts).reduce((a, b) => a + b, 0);
    expect(total).toBe(d.entries.length);
  });
});

describe("projectCommitReport (commit-live-*.json)", () => {
  it("ok report → status ok, all surfaces ok", () => {
    const r = projectCommitReport(commitLiveOk);
    expect(r.status).toBe("ok");
    expect(r.surfaces.length).toBe(5);
    expect(r.surfaces.every((s) => s.status === "ok")).toBe(true);
    expect(r.verifyFailures).toEqual([]);
  });

  it("partial report → status partial, failed surface carries its error", () => {
    const r = projectCommitReport(commitLivePartial);
    expect(r.status).toBe("partial");
    const failed = r.surfaces.find((s) => s.status === "failed")!;
    expect(failed.system).toBe("todoist");
    expect(failed.detail).toContain("unavailable");
  });

  it("projects structured due verification detail with canonical fields", () => {
    const r = projectCommitReport({
      ok: false,
      surfaces: {
        todoist: {
          status: "failed",
          error: "todoist: 'Press' due mismatch (intent 7 PM, live 11 PM)",
        },
      },
      verify_failures: ["todoist: 'Press' due mismatch (intent 7 PM, live 11 PM)"],
      verify_details: [
        {
          kind: "due",
          name: "Press",
          intent: "19:00",
          live: "23:00",
          live_raw: "2026-07-12T23:00:00Z",
          live_timezone: "America/New_York",
          reason: "mismatch",
          message: "todoist: 'Press' due mismatch (intent 7 PM, live 11 PM)",
        },
      ],
    });
    expect(r.status).toBe("failed");
    expect(r.verifyFailures).toEqual([
      "todoist: 'Press' due mismatch (intent 7 PM, live 11 PM)",
    ]);
    // machine fields stay canonical — 24h, raw ISO, IANA timezone
    expect(r.verifyDetails).toEqual([
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
    ]);
  });

  it("legacy reports without verify_details still project cleanly", () => {
    const r = projectCommitReport(commitLivePartial);
    expect(r.verifyDetails).toBeUndefined();
    expect(r.verifyFailures).toEqual([]);
  });
});

describe("model → wire body builders", () => {
  it("daySetupToWire maps capture keys to wire names", () => {
    const w = daySetupToWire({
      anchor: "07:30",
      eod: null,
      buffering: "minimal",
      anchored: { Live: { on: true, skipToday: false, time: "13:00" } },
      captures: { intention: "a", forMeegy: "b", stoic: "c" },
      confirmed: true,
    });
    expect(w.anchored).toEqual([{ id: "Live", on: true, skip_today: false, time: "13:00" }]);
    expect(w.captures).toEqual({
      intention: "a",
      megan_nicety: "b",
      stoic_intention: "c",
    });
  });

  it("daySetupToWire preserves an explicit zero-block anchored override", () => {
    const w = daySetupToWire({
      anchor: null,
      eod: null,
      buffering: "standard",
      anchored: { Live: { on: true, skipToday: false, time: "13:00", blocks: 0 } },
      captures: { intention: "", forMeegy: "", stoic: "" },
      confirmed: true,
    });
    expect(w.anchored[0].blocks).toBe(0);
  });

  it("day setup carries selected Mint sessions", () => {
    const w = daySetupToWire({
      anchor: null,
      eod: null,
      buffering: "standard",
      anchored: {},
      captures: { intention: "", forMeegy: "", stoic: "" },
      confirmed: true,
      schedulable: { minting: { on: true, n: 1, sessions: ["mint:morning"] } },
    });
    expect(w.schedulable).toEqual({
      minting: { on: true, n: 1, sessions: ["mint:morning"] },
    });
    expect(projectDaySetup(w, true).schedulable?.minting.sessions).toEqual(["mint:morning"]);
  });

  it("rowToWire round-trips backdrop marking", () => {
    expect(rowToWire({ id: "X", start: "09:00", end: "09:30", zone: null, kind: "work" }))
      .toEqual({ id: "X", start: "09:00", end: "09:30", zone: null });
    expect(rowToWire({ id: "Z", start: "09:00", end: "12:00", zone: "work_hours", kind: "zone" }))
      .toEqual({ id: "Z", start: "09:00", end: "12:00", zone: "work_hours", backdrop: true });
  });

  it("shapeAssignedWire drops excluded rows, applies overrides, keeps wire shape", () => {
    const raw = (planInputs as any).digest.assigned;
    const shaped = shapeAssignedWire(raw, [
      { id: "Make", blocks: 4 }, // duration override
      { id: "Sample Project", blocks: 1 },
    ]);
    expect(shaped.map((r: any) => r.name)).toEqual(["Make", "Sample Project"]);
    expect(shaped[0].blocks).toBe(4);
    expect(shaped[0].id).toBe("Make"); // id=name T1 contract
    expect(shaped[0].path).toBe(raw[0].path); // wire row otherwise verbatim
    // Upstream truth untouched (locked decision 16):
    expect(shaped.every((r: any) => r.assigned === true)).toBe(true);
    expect(raw[0].blocks).toBe(2); // input not mutated
  });
});

describe("projectDaySetup edge shapes", () => {
  it("empty runstate → unconfirmed defaults", () => {
    const d = projectDaySetup({}, false);
    expect(d.confirmed).toBe(false);
    expect(d.buffering).toBe("standard");
    expect(d.anchored).toEqual({});
  });

  // FEEDBACK-24: confirmation is the server's explicit flag, never inferred
  // from echoed keys — a skeleton runstate can echo schedulable/anchor keys
  // without the user ever confirming Day Setup.
  it("echoed skeleton keys without the flag stay unconfirmed", () => {
    const d = projectDaySetup({ schedulable: {}, anchor: "09:00" }, false);
    expect(d.confirmed).toBe(false);
    expect(d.anchor).toBe("09:00");
  });

  it("the explicit confirmation flag drives confirmed", () => {
    const d = projectDaySetup({}, true);
    expect(d.confirmed).toBe(true);
  });

  it("projects Mint session options and object-shaped enabled zones", () => {
    const d = projectDaySemantics({
      enabled_zones: [{ name: "work_hours" }],
      mint_sessions: [{
        id: "mint:morning",
        name: "Mint Morning",
        slot: "Morning",
        start: "8:30 AM",
        end: "12:30 PM",
      }],
    });
    expect(d.enabledZones).toEqual(["work_hours"]);
    expect(d.mintSessions).toEqual([{
      id: "mint:morning",
      name: "Mint Morning",
      slot: "Morning",
      start: "08:30",
      end: "12:30",
    }]);
  });
});
