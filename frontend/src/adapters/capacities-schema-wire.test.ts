/* capacities-schema-wire.test.ts — the additive `schema_change` key on
   GET /capacities/refresh/status, and the ack POST body.

   The notice is advisory and content-free: it carries one generated sentence,
   the hashes, and per-kind counts. Everything here is defensive — a null,
   absent, malformed, or unknown-severity block must project as "no notice"
   rather than throwing, because a broken sidecar may never turn a status read
   into a UI failure. */

import { describe, expect, it } from "vitest";
import {
  capacitiesSchemaChangeAckToWire,
  projectCapacitiesRefreshStatus,
} from "./wire";

const SUMMARY =
  "Capacities changed since the last refresh: 1 status option removed. Review the mapping in Connections.";

function noticeWire(patch: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    severity: "warn",
    summary: SUMMARY,
    detected_at: "2026-10-29T10:00:00+00:00",
    generation: 4,
    from_hash: "from-hash",
    to_hash: "to-hash",
    acknowledged: false,
    acknowledged_at: null,
    diff: { counts: { options_removed: 1, properties_added: 0 } },
    ...patch,
  };
}

describe("projectCapacitiesRefreshStatus schema_change", () => {
  it("projects a warn notice verbatim", () => {
    const status = projectCapacitiesRefreshStatus({
      configured: true,
      schema_change: noticeWire(),
    });

    expect(status.schemaChange).toEqual({
      severity: "warn",
      summary: SUMMARY,
      detectedAt: "2026-10-29T10:00:00+00:00",
      generation: 4,
      fromHash: "from-hash",
      toHash: "to-hash",
      acknowledged: false,
      acknowledgedAt: null,
      counts: { options_removed: 1, properties_added: 0 },
    });
  });

  it("projects an info notice", () => {
    const status = projectCapacitiesRefreshStatus({
      configured: true,
      schema_change: noticeWire({ severity: "info", acknowledged: true, acknowledged_at: "2026-10-29T11:00:00+00:00" }),
    });

    expect(status.schemaChange?.severity).toBe("info");
    // Acknowledged notices still project truthfully; hiding them is the
    // panel's decision, not the wire's.
    expect(status.schemaChange?.acknowledged).toBe(true);
    expect(status.schemaChange?.acknowledgedAt).toBe("2026-10-29T11:00:00+00:00");
  });

  it("projects no notice for null, absent, or non-object blocks", () => {
    expect(projectCapacitiesRefreshStatus({ configured: true, schema_change: null }).schemaChange).toBeNull();
    expect(projectCapacitiesRefreshStatus({ configured: true }).schemaChange).toBeNull();
    expect(projectCapacitiesRefreshStatus({ configured: true, schema_change: "warn" }).schemaChange).toBeNull();
    expect(projectCapacitiesRefreshStatus({ configured: true, schema_change: [] }).schemaChange).toBeNull();
    expect(projectCapacitiesRefreshStatus({ configured: true, schema_change: 7 }).schemaChange).toBeNull();
  });

  it("projects no notice for an unknown severity or a missing summary", () => {
    expect(
      projectCapacitiesRefreshStatus({ schema_change: noticeWire({ severity: "error" }) }).schemaChange,
    ).toBeNull();
    expect(
      projectCapacitiesRefreshStatus({ schema_change: noticeWire({ severity: 1 }) }).schemaChange,
    ).toBeNull();
    expect(
      projectCapacitiesRefreshStatus({ schema_change: noticeWire({ summary: "" }) }).schemaChange,
    ).toBeNull();
    expect(
      projectCapacitiesRefreshStatus({ schema_change: noticeWire({ summary: { text: SUMMARY } }) }).schemaChange,
    ).toBeNull();
  });

  it("projects no notice when the hash an acknowledgement must name is unusable", () => {
    expect(
      projectCapacitiesRefreshStatus({ schema_change: noticeWire({ to_hash: "" }) }).schemaChange,
    ).toBeNull();
    expect(
      projectCapacitiesRefreshStatus({ schema_change: noticeWire({ to_hash: null }) }).schemaChange,
    ).toBeNull();
  });

  it("never throws on wrong types and defaults the optional fields", () => {
    const status = projectCapacitiesRefreshStatus({
      schema_change: noticeWire({
        detected_at: 12,
        generation: "4",
        from_hash: 3,
        acknowledged: "yes",
        acknowledged_at: 5,
        diff: "counts",
      }),
    });

    expect(status.schemaChange).toEqual({
      severity: "warn",
      summary: SUMMARY,
      detectedAt: null,
      generation: 0,
      fromHash: null,
      toHash: "to-hash",
      acknowledged: false,
      acknowledgedAt: null,
      counts: {},
    });
  });

  it("keeps only numeric diff counts and ignores everything else in the block", () => {
    const status = projectCapacitiesRefreshStatus({
      schema_change: noticeWire({
        diff: {
          counts: { options_removed: 2, properties_renamed: "3", structures_added: -1 },
          options_removed: [["space", "status", "blocked"]],
          namespace: "machine-local",
        },
        scope_key: "machine-local",
        version: 1,
      }),
    });

    expect(status.schemaChange?.counts).toEqual({ options_removed: 2 });
  });

  it("leaves every existing status field unchanged", () => {
    const status = projectCapacitiesRefreshStatus({
      configured: true,
      phase: "complete",
      outcome: "published",
      mode: "refresh",
      scope: "all",
      job: {
        job_id: "job-1",
        mode: "refresh",
        scope: "all",
        phase: "complete",
        outcome: "published",
        started_at: 10,
        finished_at: 20,
        progress: { listed: 3, read: 2 },
        warnings: ["partial"],
      },
      progress: { listed: 3, read: 2, to_read: 3, types: { Task: 2 } },
      warnings: ["partial"],
      schema_change: noticeWire(),
      snapshot: { present: true, generation: 4, revision: "rev", installed_at: 30, member_count: 3 },
    });

    expect(status.configured).toBe(true);
    expect(status.phase).toBe("complete");
    expect(status.outcome).toBe("published");
    expect(status.mode).toBe("refresh");
    expect(status.scope).toBe("all");
    expect(status.job?.jobId).toBe("job-1");
    expect(status.progress).toEqual({
      listed: 3,
      read: 2,
      toRead: 3,
      waiting: null,
      types: { Task: 2 },
    });
    expect(status.warnings).toEqual(["partial"]);
    expect(status.snapshot).toMatchObject({ present: true, generation: 4, memberCount: 3 });
    expect(status.schemaChange?.toHash).toBe("to-hash");
  });

  it("projects no notice on an unconfigured source", () => {
    const status = projectCapacitiesRefreshStatus({
      configured: false,
      schema_change: noticeWire(),
    });

    expect(status.configured).toBe(false);
    expect(status.schemaChange?.severity).toBe("warn");
  });
});

describe("capacitiesSchemaChangeAckToWire", () => {
  it("names exactly the hash being acknowledged", () => {
    expect(capacitiesSchemaChangeAckToWire("to-hash")).toEqual({ to_hash: "to-hash" });
  });
});
