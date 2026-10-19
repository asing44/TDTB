/* BatchActions — task-actions F6: the compact selection bar and the inline
   review / result / undo panel, as standalone exports (BatchBar, BatchPanel).
   Unmounted until the integration slice places them above the list.

   One surface, no modal. BatchBar shows only while rows are selected: count,
   Clear selection, Select all, then the batchable actions. Delete and exact
   start are deliberately absent (one task at a time). BatchPanel renders
   whenever a review or results exist, even with an empty selection, because
   single-row actions (runRowBatch) report through it too.

   Source-changing actions (Complete, Skip today, Unassign, Hide always) open
   an inline review of the exact named effects; nothing reaches the adapter
   until the explicit Apply. Planning-only actions are applied by the
   controller to today's plan on the spot (no server call), so the panel
   reports them with that copy rather than offering an Apply. Results are kept
   per task: Retry failed re-runs only failures, Undo reverses the whole batch
   and reports reverted / conflict / not-reversible rather than rolling back
   silently. */

import { useEffect, useRef } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { batchRowsOf, type AppState, type BatchItemOutcome } from "../store/store";
import type { BatchAction, BatchRow } from "../model/batchEligibility";
import { isPlanningBatchAction } from "../store/controller";
import "./batch-actions.css";

/** Planning-only duration choices, in 30-minute blocks. */
const DURATIONS: readonly { blocks: number; label: string }[] = [
  { blocks: 1, label: "30 min" },
  { blocks: 2, label: "60 min" },
  { blocks: 3, label: "90 min" },
  { blocks: 4, label: "120 min" },
  { blocks: 6, label: "180 min" },
  { blocks: 8, label: "240 min" },
];

const SOURCE_ACTIONS: readonly BatchAction[] = [
  "complete",
  "skipToday",
  "unassign",
  "hideAlways",
];

const PLAN_ACTIONS: readonly BatchAction[] = [
  "allDay",
  "timeBlock",
  "automaticPlacement",
  "mintAllow",
  "mintDisallow",
];

const SOURCE_NAME: Record<string, string> = {
  todoist: "Todoist",
  capacities: "Capacities",
};

/** Whether every row in the set is recurring (Unassign reads as Skip
    occurrence for it). The wire verb is `unassign` either way. */
function allRecurring(rows: readonly Pick<BatchRow, "isRecurring">[]): boolean {
  return rows.length > 0 && rows.every((r) => r.isRecurring);
}

function actionLabel(action: BatchAction, recurring: boolean): string {
  switch (action) {
    case "complete":
      return "Complete";
    case "skipToday":
      return "Skip today";
    case "unassign":
      return recurring ? "Skip occurrence" : "Unassign in source";
    case "hideAlways":
      return "Hide always";
    case "duration":
      return "Set duration";
    case "allDay":
      return "All-day";
    case "timeBlock":
      return "Time block";
    case "automaticPlacement":
      return "Automatic placement";
    case "mintAllow":
      return "Allow Mint overlap";
    case "mintDisallow":
      return "Disallow Mint overlap";
  }
}

/** What one row gets from a source-changing action, named for the review. */
function rowEffect(action: BatchAction, row: BatchRow): string {
  const source = SOURCE_NAME[row.source] ?? row.source;
  switch (action) {
    case "complete":
      return `Mark done in ${source}`;
    case "skipToday":
      return "Skip today; saved now, restorable from Skipped today";
    case "unassign":
      return row.isRecurring
        ? `Skip occurrence in ${source}`
        : `Unassign in ${source}`;
    case "hideAlways":
      return "Hide always";
    default:
      return actionLabel(action, row.isRecurring);
  }
}

function disabledBar(state: AppState): boolean {
  return state.batch.phase === "applying";
}

export function BatchBar() {
  const { controller } = useApp();
  const state = useAppState();
  const selection = state.ui.selection;
  if (selection.length === 0) return null;

  const total = state.inputs?.assigned.length ?? 0;
  const busy = disabledBar(state);
  const rows = batchRowsOf(state);
  const recurring = allRecurring(rows);
  const pending =
    state.batch.phase === "review" || state.batch.phase === "done"
      ? state.batch.action
      : null;
  const allSelected = total > 0 && selection.length >= total;
  const some = selection.length > 0 && !allSelected;

  const button = (action: BatchAction) => (
    <button
      type="button"
      class="batch-bar__btn"
      data-batch-action={action}
      disabled={busy}
      aria-pressed={pending === action && state.batch.phase === "review"}
      onClick={() => controller.reviewBatch(action)}
    >
      {actionLabel(action, recurring)}
    </button>
  );

  return (
    <section class="batch-bar" aria-label="Batch actions" data-testid="batch-bar">
      <div class="batch-bar__top">
        <label class="batch-bar__all">
          <input
            type="checkbox"
            checked={allSelected}
            indeterminate={some}
            aria-checked={some ? "mixed" : allSelected}
            disabled={busy}
            onChange={(e) => {
              if ((e.currentTarget as HTMLInputElement).checked) {
                controller.selectAllRows();
              } else {
                controller.clearSelection();
              }
            }}
          />
          <span>Select all</span>
        </label>
        <span class="batch-bar__count" data-testid="batch-count">
          {selection.length} selected
        </span>
        <button
          type="button"
          class="batch-bar__btn batch-bar__clear"
          disabled={busy}
          onClick={() => controller.clearSelection()}
        >
          Clear selection
        </button>
      </div>
      <div class="batch-bar__groups">
        <div class="batch-bar__group" role="group" aria-label="Source actions, review first">
          <span class="batch-bar__glabel">Source</span>
          {SOURCE_ACTIONS.map((a) => button(a))}
        </div>
        <div class="batch-bar__group" role="group" aria-label="Planning actions, plan only">
          <span class="batch-bar__glabel">Plan only</span>
          {PLAN_ACTIONS.slice(0, 3).map((a) => button(a))}
          <label class="batch-sr-only" for="batch-duration">
            Set duration for selected
          </label>
          <select
            id="batch-duration"
            class="batch-bar__select"
            value=""
            disabled={busy}
            onChange={(e) => {
              const el = e.currentTarget as HTMLSelectElement;
              const blocks = Number(el.value);
              el.value = "";
              if (blocks > 0) controller.reviewBatch("duration", { blocks });
            }}
          >
            <option value="">Duration…</option>
            {DURATIONS.map((d) => (
              <option value={String(d.blocks)}>{d.label}</option>
            ))}
          </select>
          {PLAN_ACTIONS.slice(3).map((a) => button(a))}
        </div>
      </div>
      <p class="batch-bar__note">
        Exact start times and Delete are one task at a time. Source actions
        change nothing until you Apply the review.
      </p>
    </section>
  );
}

interface RowInfo {
  name: string;
  row: BatchRow | null;
}

/** Names survive the refresh that removes an applied row from the queue, so
    the result list can still say who each outcome belongs to. */
function useRowNames(state: AppState): (id: string) => RowInfo {
  const cache = useRef(new Map<string, RowInfo>());
  const review = state.batch.review;
  if (review) {
    for (const row of batchRowsOf(state, review.eligible)) {
      cache.current.set(row.id, { name: row.name, row });
    }
    for (const ex of review.excluded) {
      if (!cache.current.has(ex.id)) cache.current.set(ex.id, { name: ex.name, row: null });
    }
  }
  return (id) => cache.current.get(id) ?? { name: id, row: null };
}

function statusKind(status: string): "ok" | "bad" | "other" {
  if (status === "applied") return "ok";
  // duplicate = already settled server-side; everything else (failed,
  // refused, or an unknown status) is a non-success and reads as a failure.
  if (status === "duplicate") return "other";
  return "bad";
}

function statusWord(status: string): string {
  if (status === "applied") return "Done";
  if (status === "failed") return "Failed";
  if (status === "refused") return "Refused";
  return status;
}

export function BatchPanel() {
  const { controller } = useApp();
  const state = useAppState();
  const batch = state.batch;
  const nameOf = useRowNames(state);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const report = batch.undoReport;

  const showing = batch.phase !== "idle" && batch.action !== null;
  useEffect(() => {
    if (showing) headingRef.current?.focus();
  }, [showing, batch.phase, batch.id, batch.action, report]);

  const action = batch.action;
  const review = batch.review;
  const busy = batch.phase === "applying";
  const reviewRows = review ? batchRowsOf(state, review.eligible) : [];
  const recurring = allRecurring(reviewRows);
  const label = action ? actionLabel(action, recurring) : "";

  const results = batch.results;
  const applied = results.filter((r) => r.status === "applied").length;
  const failed = results.filter((r) => r.status === "failed").length;
  const refused = results.filter((r) => r.status === "refused").length;
  const problems = results.filter(
    (r) => r.status !== "applied" && r.status !== "duplicate",
  ).length;
  // Names only the kinds present; never "done" when nothing succeeded.
  const problemWord =
    problems === 0
      ? ""
      : refused === problems
        ? `${problems} refused`
        : failed === problems
          ? `${problems} failed`
          : `${problems} failed or refused`;
  const summary =
    problems === 0
      ? `${applied} done`
      : applied > 0
        ? `${applied} done · ${problemWord}`
        : problemWord;
  const planning = action !== null && isPlanningBatchAction(action);

  let live = "";
  if (showing && review && action) {
    if (busy) live = `${label}: applying…`;
    else if (batch.phase === "review") {
      live = `Review ready: ${review.label} for ${label}. Nothing has changed yet.`;
    } else if (planning) {
      live = `${label} staged in today's plan for ${review.counts.eligible} of ${review.counts.total}. Nothing was sent to sources.`;
    } else if (report) {
      live = `Undo: ${report.reverted.length} reverted, ${report.conflicts.length} conflicts, ${report.nonReversible.length} not reversible.`;
    } else {
      live = `${label}: ${summary.replace(" · ", ", ")}.`;
    }
  }

  const dismiss = () => {
    const prior = action;
    controller.cancelReview();
    if (prior && typeof document !== "undefined") {
      document
        .querySelector<HTMLElement>(`[data-batch-action="${prior}"]`)
        ?.focus();
    }
  };

  const liveRegion = (
    <div class="batch-sr-only" role="status" aria-live="polite" data-testid="batch-live">
      {live}
    </div>
  );

  if (!showing || !review || !action) return liveRegion;

  const targetName = new Map<string, string>(
    results.map((r) => [r.target, nameOf(r.id).name]),
  );
  const named = (target: string) => targetName.get(target) ?? target;

  // Results view: a server batch has reported, or a retry is in flight.
  if (results.length > 0) {
    const row = (r: BatchItemOutcome) => {
      const kind = statusKind(r.status);
      return (
        <li data-status={r.status}>
          <span class={`batch-chip batch-chip--${kind}`}>{statusWord(r.status)}</span>
          <strong>{nameOf(r.id).name}</strong>
          {kind === "bad" ? (
            <span class="batch-panel__fail">{r.error ?? statusWord(r.status)}</span>
          ) : null}
        </li>
      );
    };
    return (
      <>
        {liveRegion}
        <section class="batch-panel" aria-label="Batch result" data-testid="batch-results">
          <h2 class="batch-panel__h" tabindex={-1} ref={headingRef}>
            Result · {label}
            <span class={`batch-chip batch-chip--${problems ? "bad" : "ok"}`}>
              {summary}
            </span>
            {report ? <span class="batch-chip batch-chip--info">Undo reported</span> : null}
          </h2>
          <ul class="batch-panel__list">{results.map(row)}</ul>
          {review.excluded.length > 0 ? (
            <p class="batch-panel__why">
              Not included:{" "}
              {review.excluded.map((x) => `${x.name} (${x.reason})`).join("; ")}
            </p>
          ) : null}
          {report ? (
            <div class="batch-panel__undo" data-testid="batch-undo-report">
              <p class="batch-panel__sub">Undo report</p>
              <ul class="batch-panel__list">
                {report.reverted.length > 0 ? (
                  <li>
                    <span class="batch-chip batch-chip--ok">Reverted</span>
                    <span>{report.reverted.map(named).join(", ")}</span>
                  </li>
                ) : null}
                {report.conflicts.map((c) => (
                  <li>
                    <span class="batch-chip batch-chip--warn">Conflict</span>
                    <strong>{named(c.target)}</strong>
                    <span class="batch-panel__reason">{c.reason}; left as is</span>
                  </li>
                ))}
                {report.nonReversible.map((c) => (
                  <li>
                    <span class="batch-chip batch-chip--bad">Not reversible</span>
                    <strong>{named(c.target)}</strong>
                    <span class="batch-panel__reason">{c.reason}</span>
                  </li>
                ))}
                {report.reverted.length === 0 &&
                report.conflicts.length === 0 &&
                report.nonReversible.length === 0 ? (
                  <li class="batch-panel__reason">Nothing to undo.</li>
                ) : null}
              </ul>
            </div>
          ) : null}
          <div class="batch-panel__acts">
            {!report && failed > 0 ? (
              <button
                type="button"
                class="batch-panel__primary"
                disabled={busy}
                onClick={() => void controller.retryFailed()}
              >
                {busy ? "Retrying…" : `Retry failed (${failed})`}
              </button>
            ) : null}
            {!report && applied > 0 ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => void controller.undoBatch()}
              >
                Undo whole batch
              </button>
            ) : null}
            <button type="button" disabled={busy} onClick={dismiss}>
              Dismiss
            </button>
          </div>
        </section>
      </>
    );
  }

  // Planning-only: already applied to today's plan; report, nothing to Apply.
  if (planning && batch.phase === "done") {
    return (
      <>
        {liveRegion}
        <section class="batch-panel" aria-label="Batch result" data-testid="batch-planning">
          <h2 class="batch-panel__h" tabindex={-1} ref={headingRef}>
            {label}
            <span class="batch-chip batch-chip--info">Plan only · staged</span>
          </h2>
          <p class="batch-panel__eligible">{review.label}</p>
          <p class="batch-panel__why">
            {review.effects}. Staged in today&apos;s plan on this screen; nothing
            was sent to Todoist or Capacities.
          </p>
          {review.excluded.length > 0 ? (
            <ExcludedList excluded={review.excluded} />
          ) : null}
          <div class="batch-panel__acts">
            <button type="button" onClick={dismiss}>
              Dismiss
            </button>
          </div>
        </section>
      </>
    );
  }

  // Source-changing review (also shown while the first Apply is in flight).
  const noneEligible = review.counts.eligible === 0;
  return (
    <>
      {liveRegion}
      <section class="batch-panel batch-panel--source" aria-label="Batch review" data-testid="batch-review">
        <h2 class="batch-panel__h" tabindex={-1} ref={headingRef}>
          Review · {label}
          <span class="batch-chip batch-chip--warn">Changes sources</span>
        </h2>
        <p class="batch-panel__eligible" data-testid="batch-eligible">
          {review.label}
        </p>
        {reviewRows.length > 0 ? (
          <>
            <p class="batch-panel__sub">Will happen</p>
            <ul class="batch-panel__list" data-testid="batch-effects">
              {reviewRows.map((r) => (
                <li>
                  <strong>{r.name}</strong>
                  <span>{rowEffect(action, r)}</span>
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {review.excluded.length > 0 ? (
          <ExcludedList excluded={review.excluded} />
        ) : null}
        {action === "skipToday" ? (
          <p class="batch-panel__why">
            Skip today is saved right away, not just for this session.
          </p>
        ) : null}
        <div class="batch-panel__acts">
          <button
            type="button"
            class="batch-panel__primary"
            disabled={busy || noneEligible}
            onClick={() => void controller.applyBatch()}
          >
            {busy ? "Applying…" : "Apply to sources"}
          </button>
          <button type="button" disabled={busy} onClick={dismiss}>
            Cancel
          </button>
        </div>
      </section>
    </>
  );
}

function ExcludedList(props: {
  excluded: readonly { id: string; name: string; reason: string }[];
}) {
  return (
    <>
      <p class="batch-panel__sub">Not included</p>
      <ul class="batch-panel__list" data-testid="batch-excluded">
        {props.excluded.map((x) => (
          <li>
            <strong>{x.name}</strong>
            <span class="batch-panel__reason">{x.reason}</span>
          </li>
        ))}
      </ul>
    </>
  );
}
