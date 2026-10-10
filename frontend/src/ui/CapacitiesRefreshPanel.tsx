/* CapacitiesRefreshPanel — explicit Refresh / Rescan controls for the
   Connections surface.

   Contract this surface keeps:
   - Refresh and Rescan are two DISTINCT explicit actions: each is one
     token-guarded POST /capacities/refresh/start with its own mode. Nothing
     here runs on mount except a tokenless status read.
   - Status is truthful: an unconfigured source disables both actions and says
     so; a running job shows its phase/progress/warnings; a terminal job shows
     its outcome; a snapshot summary distinguishes "no complete generation yet"
     from a real installed generation. Partial/stale warnings are rendered
     verbatim, never smoothed into success.
   - Polling runs ONLY while a job is active and the panel is mounted/active;
     the interval is cleared on unmount, on deactivation, and as soon as the
     job reaches a terminal phase. Cancel is offered only while a job runs.
   - A 409 from start (single-flight) is surfaced as "a job is already
     running", never retried automatically. */

import { useEffect, useRef, useState } from "preact/hooks";
import { useApp } from "./context";
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

export function CapacitiesRefreshPanel({ active }: { active: boolean }) {
  const { controller } = useApp();
  const [status, setStatus] = useState<CapacitiesRefreshStatus | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
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
  useEffect(() => {
    if (!active || !running) return;
    const id = setInterval(() => {
      void load();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [active, running, controller]);

  if (!active) return null;

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
          Refresh re-reads the Capacities source; Rescan additionally re-lists structures.
          Both are explicit, paced, and never run on their own.
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
              <button
                class="btn"
                disabled={busy !== null || running}
                onClick={() => void start("rescan")}
              >
                Rescan structures
              </button>
              {running && (
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
