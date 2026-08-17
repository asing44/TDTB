/* api.ts — production API adapter (T5). Talks to the FastAPI endpoints on the
   same origin; all wire↔model mapping delegates to wire.ts (pinned by the
   contract-fixture tests). Holds the RAW /plan-inputs payload internally so
   POST bodies (/sequence, /validate-sequence, /commit) are built from
   server-verbatim digest/config/anchored_blocks — components only ever see
   the projected model.

   Safety invariants owned here:
   - No billed endpoint is ever called implicitly; autoSequence is the one
     billed POST and only the controller's explicit user action reaches it.
   - readFixedInputs re-GETs /plan-inputs and THROWS on a calendar-source
     degrade — a failed fixed-input read blocks preview/commit, it is never
     an "unchanged" fingerprint (locked decision 17).
   - liveCommit hits /commit?mode=live verbatim; 409 (single-flight), 422,
     and 429 surface as typed ApiError, never silently retried. */

import type {
  Adapter,
  DurationMemoryResetResult,
  DurationMemorySaveResult,
  SequenceContext,
  SequenceResult,
  SourceRefreshResult,
} from "./adapter";
import type {
  Capacity,
  CommitReport,
  DaySetup,
  FixedInputs,
  Ledger,
  MicroIdea,
  PlanInputs,
  RuntimeAction,
  SequenceRow,
  ShadowDiff,
  Validation,
} from "../model/types";
import {
  calendarWarnings,
  daySetupToWire,
  projectCapacity,
  projectCommitReport,
  projectDurationMemoryReset,
  projectDurationMemorySave,
  projectFixedInputs,
  projectLedger,
  projectPlanInputs,
  projectRuntimeAction,
  projectSequenceResult,
  projectShadow,
  projectValidation,
  rowToWire,
  grantToWire,
  shapeAssignedWire,
  type Wire,
} from "./wire";

/** HTTP failure with the server's error detail preserved (409/422/429/...). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function detailMessage(status: number, detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in (detail as Wire)) {
    const base = String((detail as Wire).message);
    // T12 qualification (2026-07-26): the server returns actionable
    // `hard_errors` alongside the message — "foreign pinned row 'LOOTS'" —
    // and only the bare message reached the UI. The run dead-ended on
    // "pinned-row validation failed" with no stated cause, and the reason was
    // recoverable only by reading the raw response off the network tab.
    const hard = (detail as Wire).hard_errors;
    if (Array.isArray(hard) && hard.length > 0) {
      return `${base}: ${hard.join("; ")}`;
    }
    return base;
  }
  return `request failed (${status})`;
}

export class ApiAdapter implements Adapter {
  private token: string | null = null;
  /** Server-verbatim /plan-inputs payload — POST-body source of truth. */
  private raw: Wire | null = null;

  constructor(private base: string = "") {}

  private async request(path: string, init?: RequestInit): Promise<Wire> {
    const res = await fetch(this.base + path, init);
    let body: Wire | string | null = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    if (!res.ok) {
      const detail = (body as Wire)?.detail ?? body;
      throw new ApiError(res.status, detail, detailMessage(res.status, detail));
    }
    return body as Wire;
  }

  private async authHeaders(): Promise<Record<string, string>> {
    if (this.token === null) {
      const r = await this.request("/session-token");
      this.token = String(r.token);
    }
    return { "X-TDTB-Token": this.token, "Content-Type": "application/json" };
  }

  private async post(path: string, body: Wire): Promise<Wire> {
    const send = async () =>
      this.request(path, {
        method: "POST",
        headers: await this.authHeaders(),
        body: JSON.stringify(body),
      });
    try {
      return await send();
    } catch (e) {
      // T12 qualification (2026-07-26): the server's token lives in
      // app.state.token for the life of the PROCESS, so any restart rotates
      // it and an open page then 403s on every write — including the billed
      // Send — with no "session expired, reload" affordance anywhere. The
      // token is a session handle the client fetches for itself, not a
      // credential the user owns, so re-fetching and retrying once is the
      // honest recovery. Safe for the billed path specifically because a 403
      // is rejected at the dependency, before _require_billed_budget: the
      // request never executed and nothing was charged.
      if (e instanceof ApiError && e.status === 403) {
        this.token = null;
        return await send();
      }
      throw e;
    }
  }

  private rawOrThrow(): Wire {
    if (!this.raw) throw new Error("plan inputs not loaded yet");
    return this.raw;
  }

  async loadPlanInputs(): Promise<PlanInputs> {
    const wire = await this.request("/plan-inputs");
    this.raw = wire;
    return projectPlanInputs(wire);
  }

  async billedLedger(): Promise<Ledger> {
    return projectLedger(await this.request("/billed-ledger"));
  }

  /** Explicit source refresh (locked decision 20): exactly one GET
      /plan-inputs + one GET /billed-ledger — never /gather, never a billed
      or write endpoint. A degraded calendar read throws so a bad snapshot
      can neither pass as an "unchanged" fingerprint nor replace the last
      good view. Both projections come from the SAME wire payload. */
  async refreshSources(): Promise<SourceRefreshResult> {
    const wire = await this.request("/plan-inputs");
    const calWarnings = calendarWarnings((wire.source_warnings ?? []).map(String));
    if (calWarnings.length > 0) {
      throw new Error(`source refresh degraded — ${calWarnings[0]}`);
    }
    // A fresh read is also the latest raw payload for subsequent POST bodies.
    this.raw = wire;
    return {
      inputs: projectPlanInputs(wire),
      fixed: projectFixedInputs(wire),
      ledger: await this.billedLedger(),
    };
  }

  async capacityPreview(daySetup: DaySetup, selectedBlocks: number[]): Promise<Capacity> {
    const params = new URLSearchParams({
      day_setup: JSON.stringify(daySetupToWire(daySetup)),
      // Server parses durations; send explicit minutes so 0 stays 0.
      selected: JSON.stringify(selectedBlocks.map((b) => Math.round(b * 30))),
    });
    return projectCapacity(await this.request(`/capacity-preview?${params}`));
  }

  async saveDaySetup(daySetup: DaySetup): Promise<void> {
    await this.post("/day-setup", daySetupToWire(daySetup));
  }

  async saveMicroAdventure(pick: MicroIdea | null): Promise<void> {
    await this.post("/day-setup", {
      micro_adventure:
        pick === null
          ? null
          : { id: pick.id, idea: pick.idea, category: pick.category },
    });
  }

  /** Explicit durable save (duration-memory MVP): exactly ONE token-guarded
      non-billed POST. Strict client validation happens before this method is
      ever reached; the wire body carries the exact value untouched. */
  async saveDurationMemory(identity: string, minutes: number): Promise<DurationMemorySaveResult> {
    return projectDurationMemorySave(
      await this.post("/duration-memory/save", { identity, minutes }),
    );
  }

  /** Explicit durable reset (duration-memory MVP): exactly ONE token-guarded
      non-billed POST; the response carries the current source fallback. */
  async resetDurationMemory(identity: string): Promise<DurationMemoryResetResult> {
    return projectDurationMemoryReset(
      await this.post("/duration-memory/reset", { identity }),
    );
  }

  async autoSequence(ctx: SequenceContext): Promise<SequenceResult> {
    const raw = this.rawOrThrow();
    const body = {
      assigned: shapeAssignedWire(raw.digest?.assigned ?? [], ctx.included),
      config: raw.config ?? {},
      anchored_blocks: raw.anchored_blocks ?? [],
      day_semantics: raw.day_semantics ?? {},
      planning_config_fingerprint: ctx.planningConfigFingerprint ?? raw.planning_config_fingerprint ?? "",
      pinned_rows: (ctx.pinnedRows ?? []).map(rowToWire),
    };
    return projectSequenceResult(await this.post("/sequence", body));
  }

  async validateSequence(rows: SequenceRow[], ctx: SequenceContext): Promise<Validation> {
    const raw = this.rawOrThrow();
    const body = {
      sequence: rows.map(rowToWire),
      assigned: shapeAssignedWire(raw.digest?.assigned ?? [], ctx.included),
      config: raw.config ?? {},
      anchored_blocks: raw.anchored_blocks ?? [],
      overlap_grants: (ctx.overlapGrants ?? []).map(grantToWire),
      planning_config_fingerprint: ctx.planningConfigFingerprint ?? raw.planning_config_fingerprint ?? "",
      pinned_rows: (ctx.pinnedRows ?? []).map(rowToWire),
    };
    return projectValidation(await this.post("/validate-sequence", body));
  }

  async readFixedInputs(): Promise<FixedInputs> {
    const wire = await this.request("/plan-inputs");
    const calWarnings = calendarWarnings((wire.source_warnings ?? []).map(String));
    if (calWarnings.length > 0) {
      throw new Error(`fixed-input read degraded — ${calWarnings[0]}`);
    }
    // A fresh read is also the latest raw payload for subsequent POST bodies.
    this.raw = wire;
    return projectFixedInputs(wire);
  }

  /** Commit digest carries today-only shaping (T6): excluded rows drop —
      no Step C set-flag manifest rows for items excluded today — and
      duration overrides replace `blocks`. Suggested/valid_date pass through
      untouched for backend compatibility. */
  private commitBody(rows: SequenceRow[], ctx: SequenceContext): Wire {
    const raw = this.rawOrThrow();
    return {
      digest: {
        ...(raw.digest ?? {}),
        assigned: shapeAssignedWire(raw.digest?.assigned ?? [], ctx.included),
      },
      sequence: { sequence: rows.map(rowToWire) },
      config: raw.config ?? {},
      overlap_grants: (ctx.overlapGrants ?? []).map(grantToWire),
      pinned_rows: (ctx.pinnedRows ?? []).map(rowToWire),
      planning_config_fingerprint: ctx.planningConfigFingerprint ?? raw.planning_config_fingerprint ?? "",
    };
  }

  async shadowCommit(rows: SequenceRow[], ctx: SequenceContext): Promise<ShadowDiff> {
    return projectShadow(
      await this.post("/commit?mode=shadow", this.commitBody(rows, ctx)),
    );
  }

  async liveCommit(rows: SequenceRow[], ctx: SequenceContext): Promise<CommitReport> {
    return projectCommitReport(
      await this.post("/commit?mode=live", this.commitBody(rows, ctx)),
    );
  }

  async runtimeAction(
    verb: string, target: string, args: Record<string, unknown> = {},
  ): Promise<RuntimeAction> {
    return projectRuntimeAction(
      await this.post("/runtime-actions", { verb, target, args }),
    );
  }

  async undoRuntimeAction(actionId: string): Promise<RuntimeAction> {
    return projectRuntimeAction(
      await this.post(`/runtime-actions/${encodeURIComponent(actionId)}/undo`, {}),
    );
  }
}
