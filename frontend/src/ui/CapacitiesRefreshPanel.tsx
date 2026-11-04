/* CapacitiesRefreshPanel — the one explicit "Refresh now" control for the
   Settings hub.

   Contract this surface keeps:
   - Refresh now is ONE explicit action: a token-guarded
     POST /capacities/refresh/start with mode "refresh" and scope "all". A
     full-scope run already re-lists every mapped structure, so there is no
     separate Rescan control and no hint claiming one re-lists more than the
     other. Nothing here runs on mount except a tokenless status read.
   - Status is truthful in plain words: an unconfigured source disables the
     action and says so; a running job shows "Reading 176 of 227", or
     "Waiting for Capacities to allow more requests (about 40 s) · read 176 of
     227" while a rate-limit wait counts down; a finished job says when it was
     last refreshed, what it did, and what it read or reused; a snapshot
     summary distinguishes "no full refresh has finished yet" from a real
     installed snapshot. Partial/stale warnings are rendered verbatim, never
     smoothed into success. The raw phase/outcome/generation values live in a
     "Technical details" disclosure closed by default.
   - Polling runs ONLY while a job is active and the panel is mounted/active;
     the interval is cleared on unmount, on deactivation, and as soon as the
     job reaches a terminal phase. The countdown tick exists only while a
     wait is visible and is cleared the same way. Cancel is offered while a
     job runs, including while it waits.
   - A 409 from start (single-flight) is surfaced as "a job is already
     running", never retried automatically.
   - A stored schema-change notice rides the SAME status read as ONE banner
     above the job's own warnings: 'warn' is an alert, 'info' a quieter
     notice. Its sentence is rendered verbatim (the panel adds no names or
     counts), nothing is shown once it is acknowledged, and the notice's own
     "Got it" button posts its to_hash and then re-reads the status — a stale
     409 means a newer notice exists, so it re-reads without reporting a
     failure. */

import { useEffect, useRef, useState } from "preact/hooks";
import { useApp } from "./context";
import { useCapacitiesRefreshTick } from "./useCapacitiesRefreshJob";
import { capacitiesSchemaChangeAckStaleOf } from "../adapters/api";
import type { CapacitiesRefreshMode, CapacitiesRefreshStatus } from "../model/types";

const TERMINAL_PHASES = new Set(["complete", "failed", "cancelled", "interrupted"]);
const POLL_MS = 1200;

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isRunning(status: CapacitiesRefreshStatus | null): boolean {
  if (!status || !status.job || status.phase === null) return false;
  return !TERMINAL_PHASES.has(status.phase);
}

function formatTimestamp(value: number | null): string {
  if (value === null) return "unknown";
  // Epoch seconds from the server; render a stable local time.
  const date = new Date(value * 1000);
  if (Number.isNaN(date.getTime())) return "unknown";
  return date.toLocaleString();
}

/** Plain "8:12 AM" for today, a full local date and time for any other day. */
function formatWhen(value: number | null): string {
  if (value === null) return "unknown";
  const date = new Date(value * 1000);
  if (Number.isNaN(date.getTime())) return "unknown";
  const time = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return date.toDateString() === new Date().toDateString() ? `${time} today` : date.toLocaleString();
}

function outcomeText(status: CapacitiesRefreshStatus): string {
  const outcome = status.outcome;
  if (outcome === "published") {
    // Warnings mean the run finished without covering everything; saying
    // "Up to date" there would smooth a partial read into success.
    return status.warnings.length > 0 ? "Refreshed, with warnings." : "Up to date.";
  }
  if (outcome === "cancelled") return "Stopped early. What was read was kept; your plan still uses the last full copy.";
  if (outcome === "interrupted") return "Interrupted before finishing; your plan still uses the last full copy.";
  if (outcome === "staleConfiguration") return "Your Capacities setup changed during the refresh, so nothing was updated.";
  if (outcome === "noCapacities") return "Capacities returned no items, so nothing was updated.";
  if (status.phase === "failed") return "The refresh failed.";
  return "No result was reported.";
}

/** What the last finished run did and when, in plain words. ``reused`` is
    derived as listed minus read, because the job document does not carry a
    reuse counter; it never goes negative. */
function runSummaryText(status: CapacitiesRefreshStatus): string | null {
  const finishedAt = status.job?.finishedAt ?? null;
  if (finishedAt === null) return null;
  const reused = Math.max(0, status.progress.listed - status.progress.read);
  return `Last refreshed ${formatWhen(finishedAt)}. ${outcomeText(status)} Read ${status.progress.read} items, reused ${reused}.`;
}

/** One plain sentence for a running job; a rate-limit wait counts down. */
function plainProgressText(status: CapacitiesRefreshStatus | null, nowMs: number): string | null {
  if (!isRunning(status) || !status) return null;
  const { read, toRead, waiting } = status.progress;
  const counted = toRead === null ? `${read} so far` : `${read} of ${toRead}`;
  if (waiting && waiting.resumeAt !== null) {
    const remainingSeconds = Math.max(0, Math.ceil(waiting.resumeAt - nowMs / 1000));
    return `Waiting for Capacities to allow more requests (about ${remainingSeconds} s) · read ${counted}`;
  }
  if (waiting) return `Waiting for Capacities to allow more requests · read ${counted}`;
  return `Reading ${counted}`;
}

/** The headline state before any run exists. A finished run is already summed
    up by ``runSummaryText``, so this returns null then rather than repeating
    (or contradicting) the same outcome on the next line. */
function plainStatusLine(status: CapacitiesRefreshStatus): string | null {
  if (status.phase !== null || status.job !== null) return null;
  return status.snapshot?.present ? "Up to date." : "Not refreshed yet.";
}

export function CapacitiesRefreshPanel({
  active,
  extraNotes = [],
}: {
  active: boolean;
  /** Hub-derived advisory lines (e.g. mapped types missing from the refresh), listed after the refresh warnings. */
  extraNotes?: readonly string[];
}) {
  const { controller } = useApp();
  const [status, setStatus] = useState<CapacitiesRefreshStatus | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [ackError, setAckError] = useState<string | null>(null);
  const [ackBusy, setAckBusy] = useState(false);
  const [busy, setBusy] = useState<"starting" | "cancelling" | null>(null);
  const mounted = useRef(true);

  const load = async () => {
    try {
      const next = await controller.loadCapacitiesRefreshStatus();
      if (!mounted.current) return;
      setStatus(next);
      setPhase("ready");
      setError(null);
    } catch (e) {
      if (!mounted.current) return;
      setPhase("error");
      setError(messageOf(e));
    }
  };

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!active) return;
    void load();
  }, [active, controller]);

  const running = isRunning(status);
  const waiting = status?.progress.waiting ?? null;
  const nowMs = useCapacitiesRefreshTick(active && running && waiting !== null);
  useEffect(() => {
    if (!active || !running) return;
    const id = setInterval(() => {
      void load();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [active, running, controller]);

  if (!active) return null;

  const progressText = running ? plainProgressText(status, nowMs) : null;
  const summaryText = running || !status ? null : runSummaryText(status);
  // A wait is a running state; spell it out so Cancel stays offered there.
  const statusLine = status ? plainStatusLine(status) : null;
  const cancellable = running || waiting !== null;

  const start = async (mode: CapacitiesRefreshMode) => {
    setBusy("starting");
    setActionError(null);
    try {
      const next = await controller.startCapacitiesRefresh(mode, "all");
      if (!mounted.current) return;
      setStatus(next);
      setPhase("ready");
    } catch (e) {
      if (!mounted.current) return;
      setActionError(messageOf(e));
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const cancel = async () => {
    setBusy("cancelling");
    setActionError(null);
    try {
      const next = await controller.cancelCapacitiesRefresh();
      if (!mounted.current) return;
      setStatus(next);
      setPhase("ready");
    } catch (e) {
      if (!mounted.current) return;
      setActionError(messageOf(e));
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const configured = status?.configured ?? false;
  const snapshot = status?.snapshot ?? null;
  // ONE banner, only while the notice is unacknowledged. The sentence is the
  // server's; this panel adds no names and no counts of its own.
  const schemaChange = status?.schemaChange ?? null;
  const schemaNotice = schemaChange && !schemaChange.acknowledged ? schemaChange : null;
  const attentionCount =
    (status?.warnings.length ?? 0) + extraNotes.length + (status && schemaNotice ? 1 : 0);

  const acknowledge = async (toHash: string) => {
    setAckBusy(true);
    setAckError(null);
    try {
      await controller.acknowledgeCapacitiesSchemaChange(toHash);
      // The ack route answers no status, so the banner clears from a re-read
      // of the server's own record rather than from an optimistic flip.
      await load();
    } catch (e) {
      if (!mounted.current) return;
      // A stale hash means a newer notice already replaced this one: read the
      // current truth and say nothing about a failure.
      if (capacitiesSchemaChangeAckStaleOf(e)) {
        await load();
        return;
      }
      setAckError(messageOf(e));
    } finally {
      if (mounted.current) setAckBusy(false);
    }
  };

  return (
    <section
      class="setup-section capacities-settings__section"
      aria-labelledby="capacities-sec-refresh"
      data-settings-section="refresh"
    >
      <div class="setup-section__head">
        <h3 id="capacities-sec-refresh">Capacities refresh</h3>
      </div>
      <div class="setup-section__body">
        <p class="capacities-settings__hint">
          Refresh now checks Capacities for changes and updates your plan. It is one
          careful, paced pass and never runs on its own.
        </p>

        {phase === "loading" && (
          <p class="capacities-settings__state" role="status">Checking the refresh status…</p>
        )}
        {phase === "error" && (
          <section class="capacities-settings__error" role="alert">
            <strong>Could not check the refresh status</strong>
            <span>{error}</span>
            <button class="btn" onClick={() => void load()}>Try again</button>
          </section>
        )}

        {status && phase !== "loading" && !configured && (
          <p class="capacities-settings__empty" role="status">
            Capacities is not set up yet, so refresh is unavailable. Save your Capacities setup first.
          </p>
        )}

        {status && configured && (
          <>
            <div class="editor__actions setup__actions">
              <button
                class="btn btn--primary"
                disabled={busy !== null || running}
                onClick={() => void start("refresh")}
              >
                {busy === "starting" ? "Starting…" : "Refresh now"}
              </button>
              {cancellable && (
                <button class="btn" disabled={busy !== null} onClick={() => void cancel()}>
                  {busy === "cancelling" ? "Stopping…" : "Stop refresh"}
                </button>
              )}
              {!running && statusLine && <p class="capacities-settings__state">{statusLine}</p>}
            </div>

            {actionError && (
              <div class="capacities-settings__save-error" role="alert">
                <span>{actionError}</span>
              </div>
            )}

            {progressText && (
              <p class="capacities-settings__state" role="status">
                {progressText}
              </p>
            )}
            {summaryText && <p class="capacities-settings__hint">{summaryText}</p>}

            {attentionCount > 0 && (
              <section class="settings-hub__attention" aria-label="Needs attention">
                <p class="settings-hub__attention-head">
                  <strong>Needs attention</strong>
                  <span class="chip chip--warn" data-attention-count>{attentionCount}</span>
                </p>
              {schemaNotice && (
                <>
                  {schemaNotice.severity === "warn" ? (
                    <section class="capacities-settings__error" role="alert">
                      <span>{schemaNotice.summary}</span>
                      <button
                        class="btn"
                        disabled={ackBusy}
                        aria-label="Dismiss the Capacities change notice"
                        onClick={() => void acknowledge(schemaNotice.toHash)}
                      >
                        {ackBusy ? "Saving…" : "Got it"}
                      </button>
                    </section>
                  ) : (
                    <div class="capacities-rule__notice" role="status">
                      <span>{schemaNotice.summary}</span>{" "}
                      <button
                        class="btn"
                        disabled={ackBusy}
                        aria-label="Dismiss the Capacities change notice"
                        onClick={() => void acknowledge(schemaNotice.toHash)}
                      >
                        {ackBusy ? "Saving…" : "Got it"}
                      </button>
                    </div>
                  )}
                  {ackError && (
                    <div class="capacities-settings__save-error" role="alert">
                      <span>{ackError}</span>
                    </div>
                  )}
                </>
              )}

                {(status.warnings.length > 0 || extraNotes.length > 0) && (
                  <>
                    {status.warnings.length > 0 && (
                      <p class="capacities-settings__hint">
                        Shown exactly as reported. A refresh that finished with warnings may not have covered everything.
                      </p>
                    )}
                    <ul class="settings-hub__rows settings-hub__attention-list">
                      {status.warnings.map((warning) => (
                        <li key={warning}>
                          <code>{warning}</code>
                        </li>
                      ))}
                      {extraNotes.map((note) => (
                        <li key={note}>{note}</li>
                      ))}
                    </ul>
                  </>
                )}
              </section>
            )}

            <div class="capacities-snapshot">
              {snapshot && snapshot.present ? (
                <p class="capacities-settings__hint">
                  Last full refresh: {formatWhen(snapshot.installedAt)} · {snapshot.memberCount} items
                </p>
              ) : (
                <p class="capacities-settings__empty">No full refresh has finished yet.</p>
              )}
            </div>

            <details class="settings-hub__details">
              <summary>Technical details</summary>
              <dl class="capacities-status__grid">
                <div class="capacities-status__cell">
                  <dt>Job</dt>
                  <dd>{status.job ? `${status.job.mode ?? "job"} · ${status.job.scope ?? "all"}` : "None yet"}</dd>
                </div>
                <div class="capacities-status__cell">
                  <dt>Phase</dt>
                  <dd>{status.phase ?? "idle"}</dd>
                </div>
                <div class="capacities-status__cell">
                  <dt>Outcome</dt>
                  <dd>{status.outcome ?? "—"}</dd>
                </div>
                <div class="capacities-status__cell">
                  <dt>Progress</dt>
                  <dd>{status.progress.listed} listed · {status.progress.read} read</dd>
                </div>
              </dl>
              {snapshot && snapshot.present && (
                <p class="capacities-settings__hint">
                  Last complete generation {snapshot.generation} · {snapshot.memberCount} members · installed {formatTimestamp(snapshot.installedAt)}
                </p>
              )}
            </details>
          </>
        )}
      </div>
    </section>
  );
}
