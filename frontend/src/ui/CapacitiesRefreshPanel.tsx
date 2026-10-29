/* CapacitiesRefreshPanel — the one explicit "Refresh sources" control for
   the Connections surface.

   Contract this surface keeps:
   - Refresh sources is ONE explicit action: a token-guarded
     POST /capacities/refresh/start with mode "refresh" and scope "all". A
     full-scope run already re-lists every mapped structure, so there is no
     separate Rescan control and no hint claiming one re-lists more than the
     other. Nothing here runs on mount except a tokenless status read.
   - Status is truthful: an unconfigured source disables the action and says
     so; a running job shows its phase, live progress ("Reading 176/227", or
     "Capacities busy, resuming in 40s · 176/227" while a rate-limit wait
     counts down) and warnings; a terminal job shows when it finished, what
     it did, and what it read or reused; a snapshot summary distinguishes "no
     complete generation yet" from a real installed generation. Partial/stale
     warnings are rendered verbatim, never smoothed into success.
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
     counts), nothing is shown once it is acknowledged, and Acknowledge posts
     the notice's own to_hash and then re-reads the status — a stale 409 means
     a newer notice exists, so it re-reads without reporting a failure. */

import { useEffect, useRef, useState } from "preact/hooks";
import { useApp } from "./context";
import { refreshProgressText, useCapacitiesRefreshTick } from "./useCapacitiesRefreshJob";
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

function outcomeText(status: CapacitiesRefreshStatus): string {
  const outcome = status.outcome ?? "—";
  if (outcome === "published") return "Published a new complete generation.";
  if (outcome === "cancelled") return "Cancelled; partial reads were kept and no new generation was installed.";
  if (outcome === "interrupted") return "Interrupted before completion; no new generation was installed.";
  if (outcome === "staleConfiguration") return "The Capacities configuration changed mid-job; nothing was published.";
  if (outcome === "noCapacities") return "No Capacities rows were produced; nothing was published.";
  return outcome;
}

/** R7: what the last terminal run did and when. ``reused`` is derived as
    listed minus read, because the job document does not carry a reuse
    counter; it never goes negative. */
function runSummaryText(status: CapacitiesRefreshStatus): string | null {
  const finishedAt = status.job?.finishedAt ?? null;
  if (finishedAt === null) return null;
  const reused = Math.max(0, status.progress.listed - status.progress.read);
  return `Finished ${formatTimestamp(finishedAt)}: ${outcomeText(status)}; ${status.progress.read} read, ${reused} reused`;
}

export function CapacitiesRefreshPanel({ active }: { active: boolean }) {
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

  const progressText = running ? refreshProgressText(status, nowMs) : null;
  const summaryText = running || !status ? null : runSummaryText(status);
  // A wait is a running state; spell it out so Cancel stays offered there.
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
        <h3 id="capacities-sec-refresh">Source refresh</h3>
        {status?.phase && (
          <span class="capacities-settings__revision">Phase {status.phase}</span>
        )}
      </div>
      <div class="setup-section__body">
        <p class="capacities-settings__hint">
          Refresh sources re-reads the Capacities source: one explicit, paced run that
          also re-lists every mapped structure. It never runs on its own.
        </p>

        {phase === "loading" && (
          <p class="capacities-settings__state" role="status">Loading refresh status…</p>
        )}
        {phase === "error" && (
          <section class="capacities-settings__error" role="alert">
            <strong>Refresh status could not be read</strong>
            <span>{error}</span>
            <button class="btn" onClick={() => void load()}>Reload status</button>
          </section>
        )}

        {status && phase !== "loading" && !configured && (
          <p class="capacities-settings__empty" role="status">
            No Capacities source is configured; refresh is unavailable. Save a source mapping first.
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
                {busy === "starting" ? "Starting…" : "Refresh sources"}
              </button>
              {cancellable && (
                <button class="btn" disabled={busy !== null} onClick={() => void cancel()}>
                  {busy === "cancelling" ? "Cancelling…" : "Cancel refresh"}
                </button>
              )}
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
                <dd>{status.outcome ? outcomeText(status) : "—"}</dd>
              </div>
              <div class="capacities-status__cell">
                <dt>Progress</dt>
                <dd>{status.progress.listed} listed · {status.progress.read} read</dd>
              </div>
            </dl>

            {schemaNotice && (
              <>
                {schemaNotice.severity === "warn" ? (
                  <section class="capacities-settings__error" role="alert">
                    <span>{schemaNotice.summary}</span>
                    <button
                      class="btn"
                      disabled={ackBusy}
                      aria-label="Acknowledge Capacities change"
                      onClick={() => void acknowledge(schemaNotice.toHash)}
                    >
                      {ackBusy ? "Acknowledging…" : "Acknowledge"}
                    </button>
                  </section>
                ) : (
                  <div class="capacities-rule__notice" role="status">
                    <span>{schemaNotice.summary}</span>{" "}
                    <button
                      class="btn"
                      disabled={ackBusy}
                      aria-label="Acknowledge Capacities change"
                      onClick={() => void acknowledge(schemaNotice.toHash)}
                    >
                      {ackBusy ? "Acknowledging…" : "Acknowledge"}
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

            {status.warnings.length > 0 && (
              <div class="capacities-exclusion-list">
                <h4>Warnings</h4>
                <p class="capacities-settings__hint">
                  These are the job's own warnings, shown verbatim. A completed job with warnings did not mean complete coverage.
                </p>
                {status.warnings.map((warning) => (
                  <div class="capacities-exclusion-row" key={warning}>
                    <code>{warning}</code>
                  </div>
                ))}
              </div>
            )}

            <div class="capacities-snapshot">
              {snapshot && snapshot.present ? (
                <p class="capacities-settings__hint">
                  Last complete generation {snapshot.generation} · {snapshot.memberCount} members · installed {formatTimestamp(snapshot.installedAt)}
                </p>
              ) : (
                <p class="capacities-settings__empty">No complete generation is installed yet.</p>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
