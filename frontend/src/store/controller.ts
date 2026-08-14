/* controller.ts — async orchestration between adapter and store.
   Owns the safety choreography the reducer alone can't express:

   - Auto sequence is ONE explicit user action; fingerprint of fixed inputs
     is captured from a fresh read at sequence time.
   - Shadow preview and live commit each re-read fixed inputs FIRST; drift →
     FINGERPRINT_MISMATCH (staged plan invalidated), read failure → blocked,
     never treated as unchanged (locked decision 17).
   - Live commit dispatches only through requestLiveCommit, which re-checks
     canLiveCommit at call time — the second-click gate (locked decision 9). */

import type { Adapter, SequenceContext } from "../adapters/adapter";
import { ApiError } from "../adapters/api";
import { projectSequenceRow } from "../adapters/wire";
import { fingerprintFixedInputs } from "../model/fingerprint";
import { isStagingVerb } from "../model/staging";
import { prunePins } from "../model/pins";
import { planOverflow, calendarWalls, mintWalls } from "../model/overflow";
import { toMinutes } from "../model/time";
import { droppedItems } from "../model/placement";
import type { AppState, Action } from "./store";
import {
  canLiveCommit,
  canAutoSequence,
  defectsResolved,
  effectiveAnchoredBlocks,
  effectiveBlocks,
  includedItems,
} from "./store";
import type { MicroIdea, SequenceRow, AnchoredOverride } from "../model/types";

export type Dispatch = (a: Action) => void;
export type GetState = () => AppState;

/** How long a deferred override waits for the drag to settle before it spends
    a capacity + validate round trip. Long enough to swallow a drag's worth of
    input events, short enough that releasing the thumb feels immediate. */
const OVERRIDE_SETTLE_MS = 140;

/** Deterministic chronological sort key for a final sequence (FEEDBACK-01).
    HH:MM strings compare lexicographically; id breaks ties so equal starts
    stay stable and match the server's merge_immutable_rows ordering. */
function byStartThenId(a: SequenceRow, b: SequenceRow): number {
  if (a.start !== b.start) return a.start < b.start ? -1 : 1;
  if (a.id !== b.id) return a.id < b.id ? -1 : 1;
  return 0;
}

/** T28 + FEEDBACK-09: a calendar row's day-setup entry is plan participation
    (skipToday) plus a local accounting projection (blocks) — time can never
    reach an imported event (LD19), and the projection crosses so Calendar
    impact can count it today only. Omitted when absent, keeping the T28 wire
    shape byte-identical for plain attendance toggles. */
function calendarParticipation(
  o: AppState["daySetup"]["anchored"][string],
): AnchoredOverride {
  const out: AnchoredOverride = {
    on: true,
    skipToday: o.skipToday === true,
    time: null,
  };
  if (o.blocks != null) out.blocks = o.blocks;
  return out;
}

export class Controller {
  // Monotonic request tokens: rapid edits fire overlapping revalidate/capacity
  // calls; a stale response landing after a newer one must never overwrite it.
  private revalidateToken = 0;
  private capacityToken = 0;
  private overrideTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private adapter: Adapter,
    private dispatch: Dispatch,
    private getState: GetState,
  ) {}

  /** Today-only shaping context for adapter POST bodies: included rows with
      their effective blocks (locked decision 16). */
  private sequenceContext(pinnedRows?: SequenceRow[]): SequenceContext {
    const s = this.getState();
    const included = includedItems(s).map((i) => ({
      id: i.id,
      blocks: effectiveBlocks(s, i.id),
    }));
    // T12 qualification (2026-07-26): pins persist to localStorage and
    // rehydrate via SESSION_RESTORED with no check against the freshly loaded
    // pool, so a pin outlives its item. The server then rejects the WHOLE
    // request — "foreign pinned row" — and the block is unrecoverable from
    // the UI: the row it names is gone from the queue, so there is nothing
    // left to press. Reconcile against the set actually being sent; a pin
    // naming an item we are not sending is meaningless by construction.
    const includedIds = new Set(included.map((i) => i.id));
    const pins = prunePins(
      (pinnedRows ?? s.pinnedRows).filter((row) => includedIds.has(row.id)),
    );
    return {
      included,
      planningConfigFingerprint:
        s.planningConfigFingerprint ?? s.inputs?.planningConfigFingerprint ?? "",
      overlapGrants: s.overlapGrants,
      pinnedRows: pins,
    };
  }

  async load(): Promise<void> {
    try {
      const [inputs, ledger] = await Promise.all([
        this.adapter.loadPlanInputs(),
        this.adapter.billedLedger(),
      ]);
      this.dispatch({ type: "INPUTS_LOADED", inputs, ledger });
    } catch (e) {
      this.dispatch({ type: "LOAD_FAILED", error: String(e instanceof Error ? e.message : e) });
    }
  }

  /** Explicit source refresh (locked decision 20): re-GET /plan-inputs +
      /billed-ledger, reconcile, then re-earn capacity/validation through the
      deterministic free endpoints. GETs only — the adapter method cannot
      reach /gather or a billed/write endpoint. */
  async refreshSources(): Promise<void> {
    const s = this.getState();
    if (
      s.refresh.phase === "loading" ||
      s.seqPhase === "sequencing" ||
      s.shadowPhase === "loading" ||
      s.commitPhase === "committing"
    ) {
      return;
    }
    this.dispatch({ type: "SOURCE_REFRESH_START" });
    try {
      const { inputs, fixed, ledger } = await this.adapter.refreshSources();
      this.dispatch({
        type: "SOURCE_REFRESH_OK",
        inputs,
        ledger,
        fingerprint: fingerprintFixedInputs(fixed),
        anchoredSourceFingerprint: fixed.anchoredSourceFingerprint,
        planningConfigFingerprint: fixed.planningConfigFingerprint,
        at: new Date().toISOString(),
      });
      // Free deterministic follow-ups: server-verbatim capacity for the
      // reconciled include set; surviving staged rows re-earn "valid".
      void this.refreshCapacity();
      void this.revalidate();
    } catch (e) {
      this.dispatch({
        type: "SOURCE_REFRESH_FAIL",
        error: String(e instanceof Error ? e.message : e),
      });
    }
  }

  async saveDaySetup(daySetup: AppState["daySetup"]): Promise<void> {
    const calendarIds = new Set(
      (this.getState().inputs?.anchored ?? [])
        .filter((a) => a.kind === "calendar")
        .map((a) => a.id),
    );
    // T28 + FEEDBACK-09: a calendar row's override is plan participation plus
    // a local accounting projection — sanitized to the {attending | not-
    // attending, projection} shape so time/duration edits can never reach an
    // imported event (LD19). An explicit skipToday:false entry is kept (not
    // dropped) so a restore beats server rows that still carry the dismissal
    // until the next source refresh.
    const anchored = Object.fromEntries(
      Object.entries(daySetup.anchored).map(([id, o]) =>
        calendarIds.has(id)
          ? [id, calendarParticipation(o)]
          : [id, o],
      ),
    );
    const sanitized = { ...daySetup, anchored };
    await this.adapter.saveDaySetup(sanitized);
    this.dispatch({ type: "SETUP_SAVED", daySetup: sanitized });
    await this.refreshCapacity();
  }

  /** T19: persist a Live micro-adventure override (shuffle / pool pick /
      custom text), or clear it (null) back to the deterministic auto-pick —
      pool[0] is the server's LRU auto-pick by contract. Free, unbilled,
      never a history write; the history log is consumed only by the
      authorized live commit. */
  async setMicroAdventure(pick: MicroIdea | null): Promise<void> {
    await this.adapter.saveMicroAdventure(pick);
    const pool = this.getState().inputs?.microAdventure.pool ?? [];
    this.dispatch({
      type: "MICRO_SET",
      pick: pick ?? pool[0] ?? null,
      source: pick === null ? "auto" : "override",
    });
  }

  /* T18g's saveAndRegenerateDay (save + immediate billed autoSequence) was
     removed at the T12 qualification, 2026-07-26. Under the allocator spine
     the billed call belongs at an explicit Send AFTER staging — wiring it to
     the Day Setup save spent the call before the allocator surface was ever
     seen, so sliders, per-row verbs and forgot-strip assigns could only ever
     land after the money was gone. Day Setup now saves and refreshes capacity
     (both free); `autoSequence` is reached only from the action dock.
     Callers: use `saveDaySetup` — it already sets SETUP_SAVED and refreshes
     capacity, and SetupDrawer's payload carries `confirmed: true` itself. */

  async saveAnchoredOverride(
    id: string,
    override: AppState["daySetup"]["anchored"][string],
  ): Promise<void> {
    const block = this.getState().inputs?.anchored.find((a) => a.id === id);
    if (!block) return;
    // T28 + FEEDBACK-09: calendar rows accept exactly participation plus the
    // local accounting projection.
    const sanitized =
      block.kind === "calendar"
        ? calendarParticipation(override)
        : override;
    const setup = this.getState().daySetup;
    await this.saveDaySetup({
      ...setup,
      anchored: { ...setup.anchored, [id]: sanitized },
      confirmed: true,
    });
  }

  /** `defer` coalesces the two follow-up round trips instead of firing them
      per event. A slider drag emits an input event per pixel, and each one
      re-validated the sequence — every resolution re-rendered the footer's
      alert list, which changed the sticky footer's height and shoved the row
      out from under the cursor. The dispatch stays synchronous either way, so
      the thumb and the readout still track the drag exactly. */
  setOverride(
    id: string,
    included: boolean,
    blocks: number | null,
    opts?: { defer?: boolean },
  ): void {
    this.dispatch({ type: "OVERRIDE_SET", id, override: { included, blocks } });
    if (!opts?.defer) {
      this.flushOverrideEffects();
      return;
    }
    if (this.overrideTimer != null) clearTimeout(this.overrideTimer);
    this.overrideTimer = setTimeout(() => {
      this.overrideTimer = null;
      this.flushOverrideEffects();
    }, OVERRIDE_SETTLE_MS);
  }

  private flushOverrideEffects(): void {
    void this.refreshCapacity();
    void this.revalidate();
  }

  async refreshCapacity(): Promise<void> {
    const s = this.getState();
    if (!s.inputs) return;
    const token = ++this.capacityToken;
    const selected = includedItems(s).map((i) => effectiveBlocks(s, i.id));
    const capacity = await this.adapter.capacityPreview(s.daySetup, selected);
    if (token !== this.capacityToken) return; // superseded by a newer edit
    this.dispatch({ type: "CAPACITY_UPDATED", capacity });
  }

  /** The one billed action. Explicit user click only — never auto-fired. */
  async autoSequence(): Promise<void> {
    const s = this.getState();
    if (!canAutoSequence(s)) return;
    this.dispatch({ type: "SEQUENCE_START" });
    try {
      const fixed = await this.adapter.readFixedInputs();
      const context = this.sequenceContext(this.getState().pendingPinnedRows);
      // readFixedInputs refreshes the adapter's raw contract after a setup
      // save. Use that same fresh planning fingerprint for the paid request;
      // the store still reflects the prior read until SEQUENCE_OK lands.
      context.planningConfigFingerprint =
        fixed.planningConfigFingerprint ?? context.planningConfigFingerprint;
      const result = await this.adapter.autoSequence(context);
      const included = new Set(includedItems(this.getState()).map((i) => i.id));
      const injected = (r: SequenceRow) =>
        r.wire?.source === "schedulable" ||
        r.id === "Minting" ||
        r.id === "Quick Tasks" ||
        r.id === "Shivery Jigs" ||
        /^Mint /i.test(r.id);
      const rows = result.sequence.filter(
        (r) => r.kind === "zone" || included.has(r.id) || injected(r),
      );
      // Overbooked days: the sequencer drops what does not fit, and a dropped
      // row gets no time, so it reaches no surface and never appears in
      // BusyCal. Lay those out deterministically from the frame anchor
      // instead — ordinary work rows, written by source like any other
      // (Adam, 2026-07-27). Free: no second billed call.
      // FEEDBACK-02: overflow is wall-aware — non-permeable calendar walls
      // (post Day-Setup dismissals) are skipped so a staged row can never be
      // the exact calendar-wall overlap the server now hard-rejects.
      // FEEDBACK-03: placement scans free gaps — after the calendar walls AND
      // the server's effective immutable pin set (manual pins + recurring
      // auto-pins). A dropped row no gap can hold is reported as explicit
      // infeasibility in the sequence warnings (naming the row, its need,
      // and the free capacity) instead of silently omitted; it is not staged
      // and it gates shadow/commit behind LD24 acceptance like any defect.
      const post = this.getState();
      const pins = result.pinnedRows ?? post.pendingPinnedRows;
      // FEEDBACK-25: selected Mint session intervals are hard walls too —
      // the server hard-rejects any row overlapping one, so the free-gap
      // scan must not stage a dropped row over protected Mint time.
      const plan = planOverflow(
        droppedItems(includedItems(post), rows, (i) => effectiveBlocks(post, i.id)),
        post.inputs?.time.anchor ?? "",
        (i) => effectiveBlocks(post, i.id),
        [...calendarWalls(effectiveAnchoredBlocks(post)), ...mintWalls(rows)],
        (pins ?? [])
          .filter((r) => r.kind !== "zone")
          .map((r) => ({ start: toMinutes(r.start), end: toMinutes(r.end) }))
          .filter((i) => i.end > i.start),
      );
      const overflow = plan.rows;
      const infeasibleWarnings = plan.infeasible.map(
        (f) => `⚠ overflow infeasible — ${f.id}: ${f.reason}`,
      );
      // FEEDBACK-01: overflow rows are laid out from the frame anchor, which
      // on a late-anchored overbooked day is LATER than some server-validated
      // rows (the plan reaches 23:15 while overflow starts at 17:15).
      // Appending them verbatim produced a descending sequence the server
      // then rejected — 'Log hours' 17:15 after 23:15. Sort the final merged
      // sequence by start time (id tiebreak, mirroring merge_immutable_rows)
      // so what the UI stages is exactly what validation accepts; the sort
      // reorders, never drops.
      const sequence = [...rows, ...overflow].sort(byStartThenId);
      const ledger = await this.adapter.billedLedger();
      this.dispatch({
        type: "SEQUENCE_OK",
        sequence,
        overflowIds: overflow.map((r) => r.id),
        warnings: [...result.warnings, ...infeasibleWarnings],
        fingerprint: fingerprintFixedInputs(fixed),
        anchoredSourceFingerprint: fixed.anchoredSourceFingerprint,
        planningConfigFingerprint: fixed.planningConfigFingerprint,
        overlapGrants: result.overlapGrants,
        // T27: prefer the server's effective pin set (client + recurring
        // auto-pins) so validate/commit snapshots stay byte-exact.
        pinnedRows: result.pinnedRows ?? this.getState().pendingPinnedRows,
        ledger,
      });
    } catch (e) {
      const ledger = await this.adapter.billedLedger().catch(() => null);
      // T12 qualification: a hard validation failure arrives AFTER the SDK
      // call was made and the ledger charged. The server now returns the
      // rejected body so the paid plan is at least readable instead of
      // vanishing; it is held separately from `sequence` so nothing can
      // mistake it for something committable.
      let rejectedProposal: SequenceRow[] | null = null;
      if (e instanceof ApiError && e.status === 422) {
        const detail = e.detail as { rejected_proposal?: { sequence?: unknown[] } } | undefined;
        const rows = detail?.rejected_proposal?.sequence;
        if (Array.isArray(rows) && rows.length > 0) {
          rejectedProposal = rows.map((r) => projectSequenceRow(r as never));
        }
      }
      this.dispatch({
        type: "SEQUENCE_FAIL",
        error: String(e instanceof Error ? e.message : e),
        ledger,
        rejectedProposal,
      });
    }
  }

  /** Deterministic revalidation (free) — after placement edits or exclusions. */
  async revalidate(): Promise<void> {
    const s = this.getState();
    if (!s.sequence || s.sequence.every((r) => r.kind === "zone")) return;
    const token = ++this.revalidateToken;
    const validation = await this.adapter.validateSequence(s.sequence, this.sequenceContext());
    if (token !== this.revalidateToken) return; // superseded by a newer edit
    this.dispatch({ type: "VALIDATED", validation });
  }

  moveRow(id: string, start: string): void {
    this.dispatch({ type: "ROW_MOVED", id, start });
    void this.revalidate();
  }

  placeRow(id: string, start: string): void {
    this.dispatch({ type: "ROW_PLACED", id, start });
    void this.revalidate();
  }

  unplaceRow(id: string): void {
    this.dispatch({ type: "ROW_UNPLACED", id });
    void this.revalidate();
  }

  resetPlacement(id: string): void {
    this.dispatch({ type: "ROW_PIN_RESET", id });
  }

  /** Release a row's placement entirely: drop its pin AND take it out of the
      staged sequence, so it goes back to needs-placement and the next Send is
      free to put it somewhere else.

      Both dispatches are needed. ROW_UNPLACED clears the pin too, but bails
      early when there is no sequence — which is exactly the case for a row
      pinned by hand before any Send. ROW_PIN_RESET covers that; the second
      dispatch is a no-op when there is nothing staged. */
  releasePlacement(id: string): void {
    this.dispatch({ type: "ROW_PIN_RESET", id });
    this.dispatch({ type: "ROW_UNPLACED", id });
    void this.revalidate();
  }

  /** T20: one journaled runtime verb against one committed plan item. The
      server refuses anything not in today's manifest; every failure mode
      (surface unavailable, partial, compensated) lands in state for the
      banner — never a silent retry. */
  async runtimeAction(
    verb: string, target: string, args: Record<string, unknown> = {},
  ): Promise<void> {
    this.dispatch({ type: "RUNTIME_START" });
    try {
      const action = await this.adapter.runtimeAction(verb, target, args);
      this.dispatch({ type: "RUNTIME_OK", action });
    } catch (e) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: String(e instanceof Error ? e.message : e),
      });
    }
  }

  /** Allocator-rewrite T3 → IMP-07: one staging-phase verb fired from a
      Today's-work row, BEFORE any commit exists. Reuses runtimeAction
      wholesale — same journal, same undo, same failure surfacing — and adds
      the one thing the staging phase needs: a source refresh on success, so
      the done/dropped/unassigned/deleted row actually leaves the queue
      instead of lingering as a stale row the user can act on twice.

      Guards the verb locally as well as server-side: offering a placement
      verb here would earn a 422 the user can do nothing about. */
  async stagingAction(verb: string, target: string): Promise<void> {
    if (!isStagingVerb(verb)) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: `${verb} needs a committed plan item`,
      });
      return;
    }
    await this.runtimeAction(verb, target);
    const s = this.getState();
    if (s.runtimeError) return;
    // A refused or failed action comes back HTTP 200 with the failure IN the
    // journal entry — only a thrown adapter error sets runtimeError. Without
    // this check a failed verb read as success: no message, and a source
    // refresh that made the row look handled. (Caught on the scratch server:
    // a vault note with no `status:` line failed closed, correctly, and the
    // UI said nothing at all.)
    const last = s.lastRuntimeAction;
    if (last && last.status !== "applied") {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: last.error ?? `${verb} did not apply (${last.status})`,
      });
      return;
    }
    // A staged verb settles the row for TODAY — done, dropped from plan,
    // unassigned, or deleted outright, it is not part of this plan any more.
    // Say so locally instead of waiting on the source refresh: whether the
    // row actually disappears depends on the surface (a dropped Todoist task
    // leaves the Today filter; a vault row may not), and a verb that fires,
    // journals, and leaves the queue untouched reads as a verb that did
    // nothing. Undo re-includes it.
    this.setOverride(target, false, this.getState().overrides[target]?.blocks ?? null);
    await this.refreshSources();
  }

  /** T20: the one scoped undo — reverses the LAST applied runtime action. */
  async undoRuntimeAction(): Promise<void> {
    const last = this.getState().lastRuntimeAction;
    if (!last || last.status !== "applied") return;
    this.dispatch({ type: "RUNTIME_START" });
    try {
      const action = await this.adapter.undoRuntimeAction(last.id);
      this.dispatch({ type: "RUNTIME_UNDO_OK", action });
      // Mirror of the staging exclusion above: undoing the verb puts the row
      // back in today, or Undo would reverse the write and leave the queue
      // still showing it gone.
      const target = this.getState().inputs?.assigned.find(
        (i) => i.id === action.targetName || i.name === action.targetName,
      );
      if (target) {
        this.setOverride(target.id, true, this.getState().overrides[target.id]?.blocks ?? null);
      }
    } catch (e) {
      this.dispatch({
        type: "RUNTIME_FAIL",
        error: String(e instanceof Error ? e.message : e),
      });
    }
  }

  /** Manual-layout fingerprint capture: a validated manual plan also pins the
      fixed inputs it was built against. */
  private async captureFingerprint(): Promise<{
    fingerprint: string;
    anchoredSourceFingerprint: string;
    planningConfigFingerprint: string;
  }> {
    const fixed = await this.adapter.readFixedInputs();
    return {
      fingerprint: fingerprintFixedInputs(fixed),
      anchoredSourceFingerprint: fixed.anchoredSourceFingerprint,
      planningConfigFingerprint: fixed.planningConfigFingerprint ?? "",
    };
  }

  /** Mandatory pre-shadow / pre-commit drift check. Returns true when the
      staged plan is still valid against a FRESH fixed-input read. */
  private async fixedInputsStillValid(): Promise<boolean> {
    const s = this.getState();
    const fresh = await this.adapter.readFixedInputs(); // throws on failure → caller blocks
    const fp = fingerprintFixedInputs(fresh);
    if (s.fingerprint === null) {
      // Manual layout that never sequenced: adopt the fresh fingerprint.
      return true;
    }
    if (
      fp !== s.fingerprint ||
      fresh.anchoredSourceFingerprint !== s.anchoredSourceFingerprint
      || fresh.planningConfigFingerprint !== s.planningConfigFingerprint
    ) {
      this.dispatch({ type: "FINGERPRINT_MISMATCH" });
      return false;
    }
    return true;
  }

  async shadowPreview(): Promise<void> {
    const s = this.getState();
    // Acceptable defects gate preview until explicitly accepted (locked
    // decision 24) — controller-level twin of the dock's canShadow guard.
    if (!s.sequence || s.validation?.ok !== true || !defectsResolved(s)) return;
    this.dispatch({ type: "SHADOW_START" });
    try {
      if (!(await this.fixedInputsStillValid())) return;
      if (this.getState().fingerprint === null) {
        // Manual layout that never sequenced: pin the fresh fixed inputs now.
        this.dispatch({ type: "FINGERPRINT_ADOPTED", ...(await this.captureFingerprint()) });
      }
      const shadow = await this.adapter.shadowCommit(
        this.getState().sequence as SequenceRow[],
        this.sequenceContext(),
      );
      this.dispatch({ type: "SHADOW_OK", shadow });
    } catch (e) {
      this.dispatch({ type: "SHADOW_FAIL", error: String(e instanceof Error ? e.message : e) });
    }
  }

  armLive(): void {
    this.dispatch({ type: "ARM_LIVE" });
  }

  disarmLive(): void {
    this.dispatch({ type: "DISARM_LIVE" });
  }

  /** The second click. Re-reads fixed inputs immediately before writing. */
  async requestLiveCommit(): Promise<void> {
    const s = this.getState();
    if (!canLiveCommit(s)) return;
    this.dispatch({ type: "COMMIT_START" });
    let driftOk: boolean;
    try {
      driftOk = await this.fixedInputsStillValid();
    } catch {
      // Source read failed → commit BLOCKED, not attempted (locked decision 17).
      this.dispatch({ type: "COMMIT_ABORT" });
      return;
    }
    if (!driftOk) {
      // FINGERPRINT_MISMATCH already invalidated the staged plan.
      this.dispatch({ type: "COMMIT_ABORT" });
      return;
    }
    try {
      const report = await this.adapter.liveCommit(
        s.sequence as SequenceRow[],
        this.sequenceContext(),
      );
      this.dispatch({ type: "COMMIT_DONE", report });
    } catch (e) {
      // 409 (single-flight) / 422 (plan refused) are guaranteed-no-write
      // rejections: plan stays intact and retryable. Anything else may have
      // partially written — surface it as a failed report, never swallow.
      if (e instanceof ApiError && (e.status === 409 || e.status === 422)) {
        this.dispatch({ type: "COMMIT_REJECTED", error: e.message });
        return;
      }
      this.dispatch({
        type: "COMMIT_DONE",
        report: {
          status: "failed",
          surfaces: [],
          verifyFailures: [String(e instanceof Error ? e.message : e)],
        },
      });
    }
  }
}
