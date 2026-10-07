/* wire.test.ts — pins every wire→model mapping against the contract fixtures
   captured from the REAL FastAPI routes (scripts/capture_contract_fixtures.py).
   If a backend shape changes, re-capture and these tests say exactly which
   mapping moved (T5 gate: API contract fixtures). */

import { describe, expect, it } from "vitest";
import {
  blocksLabel,
  calendarWarnings,
  capacitiesAssignmentWarnings,
  capacitiesCoverageOf,
  capacitiesSettingsToWire,
  capacitiesSourceToWire,
  daySetupToWire,
  emptyTagCatalog,
  isCanonicalTagId,
  projectTagExclusionSettings,
  tagExclusionSettingsToWire,
  durationMinutes,
  durationSourceOf,
  isCanonicalCapacitiesStructureId,
  itemIdentity,
  projectAssigned,
  projectCapacitiesSettings,
  projectCapacitiesSource,
  projectCommitReport,
  projectDaySetup,
  projectDaySemantics,
  projectDurationMemoryReset,
  projectDurationMemorySave,
  projectFixedInputs,
  projectPlanInputs,
  projectSequenceResult,
  projectShadow,
  sourceHealthOf,
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
import type { CapacitiesSource, CapacitiesSourceStructure } from "../model/types";
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

  it("preserves a namespaced Capacities identity without treating it as vault", () => {
    const item = projectAssigned({
      name: "Ship project",
      source: "capacities",
      path: "capacities://space-1/object-1",
      identity: "capacities:space-1:custom-project:object-1",
      duration_minutes: 60,
      blocks: 2,
    });
    expect(item.source).toBe("capacities");
    expect(item.path).toBe("capacities://space-1/object-1");
    expect(item.identity).toBe("capacities:space-1:custom-project:object-1");
    expect(item.todoistId).toBeNull();
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

describe("P6-01 grouping metadata projection", () => {
  it("projects vault tags without losing labels, relatesTo, or source identity", () => {
    const digest = (planInputs as any).digest;
    const vaultPath = "50 - Operations/Projects/Grouped parent.md";
    const projected = projectPlanInputs({
      ...(planInputs as any),
      digest: {
        ...digest,
        assigned: [
          {
            ...digest.assigned[0],
            name: "Grouped parent",
            path: vaultPath,
            source: "vault",
            tags: ["systems", "household"],
            relates_to: "[[Professional Development]]",
          },
          {
            ...digest.assigned[3],
            name: "Grouped task",
            path: "todoist://grouped-task",
            source: "todoist",
            todoist_id: "grouped-task",
            labels: ["existing-label"],
          },
        ],
      },
    });

    const vault = projected.assigned[0] as typeof projected.assigned[number] & {
      tags?: string[];
    };
    const todoist = projected.assigned[1];
    // P6-02 must add this additive field; existing relationship, label, and
    // identity fields remain part of the established AssignedItem contract.
    expect(vault.tags).toEqual(["systems", "household"]);
    expect(vault.labels).toEqual([]);
    expect(vault.relatesTo).toBe("[[Professional Development]]");
    expect(vault.identity).toBe(vaultPath);
    expect(todoist.labels).toEqual(["existing-label"]);
    expect(todoist.identity).toBe("todoist:grouped-task");
  });

  it("keeps a selected Mint session as one marked backdrop row", () => {
    const result = projectSequenceResult({
      sequence: [{
        id: "Mint:morning",
        start: "08:30",
        end: "09:00",
        zone: "Mint",
        backdrop: true,
        mint_session: true,
      }],
      warnings: [],
    });
    expect(result.sequence).toHaveLength(1);
    expect(result.sequence[0]).toMatchObject({
      id: "Mint:morning",
      kind: "zone",
      wire: expect.objectContaining({ mint_session: true }),
    });
  });
});

describe("degraded sources (plan-inputs-degraded.json)", () => {
  const p = projectPlanInputs(planInputsDegraded);

  it("surfaces warnings and degraded health", () => {
    expect(p.sourceWarnings.length).toBeGreaterThan(0);
    expect(p.sourceHealth).toBe("degraded");
  });

  it("flags calendar read failures for the fixed-input gate", () => {
    expect(calendarWarnings(p.sourceWarnings).length).toBeGreaterThan(0);
  });

  it("treats the known reminder omission as advisory, not degraded", () => {
    const warning = "Calendar omission: 2.0M: zero or negative duration (reminder-style marker)";
    expect(calendarWarnings([warning])).toEqual([]);
    expect(sourceHealthOf([warning])).toBe("ok");
  });

  it("keeps other calendar diagnostics on the fixed-input gate", () => {
    const warning = "Calendar: 1 duplicate event representation(s) merged by canonical identity";
    expect(calendarWarnings([warning])).toEqual([warning]);
    expect(sourceHealthOf([warning])).toBe("degraded");
  });
});

describe("Capacities partial coverage (source_warnings)", () => {
  const budgetWarning =
    "Capacities partial — 20 evaluated · 51 deferred across contributing " +
    "structures. Content-read budget reached. Wait at least a minute, then " +
    "Refresh sources to continue.";
  const rateWarning =
    "Capacities partial — 2 evaluated · 2 deferred across contributing " +
    "structures. Provider rate limit (30 requests per minute) reached. Wait " +
    "at least a minute, then Refresh sources to continue.";

  it("parses the evaluated/deferred counts and keeps the warning verbatim", () => {
    expect(capacitiesCoverageOf([budgetWarning])).toEqual({
      warnings: [budgetWarning],
      evaluated: 20,
      deferred: 51,
      limit: "content-read budget",
    });
  });

  it("distinguishes the provider rate-limit variant", () => {
    const c = capacitiesCoverageOf([rateWarning]);
    expect(c!.limit).toBe("provider rate limit");
    expect(c!.deferred).toBe(2);
    expect(c!.warnings).toEqual([rateWarning]);
  });

  it("is null when no Capacities partial warning is present", () => {
    expect(capacitiesCoverageOf([])).toBeNull();
    expect(capacitiesCoverageOf(["Calendar read failed (timeout)"])).toBeNull();
  });

  it("keeps an unparsable Capacities warning visible with unknown counts", () => {
    const warning = "Capacities partial — details unavailable";
    const c = capacitiesCoverageOf([warning]);
    expect(c!.warnings).toEqual([warning]);
    expect(c!.evaluated).toBeNull();
    expect(c!.deferred).toBeNull();
    expect(c!.limit).toBe("unknown");
  });

  it("a Capacities partial read is degraded health but not a calendar gate", () => {
    const p = projectPlanInputs({ ...planInputs, source_warnings: [budgetWarning] });
    expect(p.sourceWarnings).toEqual([budgetWarning]);
    expect(p.sourceHealth).toBe("degraded");
    expect(calendarWarnings(p.sourceWarnings)).toEqual([]);
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

  it("projects additive structured diagnostics with canonical intervals", () => {
    const v = projectValidation({
      ok: false,
      hard_errors: ["A overlaps Dentist"],
      warnings: [],
      diagnostics: [{
        rule: "calendar_overlap",
        severity: "error",
        detail: "A overlaps Dentist",
        affected_rows: ["A", "Dentist"],
        intervals: [
          { id: "A", start: "09:15", end: "10:30" },
          { id: "Dentist", start: "9:00 AM", end: "10:00 AM" },
        ],
      }],
    });
    expect(v.diagnostics).toEqual([{
      rule: "calendar_overlap",
      severity: "error",
      detail: "A overlaps Dentist",
      affectedRows: ["A", "Dentist"],
      intervals: [
        { id: "A", start: "09:15", end: "10:30" },
        { id: "Dentist", start: "09:00", end: "10:00" },
      ],
    }]);
    expect(v.hardErrors).toEqual(["A overlaps Dentist"]);
    expect(v.warnings).toEqual([]);
  });

  it("keeps a contradictory hard-error diagnostic blocking", () => {
    const v = projectValidation({
      ok: false,
      hard_errors: ["X"],
      warnings: [],
      diagnostics: [{
        rule: "contradictory_payload",
        severity: "warning",
        detail: "X",
        affected_rows: ["X"],
        intervals: [],
      }],
    });

    expect(v.diagnostics?.[0].severity).toBe("error");
  });

  it("keeps malformed additive diagnostics safe without weakening legacy fields", () => {
    const v = projectValidation({
      ok: true,
      hard_errors: [],
      warnings: ["legacy warning"],
      diagnostics: "legacy diagnostic payload",
    });
    expect(v.warnings).toEqual(["legacy warning"]);
    expect(v.diagnostics).toEqual([{
      rule: "validation",
      severity: "warning",
      detail: "legacy diagnostic payload",
      affectedRows: [],
      intervals: [],
    }]);
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

  it("emits an explicit per-item time-adjustment permission", () => {
    const raw = [
      { name: "Native", blocks: 2, allow_time_adjustment: true },
      { name: "Other", blocks: 1 },
    ];
    const shaped = shapeAssignedWire(
      raw,
      [{ id: "Native", blocks: 2 }, { id: "Other", blocks: 1 }],
      { Native: true },
    );
    expect(shaped.map((row) => row.allow_time_adjustment)).toEqual([true, false]);
    // The source payload cannot smuggle an opt-in through for another item.
    expect(shapeAssignedWire(raw, [{ id: "Native", blocks: 2 }])[0].allow_time_adjustment).toBe(false);
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

  it("projects the backend Mint sub-default signal with session options", () => {
    const d = projectDaySemantics({
      enabled_zones: [{ name: "work_hours" }],
      effective_allotment_minutes: 180,
      default_allotment_minutes: 240,
      mint_enabled: true,
      mint_below_default: true,
      mint_sessions: [{
        id: "mint:morning",
        name: "Mint Morning",
        slot: "Morning",
        start: "8:30 AM",
        end: "12:30 PM",
      }],
    });
    expect(d.enabledZones).toEqual(["work_hours"]);
    expect(d.mintBelowDefault).toBe(true);
    expect(d.mintSessions).toEqual([{
      id: "mint:morning",
      name: "Mint Morning",
      slot: "Morning",
      start: "08:30",
      end: "12:30",
    }]);
  });
});

describe("duration-memory wire projection (MVP)", () => {
  it("projectAssigned carries the canonical identity from wire identity fields", () => {
    const todoist = projectAssigned({
      name: "Sample Todoist Task", path: "todoist://9001", source: "todoist",
      todoist_id: "9001", blocks: 3,
    });
    expect(todoist.identity).toBe("todoist:9001");
    const vault = projectAssigned({
      name: "Make", path: "50 - Operations/Projects/Make.md", blocks: 2,
    });
    expect(vault.identity).toBe("50 - Operations/Projects/Make.md");
  });

  it("projectAssigned maps duration_source to a remembered/source label", () => {
    const remembered = projectAssigned({
      name: "Press", path: "50 - Operations/Pursuits/Press.md",
      duration_source: "remembered", blocks: 4,
    });
    expect(remembered.durationSource).toBe("remembered");
    const native = projectAssigned({
      name: "Task", path: "todoist://1", source: "todoist",
      todoist_id: "1", duration_source: "native", blocks: 2,
    });
    expect(native.durationSource).toBe("native");
    const tag = projectAssigned({
      name: "Task", path: "todoist://2", source: "todoist",
      todoist_id: "2", duration_source: "tag:dur45", blocks: 1,
    });
    expect(tag.durationSource).toBe("tag");
  });

  it("absent or unknown duration_source projects as default (source-resolved)", () => {
    const row = projectAssigned({
      name: "Make", path: "50 - Operations/Projects/Make.md", blocks: 2,
    });
    expect(row.durationSource).toBe("default");
    const weird = projectAssigned({
      name: "Make", path: "50 - Operations/Projects/Make.md",
      duration_source: "legacy-label", blocks: 2,
    });
    expect(weird.durationSource).toBe("default");
  });

  it("itemIdentity mirrors the backend canonical identity rule", () => {
    expect(itemIdentity({ source: "todoist", todoistId: "9001", path: "todoist://9001" })).toBe("todoist:9001");
    expect(itemIdentity({ source: "vault", path: "50 - Operations/Pursuits/Press.md" })).toBe("50 - Operations/Pursuits/Press.md");
    expect(itemIdentity({ source: "vault", path: null })).toBeNull();
    expect(itemIdentity({ source: "todoist", todoistId: null, path: null })).toBeNull();
  });

  it("durationSourceOf coerces resolver labels and fails open to default", () => {
    expect(durationSourceOf("remembered")).toBe("remembered");
    expect(durationSourceOf("tag:dur45")).toBe("tag");
    expect(durationSourceOf("native")).toBe("native");
    expect(durationSourceOf("preset")).toBe("preset");
    expect(durationSourceOf("type")).toBe("type");
    expect(durationSourceOf("default")).toBe("default");
    expect(durationSourceOf(null)).toBe("default");
    expect(durationSourceOf("")).toBe("default");
  });

  it("save projection preserves identity, minutes, and forces remembered", () => {
    const r = projectDurationMemorySave({
      identity: "vault:50 - Operations/Pursuits/Press.md", minutes: 90,
    });
    expect(r).toEqual({
      identity: "vault:50 - Operations/Pursuits/Press.md",
      minutes: 90,
      source: "remembered",
    });
  });

  it("reset projection preserves identity, fallback minutes, and source label", () => {
    // FT-01 backend shape: the reset route returns duration_minutes.
    const r = projectDurationMemoryReset({
      identity: "todoist:9001", duration_minutes: 30, duration_source: "default",
    });
    expect(r).toEqual({ identity: "todoist:9001", minutes: 30, source: "default" });
    const rememberedFallback = projectDurationMemoryReset({
      identity: "vault:x", duration_minutes: 45, duration_source: "native",
    });
    expect(rememberedFallback.source).toBe("native");
    // Tolerant of the save-style `minutes` field too.
    const legacy = projectDurationMemoryReset({
      identity: "vault:x", minutes: 60, duration_source: "default",
    });
    expect(legacy.minutes).toBe(60);
  });

  // FT-05 F2: a reset response with found:false/null fallback must project
  // minutes as null — never coerce the missing fallback to zero (All day).
  it("reset projection keeps minutes null when the server reports no fallback", () => {
    const r = projectDurationMemoryReset({
      ok: true, identity: "todoist:9001", removed: true,
      duration_minutes: null, duration_source: null, found: false,
    });
    expect(r).toEqual({ identity: "todoist:9001", minutes: null, source: "default" });
    const absent = projectDurationMemoryReset({
      ok: true, identity: "vault:x", removed: true, found: false,
    });
    expect(absent.minutes).toBeNull();
  });

  // FT-05 F1: exact remembered minutes must survive the GET projection — the
  // label renders 45min, never a 30-minute-grid-rounded "1hr".
  it("projectAssigned preserves exact remembered minutes in the label", () => {
    const exact = projectAssigned({
      name: "Press", path: "50 - Operations/Pursuits/Press.md",
      duration_source: "remembered", blocks: 1.5, duration_minutes: 45,
    });
    expect(exact.blocks).toBe(1.5);
    expect(exact.durationLabel).toBe("45min");
    // Defensive: an exact duration_minutes always wins over a rounded blocks
    // value for the user-visible label.
    const roundedBlocks = projectAssigned({
      name: "Press", path: "50 - Operations/Pursuits/Press.md",
      duration_source: "remembered", blocks: 2, duration_minutes: 45,
    });
    expect(roundedBlocks.blocks).toBe(2);
    expect(roundedBlocks.durationLabel).toBe("45min");
  });
});

describe("capacities settings active_structures (additive schema v1)", () => {
  const baseSettings = () => ({
    version: 1,
    revision: 0,
    native_task_auto: {
      active_enabled: true,
      due_enabled: true,
      deadline_enabled: true,
      deadline_horizon_days: 2,
    },
    excluded: {},
  });

  it("projects an absent active_structures key to [] (older backend)", () => {
    const projected = projectCapacitiesSettings({ persisted: false, settings: baseSettings() });
    expect(projected.activeStructures).toEqual([]);
  });

  it("parses a well-formed active_structures object and sorts the ids", () => {
    const projected = projectCapacitiesSettings({
      persisted: false,
      settings: { ...baseSettings(), active_structures: { b: true, a: true, "custom-project": true } },
    });
    expect(projected.activeStructures).toEqual(["a", "b", "custom-project"]);
  });

  const malformedObjects: Array<[string, unknown]> = [
    ["an array", []],
    ["a string", "custom-project"],
    ["null", null],
  ];
  for (const [label, bad] of malformedObjects) {
    it(`throws when active_structures is ${label}`, () => {
      expect(() =>
        projectCapacitiesSettings({
          persisted: false,
          settings: { ...baseSettings(), active_structures: bad },
        }),
      ).toThrow(/active_structures/);
    });
  }

  const malformedEntries: Array<[string, Record<string, unknown>]> = [
    ["a non-true flag", { "custom-project": 1 }],
    ["a false flag", { "custom-project": false }],
    ["an empty id", { "": true }],
    ["a whitespace-padded id", { " custom-project ": true }],
    ["an embedded-whitespace id", { "custom project": true }],
  ];
  for (const [label, bad] of malformedEntries) {
    it(`throws when active_structures contains ${label}`, () => {
      expect(() =>
        projectCapacitiesSettings({
          persisted: false,
          settings: { ...baseSettings(), active_structures: bad },
        }),
      ).toThrow(/invalid structure id/);
    });
  }

  it("emits a sorted, deduplicated active_structures object in the save body", () => {
    const wire = capacitiesSettingsToWire({
      expectedRevision: 0,
      nativeTaskAuto: { activeEnabled: true, dueEnabled: true, deadlineEnabled: true, deadlineHorizonDays: 2 },
      excluded: [],
      activeStructures: ["b", "a", "a"],
      nativeTaskStructures: [],
      activeStatuses: [],
      assignedStructures: {},
    });
    expect(wire.active_structures).toEqual({ a: true, b: true });
  });

  it("throws on an invalid active-structure id in the save draft", () => {
    expect(() =>
      capacitiesSettingsToWire({
        expectedRevision: 0,
        nativeTaskAuto: { activeEnabled: true, dueEnabled: true, deadlineEnabled: true, deadlineHorizonDays: 2 },
        excluded: [],
        activeStructures: ["bad id"],
        nativeTaskStructures: [],
        activeStatuses: [],
        assignedStructures: {},
      }),
    ).toThrow(/invalid structure id/);
  });

  it("projects an absent available_structures key to [] (older backend)", () => {
    const projected = projectCapacitiesSettings({ persisted: false, settings: baseSettings() });
    expect(projected.availableStructures).toEqual([]);
  });

  it("parses a well-formed available_structures array, deduplicated and sorted", () => {
    const projected = projectCapacitiesSettings({
      persisted: false,
      settings: baseSettings(),
      available_structures: [
        "custom-project",
        "0d194525-c5a1-4af5-bb62-202b83006b5e",
        "custom-project",
      ],
    });
    expect(projected.availableStructures).toEqual([
      "0d194525-c5a1-4af5-bb62-202b83006b5e",
      "custom-project",
    ]);
  });

  it("throws when available_structures is not an array", () => {
    for (const bad of [{ "custom-project": true }, "custom-project", null, 1]) {
      expect(() =>
        projectCapacitiesSettings({
          persisted: false,
          settings: baseSettings(),
          available_structures: bad,
        }),
      ).toThrow(/available_structures/);
    }
  });

  it("throws when available_structures contains an invalid id", () => {
    for (const bad of [[""], [" x "], ["x y"], [1], ["custom-project", null]]) {
      expect(() =>
        projectCapacitiesSettings({
          persisted: false,
          settings: baseSettings(),
          available_structures: bad,
        }),
      ).toThrow(/available_structures/);
    }
  });

  it("projects an absent structure_titles key to {} (older backend)", () => {
    const projected = projectCapacitiesSettings({ persisted: false, settings: baseSettings() });
    expect(projected.structureTitles).toEqual({});
  });

  it("parses a well-formed structure_titles map, including an id-titled structure", () => {
    const projected = projectCapacitiesSettings({
      persisted: false,
      settings: baseSettings(),
      structure_titles: {
        "0d194525-c5a1-4af5-bb62-202b83006b5e": "Project",
        "6aa7b02a-4315-47d1-9cfb-0c0cdac0950c": "Press",
        "custom-project": "custom-project",
      },
    });
    expect(projected.structureTitles).toEqual({
      "0d194525-c5a1-4af5-bb62-202b83006b5e": "Project",
      "6aa7b02a-4315-47d1-9cfb-0c0cdac0950c": "Press",
      "custom-project": "custom-project",
    });
  });

  it("throws when structure_titles is not an object", () => {
    for (const bad of [["custom-project", "Project"], "Project", null, 1]) {
      expect(() =>
        projectCapacitiesSettings({
          persisted: false,
          settings: baseSettings(),
          structure_titles: bad,
        }),
      ).toThrow(/structure_titles/);
    }
  });

  it("throws when structure_titles contains an invalid key or title", () => {
    for (const bad of [
      { "": "Project" },
      { " custom-project ": "Project" },
      { "custom project": "Project" },
      { "custom-project": "" },
      { "custom-project": "   " },
      { "custom-project": 1 },
      { "custom-project": null },
    ]) {
      expect(() =>
        projectCapacitiesSettings({
          persisted: false,
          settings: baseSettings(),
          structure_titles: bad,
        }),
      ).toThrow(/structure_titles/);
    }
  });

  it("recognizes canonical structure ids", () => {
    expect(isCanonicalCapacitiesStructureId("custom-project")).toBe(true);
    expect(isCanonicalCapacitiesStructureId("0d194525-c5a1-4af5-bb62-202b83006b5e")).toBe(true);
    expect(isCanonicalCapacitiesStructureId("")).toBe(false);
    expect(isCanonicalCapacitiesStructureId(" x ")).toBe(false);
    expect(isCanonicalCapacitiesStructureId("x y")).toBe(false);
    expect(isCanonicalCapacitiesStructureId(1)).toBe(false);
  });
});

describe("capacities settings admission keys (additive schema v1)", () => {
  const baseSettings = () => ({
    version: 1,
    revision: 0,
    native_task_auto: {
      active_enabled: true,
      due_enabled: true,
      deadline_enabled: true,
      deadline_horizon_days: 2,
    },
    excluded: {},
  });

  it("projects absent native_task_structures and active_statuses to the documented defaults", () => {
    const projected = projectCapacitiesSettings({ persisted: false, settings: baseSettings() });
    // A legacy version-1 file (or an older backend) has no keys; the store
    // would read that as the built-in native structures / the single active
    // status, so the client must project the same documented default rather
    // than the dangerous empty set.
    expect(projected.nativeTaskStructures).toEqual(["RootTask", "Task"]);
    expect(projected.activeStatuses).toEqual(["active"]);
  });

  it("parses the plain admission lists, deduplicated and sorted", () => {
    const projected = projectCapacitiesSettings({
      persisted: false,
      settings: {
        ...baseSettings(),
        native_task_structures: ["Task", "RootTask", "Task"],
        active_statuses: ["In Progress", "active", "active"],
      },
    });
    expect(projected.nativeTaskStructures).toEqual(["RootTask", "Task"]);
    expect(projected.activeStatuses).toEqual(["In Progress", "active"]);
  });

  const malformedAdmissions: Array<[string, string, unknown]> = [
    ["native_task_structures", "a bare string", "RootTask"],
    ["native_task_structures", "an object", { RootTask: true }],
    ["native_task_structures", "an empty entry", [""]],
    ["native_task_structures", "a non-string entry", ["RootTask", 1]],
    ["native_task_structures", "null", null],
    ["active_statuses", "a bare string", "active"],
    ["active_statuses", "an object", { active: true }],
    ["active_statuses", "an empty entry", ["active", ""]],
    ["active_statuses", "a non-string entry", [null]],
  ];
  for (const [key, label, bad] of malformedAdmissions) {
    it(`throws when ${key} is ${label}`, () => {
      expect(() =>
        projectCapacitiesSettings({
          persisted: false,
          settings: { ...baseSettings(), [key]: bad },
        }),
      ).toThrow(new RegExp(key));
    });
  }

  it("always emits both admission keys in the save body, even when empty", () => {
    const wire = capacitiesSettingsToWire({
      expectedRevision: 0,
      nativeTaskAuto: { activeEnabled: true, dueEnabled: true, deadlineEnabled: true, deadlineHorizonDays: 2 },
      excluded: [],
      activeStructures: [],
      nativeTaskStructures: [],
      activeStatuses: [],
      assignedStructures: {},
    });
    // Full replacement: the key must be present, never omitted — omission
    // would make the server reset it to the documented default.
    expect("native_task_structures" in wire).toBe(true);
    expect("active_statuses" in wire).toBe(true);
    expect(wire.native_task_structures).toEqual([]);
    expect(wire.active_statuses).toEqual([]);
  });

  it("emits sorted, deduplicated admission lists in the save body", () => {
    const wire = capacitiesSettingsToWire({
      expectedRevision: 0,
      nativeTaskAuto: { activeEnabled: true, dueEnabled: true, deadlineEnabled: true, deadlineHorizonDays: 2 },
      excluded: [],
      activeStructures: [],
      nativeTaskStructures: ["Task", "RootTask", "Task"],
      activeStatuses: ["active", "In Progress", "active"],
      assignedStructures: {},
    });
    expect(wire.native_task_structures).toEqual(["RootTask", "Task"]);
    expect(wire.active_statuses).toEqual(["In Progress", "active"]);
  });

  it("throws on an empty admission value in the save draft", () => {
    const base = {
      expectedRevision: 0,
      nativeTaskAuto: { activeEnabled: true, dueEnabled: true, deadlineEnabled: true, deadlineHorizonDays: 2 },
      excluded: [],
      activeStructures: [],
      assignedStructures: {},
    };
    expect(() =>
      capacitiesSettingsToWire({ ...base, nativeTaskStructures: [""], activeStatuses: [] }),
    ).toThrow(/nativeTaskStructures/);
    expect(() =>
      capacitiesSettingsToWire({ ...base, nativeTaskStructures: [], activeStatuses: [""] }),
    ).toThrow(/activeStatuses/);
  });
});

describe("capacities assigned_structures declarations (additive schema v1)", () => {
  const baseSettings = () => ({
    version: 1,
    revision: 0,
    native_task_auto: {
      active_enabled: true,
      due_enabled: true,
      deadline_enabled: true,
      deadline_horizon_days: 2,
    },
    excluded: {},
  });
  const baseDraft = () => ({
    expectedRevision: 0,
    nativeTaskAuto: {
      activeEnabled: true,
      dueEnabled: true,
      deadlineEnabled: true,
      deadlineHorizonDays: 2,
    },
    excluded: [],
    activeStructures: [],
    nativeTaskStructures: [],
    activeStatuses: [],
    assignedStructures: {},
  });

  it("projects an absent assigned_structures key to the empty map (older backend)", () => {
    const projected = projectCapacitiesSettings({ persisted: false, settings: baseSettings() });
    expect(projected.assignedStructures).toEqual({});
  });

  it("parses a well-formed map and keeps property ids exactly as declared", () => {
    const projected = projectCapacitiesSettings({
      persisted: false,
      settings: {
        ...baseSettings(),
        assigned_structures: {
          "custom-project": "Assigned-ID_CaseSensitive",
          b: " padded-is-opaque ",
        },
      },
    });
    expect(projected.assignedStructures).toEqual({
      "custom-project": "Assigned-ID_CaseSensitive",
      b: " padded-is-opaque ",
    });
  });

  it("throws when assigned_structures is not an object", () => {
    for (const bad of [[], "custom-project", null, 1]) {
      expect(() =>
        projectCapacitiesSettings({
          persisted: false,
          settings: { ...baseSettings(), assigned_structures: bad },
        }),
      ).toThrow(/assigned_structures/);
    }
  });

  it("throws on an invalid structure id or an empty property id", () => {
    for (const bad of [
      { "": "p1" },
      { " custom-project ": "p1" },
      { "custom project": "p1" },
      { "custom-project": "" },
      { "custom-project": 1 },
      { "custom-project": null },
    ]) {
      expect(() =>
        projectCapacitiesSettings({
          persisted: false,
          settings: { ...baseSettings(), assigned_structures: bad },
        }),
      ).toThrow(/assigned_structures/);
    }
  });

  it("always emits assigned_structures in the save body, even when empty", () => {
    const wire = capacitiesSettingsToWire(baseDraft());
    // Full replacement: an omitted key would wipe every saved declaration.
    expect("assigned_structures" in wire).toBe(true);
    expect(wire.assigned_structures).toEqual({});
  });

  it("emits a sorted declaration map, keeping property ids exactly", () => {
    const wire = capacitiesSettingsToWire({
      ...baseDraft(),
      assignedStructures: { zeta: "p2", alpha: " p1 " },
    });
    expect(Object.keys(wire.assigned_structures)).toEqual(["alpha", "zeta"]);
    expect(wire.assigned_structures).toEqual({ alpha: " p1 ", zeta: "p2" });
  });

  it("throws on an invalid declaration in the save draft", () => {
    expect(() =>
      capacitiesSettingsToWire({ ...baseDraft(), assignedStructures: { "bad id": "p1" } }),
    ).toThrow(/assignedStructures/);
    expect(() =>
      capacitiesSettingsToWire({ ...baseDraft(), assignedStructures: { "custom-project": "" } }),
    ).toThrow(/assignedStructures/);
  });

  it("round-trips a declaration through the save body and the GET projection", () => {
    const wire = capacitiesSettingsToWire({
      ...baseDraft(),
      assignedStructures: { "custom-project": "assigned-prop" },
    });
    // The stored settings body (minus the revision the route owns) is what
    // GET returns under `settings`, with `persisted` alongside it.
    const projected = projectCapacitiesSettings({
      persisted: true,
      available_structures: ["custom-project"],
      settings: {
        version: 1,
        revision: 1,
        native_task_auto: wire.native_task_auto,
        excluded: wire.excluded,
        active_structures: wire.active_structures,
        native_task_structures: wire.native_task_structures,
        active_statuses: wire.active_statuses,
        assigned_structures: wire.assigned_structures,
      },
    });
    expect(projected.assignedStructures).toEqual({ "custom-project": "assigned-prop" });
  });
});

describe("capacities assignment-declaration diagnostics (source_warnings)", () => {
  const declarationWarning =
    "ignored Capacities assignment declaration for structure 'custom-project': " +
    "unknown property 'assigned-prop'";

  it("matches the backend wording and keeps the text verbatim", () => {
    expect(capacitiesAssignmentWarnings([declarationWarning])).toEqual([declarationWarning]);
  });

  it("tolerates a double-quoted Python repr of the ids", () => {
    const quoted =
      'ignored Capacities assignment declaration for structure "it\'s": ' +
      'unknown property "prop\'s"';
    expect(capacitiesAssignmentWarnings([quoted])).toEqual([quoted]);
  });

  it("ignores the partial-coverage warning and unrelated warnings", () => {
    const coverage =
      "Capacities partial — 20 evaluated · 51 deferred across contributing structures.";
    expect(
      capacitiesAssignmentWarnings([coverage, "Calendar read failed (timeout)", ""]),
    ).toEqual([]);
  });

  it("is empty when no warnings are present", () => {
    expect(capacitiesAssignmentWarnings([])).toEqual([]);
  });
});

describe("tag exclusion settings", () => {
  const TAG_A = "5a25370b-f9a0-40cf-bc3a-0cab4744913c";
  const TAG_B = "0d194525-c5a1-4af5-bb62-202b83006b5e";

  function settingsWire(overrides: Record<string, any> = {}) {
    return {
      persisted: true,
      settings: {
        version: 1,
        revision: 2,
        exclusions: {
          tags: [{ source: "capacities", space_id: "space-1", tag_id: TAG_A }],
        },
      },
      tag_catalog: {
        status: "complete",
        space_id: "space-1",
        tags: [{ id: TAG_A, title: "habituals" }],
        warnings: [],
      },
      ...overrides,
    };
  }

  it("projects stable identities and the advisory catalog", () => {
    const projected = projectTagExclusionSettings(settingsWire());
    expect(projected).toEqual({
      version: 1,
      revision: 2,
      persisted: true,
      tags: [{ source: "capacities", spaceId: "space-1", tagId: TAG_A }],
      catalog: {
        status: "complete",
        spaceId: "space-1",
        tags: [{ id: TAG_A, title: "habituals" }],
        warnings: [],
      },
    });
  });

  it("rejects unknown dimensions, malformed entries, and duplicates", () => {
    const withExclusions = (exclusions: any) =>
      settingsWire({ settings: { version: 1, revision: 0, exclusions } });
    expect(() =>
      projectTagExclusionSettings(withExclusions({ tags: [], labels: [] })),
    ).toThrow(/dimension/);
    expect(() =>
      projectTagExclusionSettings(withExclusions({ tags: "habituals" })),
    ).toThrow(/tags/);
    expect(() =>
      projectTagExclusionSettings(
        withExclusions({ tags: [{ source: "todoist", space_id: "s", tag_id: TAG_A }] }),
      ),
    ).toThrow(/source/);
    expect(() =>
      projectTagExclusionSettings(
        withExclusions({ tags: [{ source: "capacities", space_id: " s", tag_id: TAG_A }] }),
      ),
    ).toThrow(/space_id/);
    expect(() =>
      projectTagExclusionSettings(
        withExclusions({ tags: [{ source: "capacities", space_id: "s", tag_id: TAG_A.toUpperCase() }] }),
      ),
    ).toThrow(/UUID/);
    expect(() =>
      projectTagExclusionSettings(
        withExclusions({ tags: [
          { source: "capacities", space_id: "s", tag_id: TAG_A },
          { source: "capacities", space_id: "s", tag_id: TAG_A },
        ] }),
      ),
    ).toThrow(/duplicate/);
    expect(() =>
      projectTagExclusionSettings(
        withExclusions({ tags: [{ source: "capacities", space_id: "s", tag_id: TAG_A, title: "x" }] }),
      ),
    ).toThrow(/keys/);
    expect(() => projectTagExclusionSettings({ persisted: "yes", settings: {} })).toThrow(
      /persisted/,
    );
  });

  it("keeps a save response (no catalog) on the supplied inventory", () => {
    const loaded = projectTagExclusionSettings(settingsWire());
    const saved = projectTagExclusionSettings(
      { persisted: true, settings: { version: 1, revision: 3, exclusions: { tags: [] } } },
      loaded.catalog,
    );
    expect(saved.catalog).toEqual(loaded.catalog);

    // Without a supplied catalog the projection uses the empty advisory
    // catalog rather than inventing tags.
    const bare = projectTagExclusionSettings({
      persisted: true,
      settings: { version: 1, revision: 3, exclusions: { tags: [] } },
    });
    expect(bare.catalog).toEqual(emptyTagCatalog());
  });

  it("rejects a malformed catalog instead of rendering it", () => {
    expect(() =>
      projectTagExclusionSettings(
        settingsWire({ tag_catalog: { status: "weird", space_id: null, tags: [], warnings: [] } }),
      ),
    ).toThrow(/status/);
    expect(() =>
      projectTagExclusionSettings(
        settingsWire({ tag_catalog: { status: "complete", space_id: null, tags: [{ id: "x" }], warnings: [] } }),
      ),
    ).toThrow(/id and title/);
  });

  it("builds the strict full-replacement body", () => {
    expect(
      tagExclusionSettingsToWire({
        expectedRevision: 2,
        tags: [
          { source: "capacities", spaceId: "space-1", tagId: TAG_B },
          { source: "capacities", spaceId: "space-1", tagId: TAG_A },
        ],
      }),
    ).toEqual({
      expected_revision: 2,
      exclusions: {
        tags: [
          { source: "capacities", space_id: "space-1", tag_id: TAG_B },
          { source: "capacities", space_id: "space-1", tag_id: TAG_A },
        ],
      },
    });
    expect(() => tagExclusionSettingsToWire({ expectedRevision: -1, tags: [] })).toThrow(
      /expectedRevision/,
    );
    expect(() =>
      tagExclusionSettingsToWire({
        expectedRevision: 0,
        tags: [{ source: "capacities", spaceId: "space-1", tagId: "nope" }],
      }),
    ).toThrow(/UUID/);
    expect(() =>
      tagExclusionSettingsToWire({
        expectedRevision: 0,
        tags: [
          { source: "capacities", spaceId: "space-1", tagId: TAG_A },
          { source: "capacities", spaceId: "space-1", tagId: TAG_A },
        ],
      }),
    ).toThrow(/duplicate/);
  });

  it("recognizes canonical tag ids", () => {
    expect(isCanonicalTagId(TAG_A)).toBe(true);
    expect(isCanonicalTagId(TAG_A.toUpperCase())).toBe(false);
    expect(isCanonicalTagId(`{${TAG_A}}`)).toBe(false);
    expect(isCanonicalTagId(TAG_A.replace(/-/g, ""))).toBe(false);
    expect(isCanonicalTagId("habituals")).toBe(false);
    expect(isCanonicalTagId(null)).toBe(false);
  });
});

describe("Capacities source mapping (wire ↔ model)", () => {
  const sourceWire = () => ({
    version: 1,
    revision: 4,
    space_id: "space-1",
    structures: [
      {
        structure_id: "project",
        title_property: "title-prop",
        status_property: "status-prop",
        open_status_values: ["In Progress", "On Hold"],
        date_property: "date-prop",
        deadline_property: "deadline-prop",
        duration_property: "duration-prop",
        assignment_property: "assignment-prop",
        assignment_values: ["Adam", "Meegy"],
        completion_property: "completion-prop",
        completion_value: "Done",
      },
      {
        structure_id: "task",
        title_property: "title",
        status_property: null,
        open_status_values: [],
        date_property: null,
        deadline_property: null,
        duration_property: null,
        assignment_property: null,
        assignment_values: [],
        completion_property: null,
        completion_value: null,
      },
    ],
  });

  it("projects an absent record without throwing", () => {
    expect(projectCapacitiesSource({ source: null, persisted: false })).toEqual({
      source: null,
      persisted: false,
    });
  });

  it("projects every row field, including nullable ids and both value lists", () => {
    const projected = projectCapacitiesSource({ source: sourceWire(), persisted: true });
    expect(projected.persisted).toBe(true);
    expect(projected.source).toEqual({
      version: 1,
      revision: 4,
      spaceId: "space-1",
      structures: [
        {
          structureId: "project",
          titleProperty: "title-prop",
          statusProperty: "status-prop",
          openStatusValues: ["In Progress", "On Hold"],
          dateProperty: "date-prop",
          deadlineProperty: "deadline-prop",
          durationProperty: "duration-prop",
          assignmentProperty: "assignment-prop",
          assignmentValues: ["Adam", "Meegy"],
          completionProperty: "completion-prop",
          completionValue: "Done",
        },
        {
          structureId: "task",
          titleProperty: "title",
          statusProperty: null,
          openStatusValues: [],
          dateProperty: null,
          deadlineProperty: null,
          durationProperty: null,
          assignmentProperty: null,
          assignmentValues: [],
          completionProperty: null,
          completionValue: null,
        },
      ],
    });
  });

  it("round-trips model → wire → model faithfully", () => {
    const structure: CapacitiesSourceStructure = {
      structureId: "project",
      titleProperty: "title-prop",
      statusProperty: "status-prop",
      // Deliberately unsorted: order-sensitive mapping data is never reordered.
      openStatusValues: ["On Hold", "In Progress"],
      dateProperty: "date-prop",
      deadlineProperty: null,
      durationProperty: "duration-prop",
      assignmentProperty: "assignment-prop",
      assignmentValues: ["Meegy", "Adam"],
      completionProperty: null,
      completionValue: "Done",
    };
    const model: CapacitiesSource = {
      version: 1,
      revision: 7,
      spaceId: "space-1",
      structures: [structure],
    };
    const wire = capacitiesSourceToWire({
      expectedRevision: model.revision,
      spaceId: model.spaceId,
      structures: model.structures,
    });
    expect(wire).toEqual({
      expected_revision: 7,
      space_id: "space-1",
      structures: [
        {
          structure_id: "project",
          title_property: "title-prop",
          status_property: "status-prop",
          open_status_values: ["On Hold", "In Progress"],
          date_property: "date-prop",
          deadline_property: null,
          duration_property: "duration-prop",
          assignment_property: "assignment-prop",
          assignment_values: ["Meegy", "Adam"],
          completion_property: null,
          completion_value: "Done",
        },
      ],
    });
    // The save response carries the stored body (server-owned version and
    // revision) under `source`; the projection must land on the exact model.
    const projected = projectCapacitiesSource({
      persisted: true,
      source: {
        version: model.version,
        revision: model.revision,
        space_id: wire.space_id,
        structures: wire.structures,
      },
    });
    expect(projected.source).toEqual(model);
  });

  it("fails closed on a malformed record or draft", () => {
    expect(() =>
      projectCapacitiesSource({ source: { ...sourceWire(), version: 9 }, persisted: true }),
    ).toThrow(/unsupported/);
    expect(() =>
      projectCapacitiesSource({
        persisted: true,
        source: {
          ...sourceWire(),
          structures: [{ ...sourceWire().structures[0], title_property: "" }],
        },
      }),
    ).toThrow(/title_property/);
    expect(() =>
      projectCapacitiesSource({
        persisted: true,
        source: {
          ...sourceWire(),
          structures: [{ ...sourceWire().structures[0], open_status_values: "active" }],
        },
      }),
    ).toThrow(/open_status_values/);
    expect(() =>
      capacitiesSourceToWire({ expectedRevision: -1, spaceId: "space-1", structures: [] }),
    ).toThrow(/expectedRevision/);
    expect(() =>
      capacitiesSourceToWire({
        expectedRevision: 0,
        spaceId: "space-1",
        structures: [{ ...structureWithBadValue() }],
      }),
    ).toThrow(/openStatusValues/);
  });

  function structureWithBadValue(): CapacitiesSourceStructure {
    return {
      structureId: "project",
      titleProperty: "title-prop",
      statusProperty: null,
      openStatusValues: [""],
      dateProperty: null,
      deadlineProperty: null,
      durationProperty: null,
      assignmentProperty: null,
      assignmentValues: [],
      completionProperty: null,
      completionValue: null,
    };
  }
});
