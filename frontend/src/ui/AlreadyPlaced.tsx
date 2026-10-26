/* AlreadyPlaced — Planning 2a, Plan tasks screen. The same full lane timeline
   the Set up day screen shows (Calendar / Mint / Anchors), drawn READ-ONLY:
   no selection, no detail row, no popover, no drag. It answers one question
   before the task table does — what is already on the day — so it reuses
   DayStrip's lanes and styles instead of a second timeline implementation.

   The card never shows tasks or Capacities items: after commit those are
   placed into the calendar, so this lane set is commitments and anchors only.
   The right-aligned note says exactly that ("Tasks are placed after you
   commit.").

   Pure projection: every value is read from the store with the same selectors
   the Set up screen uses (effectiveAnchoredBlocks, calendarWalls, the mint
   session helpers), so the two screens can never disagree about the day. */

import { useApp, useAppState } from "./context";
import "./strip-resize.css";
import { DayStrip, type StripAnchor, type StripCalendarEvent, type StripSession } from "./day-setup/DayStrip";
import { effectiveAnchoredBlocks, selectDayFrame } from "../store/store";
import { displayAnchored } from "../model/calendarEvents";
import { anchorFrameStart, anchoredBlocks, anchoredOverrideOf } from "../model/anchored";
import { explicitMintCapMinutes, initialMintSessionIds } from "../model/mint";
import { calendarWalls } from "../model/overflow";

/** Counted duration is whole 30-minute blocks (1–12), exactly the grid the
    Calendar impact stepper saves on: a resize can only land on that grid. */
export const RESIZE_BLOCK_MIN = 30;
export const RESIZE_MAX_BLOCKS = 12;

export function AlreadyPlaced() {
  const s = useAppState();
  const controller = useApp().controller;
  if (!s.inputs) return null;

  const { start: frameStart, end: frameEnd } = selectDayFrame(s)!;

  const effective = effectiveAnchoredBlocks(s);
  const walls = calendarWalls(effective);

  // Mint: the day's saved selection, drawn as placed blocks. With no sessions
  // (or none selected) the lane hatches "Off today" exactly as Set up does.
  const sessions = s.inputs.daySemantics.mintSessions ?? [];
  const selectedMintIds = initialMintSessionIds(
    sessions,
    s.daySetup.schedulable?.minting,
    explicitMintCapMinutes(s.daySetup, s.inputs.daySemantics.effectiveAllotmentMinutes),
    frameStart,
    walls,
  );
  const selectedSessions: StripSession[] = sessions
    .filter((session) => selectedMintIds.includes(session.id))
    .map((session) => ({
      id: session.id,
      name: session.name,
      start: session.start,
      end: session.end,
    }));

  const shown = displayAnchored(effective, { sessionCalendars: true });

  const anchors: StripAnchor[] = shown
    .filter((row) => row.kind !== "calendar" && row.start != null)
    .map((row) => {
      const override = anchoredOverrideOf(row, s.daySetup.anchored[row.id]);
      const on = override.on && !override.skipToday;
      const source = s.inputs!.anchored.find((a) => a.id === row.id);
      const blocks = anchoredBlocks(row, override);
      const placement = anchorFrameStart(row.kind, {
        start: override.time ?? (row.start as string),
        end: source?.end ?? null,
        blocks,
        frameStart,
        frameEnd,
      });
      const time = placement.start ?? (row.start as string);
      const outside = placement.afterFrame;
      const counted = on && !outside;
      return {
        id: row.id,
        name: row.name,
        start: time,
        window:
          (row.kind === "window" || row.kind === "template") &&
          source != null &&
          source.start !== null &&
          source.end !== null
            ? { start: source.start, end: source.end }
            : undefined,
        counted,
        reason: !on ? "skipped" : outside ? "outside frame" : null,
        blocks,
        // Movable requires a write path; the Plan screen has none.
        movable: false,
      };
    });

  const calendar: StripCalendarEvent[] = shown
    .filter(
      (row) =>
        row.kind === "calendar" &&
        row.start !== null &&
        row.durationMin > 0 &&
        (row.capacityClass ?? "fixed") !== "ignored",
    )
    .map((row) => ({
      id: row.id,
      name: row.name,
      start: row.start as string,
      durationMin: row.durationMin,
      counted: row.on && !row.skipToday,
      capacityClass: row.capacityClass,
      resizable: !s.runtimeBusy,
    }));

  // Same single path as the Calendar impact stepper: a today-only counted
  // duration through saveAnchoredOverride. Nothing is written to the calendar.
  const resizeEvent = (id: string, durationMin: number) => {
    const blocks = Math.min(
      RESIZE_MAX_BLOCKS,
      Math.max(1, Math.round(durationMin / RESIZE_BLOCK_MIN)),
    );
    const saved = s.daySetup.anchored[id];
    void controller.saveAnchoredOverride(id, {
      on: true,
      skipToday: false,
      time: null,
      // A local start edit survives the duration edit.
      ...(saved?.start != null
        ? {
            start: saved.start,
            ...(saved.calendarStartAtEdit != null
              ? { calendarStartAtEdit: saved.calendarStartAtEdit }
              : {}),
          }
        : {}),
      blocks,
    });
  };

  return (
    <section class="already-placed" aria-label="Already placed">
      <header class="already-placed__head">
        <h2>Already placed</h2>
        <p class="already-placed__note">Tasks are placed after you commit.</p>
      </header>
      <DayStrip
        readOnly
        frameStart={frameStart}
        frameEnd={frameEnd}
        sessions={selectedSessions}
        selectedIds={selectedMintIds}
        selectableIds={selectedMintIds}
        onSelectIds={() => {}}
        onToggleSession={() => {}}
        anchors={anchors}
        calendar={calendar}
        onSelectAnchor={() => {}}
        onResizeEvent={resizeEvent}
        resizeStepMin={RESIZE_BLOCK_MIN}
        resizeMaxMin={RESIZE_MAX_BLOCKS * RESIZE_BLOCK_MIN}
      />
    </section>
  );
}
