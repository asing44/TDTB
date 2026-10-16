/* CalendarDrift — issue asing44/TDTB#19 (part B): a compact, read-only panel
   of how the live Calendar drifted from what TDTB committed today. It is
   purely informational: the only network call is GET /calendar-drift through
   the adapter, and nothing here writes, adopts, repairs, or re-commits.

   Triggers live here: once when the day shows a committed state (the same
   `commitPhase` signal the Results surface uses), the explicit Check calendar
   button, and window focus (throttled to one call per 30s in the controller).
   The panel hides itself when there is nothing to show. */

import { useEffect, useRef } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { display12h } from "../model/time";
import type {
  CalendarDriftAdded,
  CalendarDriftInterval,
  CalendarDriftMissing,
  CalendarDriftMoved,
} from "../model/types";

/** Naive-local ISO ("2026-10-16T15:00:00") to "HH:MM" for display12h; null
    when there is no usable clock value. */
function isoClock(iso: string | null): string | null {
  if (!iso) return null;
  const time = iso.includes("T") ? iso.slice(iso.indexOf("T") + 1) : iso;
  const hhmm = time.slice(0, 5);
  return /^\d{2}:\d{2}$/.test(hhmm) ? hhmm : null;
}

/** One interval via the shared 12-hour formatter: "3 PM–3:30 PM". */
function range(interval: CalendarDriftInterval): string {
  return `${display12h(isoClock(interval.start))}–${display12h(isoClock(interval.end))}`;
}

function MovedRow({ row }: { row: CalendarDriftMoved }) {
  return (
    <li class="alerts__item alerts__item--warning">
      <span aria-hidden="true">⚠</span>
      <span>
        <strong>{row.name}</strong> moved: {range(row.committed)} → {range(row.live)}
      </span>
    </li>
  );
}

function MissingRow({ row }: { row: CalendarDriftMissing }) {
  return (
    <li class="alerts__item alerts__item--warning">
      <span aria-hidden="true">⚠</span>
      <span>
        <strong>{row.name}</strong> missing from Calendar
        {row.committed ? ` (was ${range(row.committed)})` : ""}
      </span>
    </li>
  );
}

function AddedRow({ row }: { row: CalendarDriftAdded }) {
  return (
    <li class="alerts__item alerts__item--warning">
      <span aria-hidden="true">⚠</span>
      <span>
        <strong>{row.name}</strong> added: {range(row.live)}
      </span>
    </li>
  );
}

/** The backend's reason may already carry the "calendar unavailable:" prefix
    (it comes from the same route); never say it twice. */
function unavailableLine(reason: string | null): string {
  const text = (reason ?? "").trim();
  if (!text) return "Calendar unavailable";
  return /^calendar unavailable/i.test(text) ? text : `Calendar unavailable: ${text}`;
}

export function CalendarDrift() {
  const { controller } = useApp();
  const s = useAppState();
  const autoChecked = useRef(false);

  // Once when the day shows a committed state — the same signal the Results
  // surface uses: commitPhase done / partial / failed (commitReport set).
  useEffect(() => {
    if (autoChecked.current) return;
    if (s.commitPhase === "idle") return;
    autoChecked.current = true;
    void controller.checkCalendarDrift();
  }, [controller, s.commitPhase]);

  // On window focus, throttled inside the controller (at most one per 30s).
  useEffect(() => {
    const onFocus = () => void controller.checkCalendarDrift({ fromFocus: true });
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [controller]);

  const checkButton = (
    <button
      class="btn drawer-empty__build"
      onClick={() => void controller.checkCalendarDrift()}
    >
      Check calendar
    </button>
  );

  const report = s.calendarDrift;
  const error = s.calendarDriftError;

  if (error) {
    return (
      <div class="calendar-drift calendar-drift--quiet" role="status">
        <p class="calendar-drift__quiet">Calendar check failed: {error}</p>
        {checkButton}
      </div>
    );
  }
  if (!report || !report.hasCommit) return null;

  if (report.status === "calendar-unavailable") {
    return (
      <div class="calendar-drift calendar-drift--quiet" role="status">
        <p class="calendar-drift__quiet">{unavailableLine(report.reason)}</p>
        {checkButton}
      </div>
    );
  }

  const drift = report.moved.length + report.missing.length + report.added.length;
  if (drift === 0) {
    return (
      <div class="calendar-drift calendar-drift--quiet" role="status">
        <p class="calendar-drift__quiet">Calendar matches today's commit</p>
        {checkButton}
      </div>
    );
  }

  return (
    <div class="calendar-drift">
      <div class="calendar-drift__head">
        <h3 class="calendar-drift__title">Calendar drift</h3>
        {checkButton}
      </div>
      {report.moved.length > 0 && (
        <>
          <h4 class="calendar-drift__group">Moved</h4>
          <ul class="alerts__list" aria-label="Moved">
            {report.moved.map((row) => (
              <MovedRow key={row.id} row={row} />
            ))}
          </ul>
        </>
      )}
      {report.missing.length > 0 && (
        <>
          <h4 class="calendar-drift__group">Missing</h4>
          <ul class="alerts__list" aria-label="Missing">
            {report.missing.map((row) => (
              <MissingRow key={row.id} row={row} />
            ))}
          </ul>
        </>
      )}
      {report.added.length > 0 && (
        <>
          <h4 class="calendar-drift__group">Added</h4>
          <ul class="alerts__list" aria-label="Added">
            {report.added.map((row) => (
              <AddedRow key={row.id} row={row} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
