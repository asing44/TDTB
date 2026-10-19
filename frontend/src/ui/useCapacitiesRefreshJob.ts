/* useCapacitiesRefreshJob — the Capacities refresh-job lifecycle backing the
   cockpit header's healthy-state Refresh action.

   Contract:
   - One explicit action, "Refresh sources": a single token-guarded
     POST /capacities/refresh/start with mode "refresh" and scope "all". The
     server keeps its "rescan" mode for old job documents and for non-UI
     callers; this surface does not offer it. The only read on mount is the
     tokenless status read.
   - The hook watches ONE shared job whoever started it: the controller's
     start signal attaches the bounded poll when the Connections panel starts
     a run, and the mount status read covers a run already in flight.
   - Status is truthful: a running job shows its phase; a terminal job shows
     its outcome.
   - Polling runs ONLY while a job is active, and the interval is cleared on
     unmount, on deactivation, and as soon as the job reaches a terminal phase.
   - A 409 from start (single-flight) is "a job is already running": the hook
     adopts the running job through the status read instead of surfacing an
     error flood, and still reports its terminal phase.
   - A terminal phase reports once through `onTerminal` so the caller can
     reload the snapshot the job just installed. A terminal status merely
     observed on mount belongs to an older job and never fires it. */

import { useEffect, useRef, useState } from "preact/hooks";
import { useApp } from "./context";
import { capacitiesRefreshBusyOf } from "../adapters/api";
import type { CapacitiesRefreshMode, CapacitiesRefreshStatus } from "../model/types";

export const CAPACITIES_REFRESH_TERMINAL_PHASES = new Set([
  "complete",
  "failed",
  "cancelled",
  "interrupted",
]);
export const CAPACITIES_REFRESH_POLL_MS = 1200;
/** One-second repaint cadence, so a rate-limit countdown moves without
    re-reading the status document. */
export const CAPACITIES_REFRESH_TICK_MS = 1000;

export function capacitiesRefreshJobRunning(status: CapacitiesRefreshStatus | null): boolean {
  if (!status || !status.job || status.phase === null) return false;
  return !CAPACITIES_REFRESH_TERMINAL_PHASES.has(status.phase);
}

/** The one-line progress sentence for a live run (R3, R4).

    Pure: the caller supplies the wall clock, so the countdown is
    deterministic under fake timers. Returns null when no run is live, which
    is what lets a caller fall back to its own idle/terminal rendering.

    - waiting: "Capacities busy, resuming in 40s · 176/227", counted down
      from the server's absolute ``resumeAt`` (never below 0).
    - reading: "Reading 176/227", or "Reading 176 read" when the server did
      not report a read target (older job documents). */
export function refreshProgressText(
  status: CapacitiesRefreshStatus | null,
  nowMs: number,
): string | null {
  if (!capacitiesRefreshJobRunning(status) || !status) return null;
  const { read, toRead, waiting } = status.progress;
  const counted = toRead === null ? `${read} read` : `${read}/${toRead}`;
  if (waiting && waiting.resumeAt !== null) {
    const remainingSeconds = Math.max(0, Math.ceil(waiting.resumeAt - nowMs / 1000));
    return `Capacities busy, resuming in ${remainingSeconds}s · ${counted}`;
  }
  if (waiting) return `Capacities busy · ${counted}`;
  return `Reading ${counted}`;
}

/** A once-per-second clock for a visible countdown. The interval exists only
    while ``active``, and is cleared on unmount or when the wait ends. */
export function useCapacitiesRefreshTick(active: boolean): number {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    // Repaint immediately so a just-started wait shows its first second.
    setNowMs(Date.now());
    const id = setInterval(() => setNowMs(Date.now()), CAPACITIES_REFRESH_TICK_MS);
    return () => clearInterval(id);
  }, [active]);
  return nowMs;
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export interface CapacitiesRefreshJob {
  status: CapacitiesRefreshStatus | null;
  phase: "loading" | "ready" | "error";
  error: string | null;
  actionError: string | null;
  running: boolean;
  /** Wall clock that advances once a second while a rate-limit wait is
      visible, so callers can render ``refreshProgressText``. */
  nowMs: number;
  busy: "starting" | "cancelling" | null;
  start: (mode: CapacitiesRefreshMode) => Promise<void>;
  cancel: () => Promise<void>;
  reload: () => Promise<void>;
}

export function useCapacitiesRefreshJob(
  active: boolean,
  onTerminal?: (status: CapacitiesRefreshStatus) => void,
): CapacitiesRefreshJob {
  const { controller } = useApp();
  const [status, setStatus] = useState<CapacitiesRefreshStatus | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"starting" | "cancelling" | null>(null);
  const mounted = useRef(true);
  // Monotonic read epoch: a start, adopt, cancel, or newer poll invalidates
  // any status read already in flight, so a stale GET (for example the
  // mount-time status read resolving after a start) can never overwrite the
  // newer truth and stop the poll.
  const readEpoch = useRef(0);
  // Set while this hook is watching a live run, from any surface. The
  // terminal report fires once per tracked run; a terminal status merely
  // observed on mount — or already held when the user clicks — never sets
  // this and never fires. State, not a ref: an effect flushed for a
  // pre-click render must not read a later click's tracking flag and report
  // that stale status as a completion.
  const [tracking, setTracking] = useState(false);
  const onTerminalRef = useRef(onTerminal);
  onTerminalRef.current = onTerminal;

  const load = async () => {
    const epoch = ++readEpoch.current;
    try {
      const next = await controller.loadCapacitiesRefreshStatus();
      if (!mounted.current || epoch !== readEpoch.current) return;
      setStatus(next);
      setPhase("ready");
      setError(null);
    } catch (e) {
      if (!mounted.current || epoch !== readEpoch.current) return;
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

  const running = capacitiesRefreshJobRunning(status);
  const waiting = status?.progress.waiting ?? null;
  const nowMs = useCapacitiesRefreshTick(active && running && waiting !== null);
  useEffect(() => {
    if (!active || !running) return;
    const id = setInterval(() => {
      void load();
    }, CAPACITIES_REFRESH_POLL_MS);
    return () => clearInterval(id);
  }, [active, running, controller]);

  // A refresh started by ANY surface goes through the same controller
  // entry, so its start signal attaches this hook's bounded poll to a run the
  // header did not start (for example the Connections panel). The read is
  // epoch-guarded like every other status read.
  useEffect(() => {
    if (!active) return;
    return controller.subscribeCapacitiesRefreshStart(() => {
      void load();
    });
  }, [active, controller]);

  // Any live run this hook is watching — its own, one adopted from a 409, one
  // the panel started, or one already running when the page loaded — is
  // tracked so its terminal phase reloads the snapshot exactly once.
  useEffect(() => {
    if (active && running) setTracking(true);
  }, [active, running]);

  useEffect(() => {
    if (!tracking || running || !status) return;
    setTracking(false);
    onTerminalRef.current?.(status);
  }, [tracking, status, running]);

  const start = async (mode: CapacitiesRefreshMode) => {
    setBusy("starting");
    setActionError(null);
    try {
      const next = await controller.startCapacitiesRefresh(mode, "all");
      if (!mounted.current) return;
      readEpoch.current += 1;
      setStatus(next);
      setPhase("ready");
      setTracking(true);
    } catch (e) {
      if (!mounted.current) return;
      // A running job is not a failure: adopt it through the status read so
      // the surface keeps showing the truth (and never floods errors).
      if (capacitiesRefreshBusyOf(e) !== null) {
        // The running job is state, not a failure, but the adopt read can
        // still fail on its own: report it without rejecting `start` (the
        // Header fires the promise with `void`, so a rejection would be an
        // unhandled rejection rather than a visible error).
        try {
          const adopted = await controller.loadCapacitiesRefreshStatus();
          if (!mounted.current) return;
          readEpoch.current += 1;
          setStatus(adopted);
          setPhase("ready");
          setTracking(true);
        } catch (adoptError) {
          if (!mounted.current) return;
          setActionError(messageOf(adoptError));
        }
        return;
      }
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
      readEpoch.current += 1;
      setStatus(next);
      setPhase("ready");
    } catch (e) {
      if (!mounted.current) return;
      setActionError(messageOf(e));
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  return { status, phase, error, actionError, running, nowMs, busy, start, cancel, reload: load };
}
