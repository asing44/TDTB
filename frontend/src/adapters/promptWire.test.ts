/* U5 prompt wire projection: a landed main commit with unclean prompt exports
   is a partial commit, the __main_failure__ sentinel is never a prompt name,
   and the fixture mirrors the real save-echo shape. */

import { describe, expect, it } from "vitest";
import {
  projectCommitReport,
  projectDaySetupSaveResult,
  projectPlanInputs,
  promptDraftsToWire,
  promptOptinsToWire,
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

  it("the day-setup save echo projects optins, revision, and content-free warnings", () => {
    const result = projectDaySetupSaveResult({
      ok: true,
      day_setup_confirmed: false,
      optins: { intention: false, megan_nicety: true, stoic_intention: false },
      optins_revision: 4,
      prompt_warnings: ["Prompt drafts could not be read; showing empty prompts."],
    });
    expect(result.daySetupConfirmed).toBe(false);
    expect(result.optinsAvailable).toBe(true);
    expect(result.optins.megan_nicety).toBe(true);
    expect(result.optinsRevision).toBe(4);
    expect(result.promptWarnings).toHaveLength(1);
  });

  it("an absent or malformed opt-in echo is unavailable, never all-false", () => {
    const absent = projectDaySetupSaveResult({
      ok: true,
      day_setup_confirmed: false,
      prompt_warnings: [
        "Prompt opt-ins could not be read; preferences are shown as unavailable.",
      ],
    });
    expect(absent.optinsAvailable).toBe(false);
    expect(absent.optins).toEqual({});
    expect(absent.optinsRevision).toBe(0);
    expect(absent.promptWarnings).toHaveLength(1);

    // Present but malformed (missing revision / non-object optins) stays
    // fail-closed rather than being treated as a readable all-false store.
    const noRevision = projectDaySetupSaveResult({
      ok: true,
      optins: { intention: true },
    });
    expect(noRevision.optinsAvailable).toBe(false);
    expect(noRevision.optins).toEqual({});

    const badOptins = projectDaySetupSaveResult({
      ok: true,
      optins: ["intention"],
      optins_revision: 2,
    });
    expect(badOptins.optinsAvailable).toBe(false);
  });

  it("builders send only prompt keys (prompt-only / opt-in-only bodies)", () => {
    expect(promptDraftsToWire({ intention: "x" })).toEqual({ captures: { intention: "x" } });
    expect(promptOptinsToWire({ megan_nicety: true }, 2)).toEqual({
      optins: { megan_nicety: true },
      optins_revision: 2,
    });
  });

  it("plan-inputs opt-in metadata projects to the same shape the fixture emits", async () => {
    const projected = projectPlanInputs({
      digest: {},
      prompt_optins: { optins: { megan_nicety: true }, revision: 4 },
    }).promptOptins;
    const adapter = new FixtureAdapter("fresh");
    const echo = await adapter.savePromptOptins(
      { intention: false, megan_nicety: true, stoic_intention: false },
      0,
    );
    const fixtureWire = projectDaySetupSaveResult({
      ok: true,
      day_setup_confirmed: false,
      optins: echo.optins,
      optins_revision: echo.optinsRevision,
      prompt_warnings: [],
    });
    // Fixture echo and real-wire projection are interchangeable.
    expect(fixtureWire).toEqual(echo);
    expect(projected).toEqual({ optins: { megan_nicety: true }, revision: 4 });
  });
});
