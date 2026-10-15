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

import { useAppState } from "./context";
import { DayStrip, type StripAnchor, type StripCalendarEvent, type StripSession } from "./day-setup/DayStrip";
import { effectiveAnchoredBlocks, selectDayFrame } from "../store/store";
import { displayAnchored } from "../model/calendarEvents";
import { anchorFrameStart, anchoredBlocks, anchoredOverrideOf } from "../model/anchored";
import { explicitMintCapMinutes, initialMintSessionIds } from "../model/mint";
import { calendarWalls } from "../model/overflow";

export function AlreadyPlaced() {
  const s = useAppState();
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
    }));

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
      />
    </section>
  );
}
