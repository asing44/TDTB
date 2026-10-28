/* api.ts — production API adapter (T5). Talks to the FastAPI endpoints on the
   same origin; all wire↔model mapping delegates to wire.ts (pinned by the
   contract-fixture tests). Holds the RAW /plan-inputs payload internally so
   POST bodies (/sequence, /validate-sequence, /commit) are built from
   server-verbatim digest/config/anchored_blocks — components only ever see
   the projected model.

   Safety invariants owned here:
   - No billed endpoint is ever called implicitly; autoSequence is the one
     billed POST and only the controller's explicit user action reaches it.
   - readFixedInputs re-GETs /plan-inputs and, since 2026-10-19 (OQ-P1,
     reversing decision-17), keeps a calendar-only degradation as data: the
     calendar source shows its own advisory note and the fresh read stays
     authoritative, exactly as refreshSources does. Todoist is unchanged.
   - liveCommit hits /commit?mode=live verbatim; 409 (single-flight), 422,
     and 429 surface as typed ApiError, never silently retried. */

import type {
  Adapter,
  AnchoredStartSaveResult,
  DurationMemoryResetResult,
  DurationMemorySaveResult,
  RuntimeBatchItemInput,
  RuntimeBatchResult,
  RuntimeBatchUndoReport,
  SequenceContext,
  SequenceResult,
  SourceRefreshResult,
} from "./adapter";
import type {
  CalendarDrift,
  Capacity,
  CapacitiesCatalog,
  CapacitiesRefreshMode,
  CapacitiesRefreshStatus,
  CapacitiesRuleNode,
  CapacitiesRuleSaveResponse,
  CapacitiesRules,
  CapacitiesSelections,
  CapacitiesSelectionsDraft,
  CapacitiesSettings,
  CapacitiesSettingsDraft,
  CapacitiesSourceDraft,
  CapacitiesSourceRead,
  CommitReport,
  DaySetup,
  DaySetupSaveResult,
  DescriptionSkips,
  FixedInputs,
  Ledger,
  MicroIdea,
  PlanInputs,
  RuntimeAction,
  SequenceRow,
  ShadowDiff,
  TagCatalog,
  TagExclusionSettings,
  TagExclusionSettingsDraft,
  Validation,
} from "../model/types";
import {
  daySetupToWire,
  capacitiesSettingsToWire,
  projectDaySetupSaveResult,
  promptDraftsToWire,
  descriptionSkipsToWire,
  capacitiesSourceToWire,
  capacitiesRuleSaveToWire,
  capacitiesRefreshStartToWire,
  capacitiesSelectionsToWire,
  projectCapacitiesCatalog,
  projectCapacitiesRules,
  projectCapacitiesRuleSaveResponse,
  projectCapacitiesRefreshStatus,
  projectCapacitiesSelections,
  projectCapacitiesSettings,
  projectCapacitiesSource,
  projectTagExclusionSettings,
  tagExclusionSettingsToWire,
  emptyTagCatalog,
  projectCalendarDrift,
  projectCapacity,
  projectCommitReport,
  projectAnchoredStartSave,
  projectDurationMemoryReset,
  projectDurationMemorySave,
  projectFixedInputs,
  projectLedger,
  projectPlanInputs,
  projectRuntimeAction,
  projectRuntimeBatchResults,
  projectRuntimeBatchUndo,
  projectSequenceResult,
  projectShadow,
  projectValidation,
  rowToWire,
  grantToWire,
  shapeAssignedWire,
  type Wire,
} from "./wire";

/** HTTP failure with the server's error detail preserved (409/422/429/...).
    `path` names the request so callers can tell a /session-token failure from
    a billed /sequence failure instead of assuming the worst. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
    message: string,
    readonly path: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** A request that died before any HTTP status existed (fetch rejection, DNS,
    offline, browser abort). The path lets the controller say whether the
    billed POST was even attempted; a generic TypeError is NOT this. */
export class ApiTransportError extends Error {
  constructor(
    readonly path: string,
    message: string,
    readonly cause?: unknown,
  ) {
    super(message);
    this.name = "ApiTransportError";
  }
}

/** The server answered but the client could not read the response body (not
    valid JSON). Distinct from a transport failure: the reply DID arrive, so
    this must never be reported as "connection lost". Carries path + status,
    never the raw payload. */
export class ApiProtocolError extends Error {
  constructor(
    readonly path: string,
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiProtocolError";
  }
}

/** The request path carried by a typed API failure, or null. Lets the
    controller narrow a failure to the exact request that produced it. */
export function apiFailurePathOf(error: unknown): string | null {
  if (error instanceof ApiTransportError) return error.path;
  if (error instanceof ApiError) return error.path;
  if (error instanceof ApiProtocolError) return error.path;
  return null;
}

/** The single-flight 409 from POST /capacities/refresh/start. Returns the
    server's message when the error is that conflict, null for anything else
    (another status, another 409 body), so callers can treat "a job is already
    running" as state rather than as a failure. */
export function capacitiesRefreshBusyOf(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = error.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const record = detail as Wire;
    if (record.code !== undefined && record.code !== "capacities_refresh_busy") return null;
    if (typeof record.message === "string") return record.message;
  }
  return "A Capacities refresh job is already running.";
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

/** True when a request died at the transport layer rather than returning an
    HTTP status. Only the adapter's typed ApiTransportError and known browser
    fetch-rejection messages count — a generic JS TypeError (a client bug, a
    result-processing failure) is NOT evidence of transport. ApiError and
    ApiProtocolError mean the server DID respond and are never network errors.
    Live case (2026-10-15, Firefox): the operator saw the raw string after a
    long /sequence wait; the browser 300s race is an INFERENCE, not proven. */
export function isNetworkError(error: unknown): boolean {
  if (error instanceof ApiTransportError) return true;
  if (error instanceof ApiError || error instanceof ApiProtocolError) return false;
  const message = error instanceof Error ? error.message : String(error);
  return /network\s?error|failed to fetch|load failed|network request failed/i.test(message);
}

/** Shown when POST /sequence dies at the transport layer (no server status):
    the billed attempt may already have been charged, so say that plainly
    rather than echoing the browser's raw NetworkError. */
export const SEQUENCE_NETWORK_ERROR_MESSAGE =
  "Lost the connection to TDTB while sequencing. The call may still have been billed — check the day budget before retrying.";

/** True when a request is a DETERMINISTIC protocol refusal: the server
    answered with a 4xx status (422 plan refused, 409 conflict, 400 bad
    input), so resending the identical payload cannot succeed — the payload
    has to change first. Deliberately narrow: only the typed ApiError carries
    a status. A transport rejection and a 5xx (or an unreadable 200 body via
    ApiProtocolError) stay retryable, and a bare Error is never a refusal.
    2026-10-16: a 422 "commit refused: forged fixed-lane timing/backdrop" was
    answered by a Retry that resent the same sequence forever. */
export function isDeterministicRefusal(error: unknown): boolean {
  return error instanceof ApiError && error.status >= 400 && error.status < 500;
}

/** Extract the stale-mapping revisions from a real 409 source-save conflict.
    Returns null for anything else — another status, another 409 code such as
    the route's storage error, or a body without both revisions — so callers
    narrow to a typed value instead of an error subclass (house style:
    `e instanceof ApiError && e.status === N`). */
export function capacitiesSourceConflictOf(
  error: unknown,
): { expectedRevision: number; currentRevision: number } | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = error.detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return null;
  const record = detail as Wire;
  if (record.code !== "capacities_source_conflict") return null;
  const expected = record.expected_revision;
  const current = record.current_revision;
  if (!Number.isSafeInteger(expected) || expected < 0) return null;
  if (!Number.isSafeInteger(current) || current < 0) return null;
  return { expectedRevision: expected, currentRevision: current };
}

/** Extract both revisions from a real 409 Capacities rules conflict. Returns
    null for anything else (another status, another 409 code, or a body
    without both revisions). */
export function capacitiesRulesConflictOf(
  error: unknown,
): { expectedRevision: number; currentRevision: number } | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = error.detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return null;
  const record = detail as Wire;
  if (record.code !== "capacities_rules_conflict") return null;
  const expected = record.expected_revision;
  const current = record.current_revision;
  if (!Number.isSafeInteger(expected) || expected < 0) return null;
  if (!Number.isSafeInteger(current) || current < 0) return null;
  return { expectedRevision: expected, currentRevision: current };
}

/** Extract both revisions from a real 409 Capacities selections conflict.
    Returns null for anything else (another status, another 409 code, or a
    body without both revisions). */
export function capacitiesSelectionsConflictOf(
  error: unknown,
): { expectedRevision: number; currentRevision: number } | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = error.detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return null;
  const record = detail as Wire;
  if (record.code !== "capacities_selections_conflict") return null;
  const expected = record.expected_revision;
  const current = record.current_revision;
  if (!Number.isSafeInteger(expected) || expected < 0) return null;
  if (!Number.isSafeInteger(current) || current < 0) return null;
  return { expectedRevision: expected, currentRevision: current };
}

/** Extract both revisions from a real 409 Capacities settings conflict.
    Returns null for anything else (another status, another 409 code, or a
    body without both revisions) so callers narrow to a typed value. */
export function capacitiesSettingsConflictOf(
  error: unknown,
): { expectedRevision: number; currentRevision: number } | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = error.detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return null;
  const record = detail as Wire;
  if (record.code !== "capacities_settings_conflict") return null;
  const expected = record.expected_revision;
  const current = record.current_revision;
  if (!Number.isSafeInteger(expected) || expected < 0) return null;
  if (!Number.isSafeInteger(current) || current < 0) return null;
  return { expectedRevision: expected, currentRevision: current };
}

/** Extract both revisions from a real 409 exclusion-settings conflict.
    Returns null for anything else (another status, another 409 code, or a
    body without both revisions) so callers narrow to a typed value. */
export function exclusionSettingsConflictOf(
  error: unknown,
): { expectedRevision: number; currentRevision: number } | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = error.detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return null;
  const record = detail as Wire;
  if (record.code !== "exclusion_settings_conflict") return null;
  const expected = record.expected_revision;
  const current = record.current_revision;
  if (!Number.isSafeInteger(expected) || expected < 0) return null;
  if (!Number.isSafeInteger(current) || current < 0) return null;
  return { expectedRevision: expected, currentRevision: current };
}

export class ApiAdapter implements Adapter {
  private token: string | null = null;
  /** Server-verbatim /plan-inputs payload — POST-body source of truth. */
  private raw: Wire | null = null;

  constructor(private base: string = "") {}

  private async request(path: string, init?: RequestInit): Promise<Wire> {
    let res: Response;
    try {
      res = await fetch(this.base + path, init);
    } catch (e) {
      // The ONLY genuine transport failure: fetch itself rejected. Carry the
      // path so a /session-token failure is never called a billed failure.
      throw new ApiTransportError(
        path,
        `TDTB request failed before a response was available (${path})`,
        e,
      );
    }
    let body: Wire | string | null = null;
    let readable = true;
    try {
      body = await res.json();
    } catch {
      body = null;
      readable = false;
    }
    if (!res.ok) {
      // Preserve the original HTTP status/detail even when the body was
      // unreadable — an error response is not a transport failure.
      const detail = (body as Wire)?.detail ?? body;
      throw new ApiError(res.status, detail, detailMessage(res.status, detail), path);
    }
    if (!readable || body === null) {
      throw new ApiProtocolError(
        path,
        res.status,
        `TDTB returned an unreadable response (${path} · HTTP ${res.status})`,
      );
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

  /** GET /calendar-drift — read-only, tokenless (like GET /config): one GET
      through the shared request helper, no token, no billed or write
      endpoint. A degraded Calendar still answers 200 and projects normally. */
  async calendarDrift(): Promise<CalendarDrift> {
    return projectCalendarDrift(await this.request("/calendar-drift"));
  }

  /** Explicit source refresh (locked decision 20): exactly one GET
      /plan-inputs + one GET /billed-ledger — never /gather, never a billed
      or write endpoint. A degraded calendar read throws so a bad snapshot
      can neither pass as an "unchanged" fingerprint nor replace the last
      good view. Both projections come from the SAME wire payload. */
  async refreshSources(): Promise<SourceRefreshResult> {
    // Calendar warnings are advisory since 2026-10-19 (OQ-P1, reversing
    // decision-17): the calendar source shows its own note in Connections, and
    // the fresh read stays authoritative instead of failing the whole refresh.
    const wire = await this.request("/plan-inputs");
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

  /** U5 prompt-only save: exactly one POST carrying ONLY `captures`. The
      server treats it as a draft PATCH and never confirms the day. */
  async savePromptDrafts(patch: Record<string, string>): Promise<DaySetupSaveResult> {
    return projectDaySetupSaveResult(
      await this.post("/day-setup", promptDraftsToWire(patch)),
    );
  }

  /** U5 prompt-only skip save: exactly one POST carrying ONLY
      `description_skips`. The map is a full replace and the task is still
      exported — only its description is omitted. */
  async savePromptSkips(skips: DescriptionSkips): Promise<DaySetupSaveResult> {
    return projectDaySetupSaveResult(
      await this.post("/day-setup", descriptionSkipsToWire(skips)),
    );
  }

  async saveMicroAdventure(pick: MicroIdea | null): Promise<void> {
    await this.post("/day-setup", {
      micro_adventure:
        pick === null
          ? null
          : { id: pick.id, idea: pick.idea, category: pick.category },
    });
  }

  async resolveLooseEnd(entry: { date: string; id: string }, done: boolean): Promise<void> {
    await this.post("/micro-adventure/loose-end", { date: entry.date, id: entry.id, done });
  }

  async loadCapacitiesSettings(): Promise<CapacitiesSettings> {
    return projectCapacitiesSettings(await this.request("/settings/capacities"));
  }

  async saveCapacitiesSettings(draft: CapacitiesSettingsDraft): Promise<CapacitiesSettings> {
    return projectCapacitiesSettings(
      await this.post("/settings/capacities/save", capacitiesSettingsToWire(draft)),
    );
  }

  async loadCapacitiesSource(): Promise<CapacitiesSourceRead> {
    return projectCapacitiesSource(await this.request("/settings/capacities/source"));
  }

  async saveCapacitiesSource(draft: CapacitiesSourceDraft): Promise<CapacitiesSourceRead> {
    return projectCapacitiesSource(
      await this.post("/settings/capacities/source/save", capacitiesSourceToWire(draft)),
    );
  }

  /** POST /settings/capacities/source/discover — one token-guarded non-billed
      provider read. The returned catalog is advisory and never persisted. A
      failed discovery surfaces as ApiError (status and detail preserved), so
      it stays distinguishable from a genuinely empty catalog. */
  async discoverCapacitiesSource(spaceId: string): Promise<CapacitiesCatalog> {
    return projectCapacitiesCatalog(
      await this.post("/settings/capacities/source/discover", { space_id: spaceId }),
    );
  }

  async loadCapacitiesRules(): Promise<CapacitiesRules> {
    return projectCapacitiesRules(await this.request("/capacities/rules"));
  }

  async saveCapacitiesRule(args: {
    structureId: string;
    rule: CapacitiesRuleNode;
    fallbackMinutes: number | null;
    expectedRevision: number;
  }): Promise<CapacitiesRuleSaveResponse> {
    return projectCapacitiesRuleSaveResponse(
      await this.post("/capacities/rules", capacitiesRuleSaveToWire(args)),
    );
  }

  async capacitiesRefreshStatus(): Promise<CapacitiesRefreshStatus> {
    return projectCapacitiesRefreshStatus(await this.request("/capacities/refresh/status"));
  }

  async startCapacitiesRefresh(
    mode: CapacitiesRefreshMode,
    scope: string,
  ): Promise<CapacitiesRefreshStatus> {
    return projectCapacitiesRefreshStatus(
      await this.post("/capacities/refresh/start", capacitiesRefreshStartToWire(mode, scope)),
    );
  }

  async cancelCapacitiesRefresh(): Promise<CapacitiesRefreshStatus> {
    return projectCapacitiesRefreshStatus(
      await this.post("/capacities/refresh/cancel", {}),
    );
  }

  async loadCapacitiesSelections(): Promise<CapacitiesSelections> {
    return projectCapacitiesSelections(await this.request("/capacities/selections"));
  }

  async saveCapacitiesSelections(draft: CapacitiesSelectionsDraft): Promise<CapacitiesSelections> {
    return projectCapacitiesSelections(
      await this.post("/capacities/selections", capacitiesSelectionsToWire(draft)),
    );
  }

  /** Last-loaded advisory tag catalog. The save response carries no catalog,
      so a save projects against the most recent inventory rather than
      inventing one. */
  private tagCatalog: TagCatalog | null = null;

  async loadTagExclusionSettings(): Promise<TagExclusionSettings> {
    const settings = projectTagExclusionSettings(
      await this.request("/settings/exclusions"),
    );
    this.tagCatalog = settings.catalog;
    return settings;
  }

  async saveTagExclusionSettings(
    draft: TagExclusionSettingsDraft,
  ): Promise<TagExclusionSettings> {
    return projectTagExclusionSettings(
      await this.post("/settings/exclusions/save", tagExclusionSettingsToWire(draft)),
      this.tagCatalog ?? emptyTagCatalog(),
    );
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

  /** Persist one anchor's start to the tracked repo config: exactly ONE
      token-guarded non-billed POST. The wire time is the cockpit's 24h
      HH:MM; the server normalizes it to the config's "5:45 PM" style. */
  async saveAnchoredStart(block: string, start: string): Promise<AnchoredStartSaveResult> {
    return projectAnchoredStartSave(
      await this.post("/config/anchored-blocks/start", { block, start }),
    );
  }

  async autoSequence(ctx: SequenceContext): Promise<SequenceResult> {
    const raw = this.rawOrThrow();
    const body = {
      assigned: shapeAssignedWire(
        raw.digest?.assigned ?? [],
        ctx.included,
        ctx.timeAdjustmentOptIns,
        ctx.mintOverlapOptIns,
      ),
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
      assigned: shapeAssignedWire(
        raw.digest?.assigned ?? [],
        ctx.included,
        ctx.timeAdjustmentOptIns,
        ctx.mintOverlapOptIns,
      ),
      config: raw.config ?? {},
      anchored_blocks: raw.anchored_blocks ?? [],
      day_semantics: raw.day_semantics ?? {},
      overlap_grants: (ctx.overlapGrants ?? []).map(grantToWire),
      planning_config_fingerprint: ctx.planningConfigFingerprint ?? raw.planning_config_fingerprint ?? "",
      pinned_rows: (ctx.pinnedRows ?? []).map(rowToWire),
    };
    return projectValidation(await this.post("/validate-sequence", body));
  }

  async readFixedInputs(): Promise<FixedInputs> {
    // Calendar warnings are advisory since 2026-10-19 (OQ-P1, reversing
    // decision-17): the calendar dot and note in Connections carry them, so a
    // calendar hiccup no longer fails the fixed-input read (mirrors
    // refreshSources). The fresh read stays the latest raw payload for the
    // subsequent POST bodies.
    const wire = await this.request("/plan-inputs");
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
        assigned: shapeAssignedWire(
          raw.digest?.assigned ?? [],
          ctx.included,
          ctx.timeAdjustmentOptIns,
          ctx.mintOverlapOptIns,
        ),
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

  /** KTD1: POST /runtime-actions/reassign {identity} — same journaled envelope
      as every other runtime-action post, so `projectRuntimeAction` reads it
      unchanged. A day-setup or conflict refusal arrives as ApiError through
      the shared error handling, exactly like `runtimeAction`. */
  async reassign(identity: string): Promise<RuntimeAction> {
    return projectRuntimeAction(
      await this.post("/runtime-actions/reassign", { identity }),
    );
  }

  async applyRuntimeBatch(items: RuntimeBatchItemInput[]): Promise<RuntimeBatchResult> {
    return projectRuntimeBatchResults(
      await this.post("/runtime-batches", {
        // The backend requires args (defaults to {}); send it explicitly so a
        // minimal caller tuple stays a valid body.
        items: items.map((item) => ({
          verb: item.verb,
          target: item.target,
          args: item.args ?? {},
        })),
      }),
    );
  }

  async retryRuntimeBatch(batchId: string): Promise<RuntimeBatchResult> {
    return projectRuntimeBatchResults(
      await this.post(`/runtime-batches/${encodeURIComponent(batchId)}/retry`, {}),
    );
  }

  async undoRuntimeBatch(batchId: string): Promise<RuntimeBatchUndoReport> {
    return projectRuntimeBatchUndo(
      await this.post(`/runtime-batches/${encodeURIComponent(batchId)}/undo`, {}),
    );
  }
}
