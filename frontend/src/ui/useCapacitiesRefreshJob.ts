/* useCapacitiesRefreshJob — the Capacities refresh-job lifecycle backing the
   cockpit header's healthy-state Refresh action. The Connections panel keeps
   its own inline copy of this lifecycle; this hook does not change it.

   Contract:
   - Refresh and Rescan are two DISTINCT explicit actions: each is one
     token-guarded POST /capacities/refresh/start with its own mode. The only
     read on mount is the tokenless status read.
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

export function capacitiesRefreshJobRunning(status: CapacitiesRefreshStatus | null): boolean {
  if (!status || !status.job || status.phase === null) return false;
  return !CAPACITIES_REFRESH_TERMINAL_PHASES.has(status.phase);
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
  // Set only once a start attempt has produced a status to track. The
  // terminal report fires once per tracked run; a terminal status merely
  // observed on mount — or already held when the user clicks — is never
  // reported. State, not a ref: an effect flushed for a pre-click render
  // must not read a later click's tracking flag and report that stale
  // status as a completion.
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
  useEffect(() => {
    if (!active || !running) return;
    const id = setInterval(() => {
      void load();
    }, CAPACITIES_REFRESH_POLL_MS);
    return () => clearInterval(id);
  }, [active, running, controller]);

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

  return { status, phase, error, actionError, running, busy, start, cancel, reload: load };
}
