/* U5 prompt wire projection (Planning 2a): a landed main commit with unclean
   prompt exports is a partial commit, the __main_failure__ sentinel is never a
   prompt name, the captures-only builder carries browser-defined custom keys,
   and the legacy opt-in fields are tolerated but never projected. */

import { describe, expect, it } from "vitest";
import {
  projectCommitReport,
  projectDaySetupSaveResult,
  projectPlanInputs,
  promptDraftsToWire,
} from "./wire";
import { FixtureAdapter } from "./fixture";

describe("U5 prompt wire projection", () => {
  it("ok=true with prompt_exports_ok=false is a partial commit, never success", () => {
    const report = projectCommitReport({
      ok: true,
      surfaces: { todoist: { status: "ok" } },
      prompt_exports_ok: false,
      prompt_exports: [
        {
          prompt_key: "megan_nicety",
          action: "todoist_task",
          status: "needs_review",
          task_id: null,
          reason: "a prior attempt is unconfirmed — review before retrying",
        },
      ],
    });
    expect(report.status).toBe("partial");
    expect(report.promptExportsOk).toBe(false);
    expect(report.promptExports?.[0].status).toBe("needs_review");
    // No prompt text ever crosses.
    expect(JSON.stringify(report.promptExports)).not.toContain("content");
  });

  it("the __main_failure__ sentinel projects as skipped_main_failure", () => {
    const report = projectCommitReport({
      ok: false,
      surfaces: {},
      prompt_exports_ok: false,
      prompt_exports: [
        {
          prompt_key: "__main_failure__",
          action: "todoist_task",
          status: "skipped_main_failure",
          task_id: null,
          reason: "main commit did not land — exports skipped",
        },
      ],
    });
    expect(report.status).toBe("failed");
    expect(report.promptExports?.[0]).toEqual({
      promptKey: "__main_failure__",
      action: "todoist_task",
      status: "skipped_main_failure",
      taskId: null,
      reason: "main commit did not land — exports skipped",
    });
  });

  it("an unknown export status degrades to blocked", () => {
    const report = projectCommitReport({
      ok: true,
      surfaces: {},
      prompt_exports: [{ prompt_key: "stoic_intention", status: "surprise" }],
    });
    expect(report.promptExports?.[0].status).toBe("blocked");
  });

  it("a clean export lane keeps ok=true as ok", () => {
    const report = projectCommitReport({
      ok: true,
      surfaces: { todoist: { status: "ok" } },
      prompt_exports_ok: true,
      prompt_exports: [],
    });
    expect(report.status).toBe("ok");
  });

  it("the day-setup save echo keeps the confirmation flag and warnings only", () => {
    const result = projectDaySetupSaveResult({
      ok: true,
      day_setup_confirmed: false,
      prompt_warnings: ["Prompt drafts could not be read; showing empty prompts."],
    });
    expect(result).toEqual({
      ok: true,
      daySetupConfirmed: false,
      promptWarnings: ["Prompt drafts could not be read; showing empty prompts."],
    });
  });

  it("legacy optins fields in the save echo are tolerated but never projected", () => {
    const result = projectDaySetupSaveResult({
      ok: true,
      day_setup_confirmed: false,
      optins: { intention: false, megan_nicety: true, stoic_intention: false },
      optins_revision: 4,
      prompt_warnings: [],
    });
    expect(result).toEqual({ ok: true, daySetupConfirmed: false, promptWarnings: [] });
    expect(result).not.toHaveProperty("optins");
    expect(result).not.toHaveProperty("optinsAvailable");
  });

  it("the captures builder sends only captures, including custom prompt keys", () => {
    expect(promptDraftsToWire({ intention: "x" })).toEqual({ captures: { intention: "x" } });
    expect(promptDraftsToWire({ "custom-abc-1": "the rain" })).toEqual({
      captures: { "custom-abc-1": "the rain" },
    });
    // An empty answer clears on the server; the key is still sent.
    expect(promptDraftsToWire({ "custom-abc-1": "" })).toEqual({
      captures: { "custom-abc-1": "" },
    });
  });

  it("plan-inputs tolerates a legacy prompt_optins block without projecting it", () => {
    const inputs = projectPlanInputs({
      digest: {},
      prompt_optins: { optins: { megan_nicety: true }, revision: 4 },
    });
    expect(inputs).not.toHaveProperty("promptOptins");
  });

  it("the fixture accepts custom prompt keys in the captures filter", async () => {
    const adapter = new FixtureAdapter("fresh");
    const echo = await adapter.savePromptDrafts({ "custom-abc-1": "the rain" });
    expect(echo).toEqual({ ok: true, daySetupConfirmed: false, promptWarnings: [] });
  });

  it("the save echo carries the skip map under the captures wire keys", () => {
    const result = projectDaySetupSaveResult({
      ok: true,
      day_setup_confirmed: false,
      description_skips: { megan_nicety: true, stoic_intention: false },
      prompt_warnings: [],
    });
    expect(result.descriptionSkips).toEqual({ forMeegy: true, stoic: false });
    expect(result.daySetupConfirmed).toBe(false);
  });

  it("an echo without the skip map means no skips, not an all-false map", () => {
    const result = projectDaySetupSaveResult({
      ok: true,
      day_setup_confirmed: false,
      prompt_warnings: [],
    });
    expect(result).not.toHaveProperty("descriptionSkips");
  });

  it("the fixture replaces the whole skip map and omits the echo when nothing is skipped", async () => {
    const adapter = new FixtureAdapter("fresh");
    expect((await adapter.savePromptSkips({ forMeegy: true, stoic: true })).descriptionSkips)
      .toEqual({ forMeegy: true, stoic: true });
    // Full replace: an omitted-then-false key really turns off.
    expect((await adapter.savePromptSkips({ forMeegy: false, stoic: true })).descriptionSkips)
      .toEqual({ forMeegy: false, stoic: true });
    expect(await adapter.savePromptSkips({ forMeegy: false, stoic: false })).toEqual({
      ok: true,
      daySetupConfirmed: false,
      promptWarnings: [],
    });
  });
});
