/* U12 day-frame strip (Planning 2a). The ruler is the day frame itself:
   x = (minutes - frameStart) / (frameEnd - frameStart). A 90px lane-label
   column carries Calendar / Mint / Anchors, hour ticks sit above the lanes,
   and there is no separate frame lane or above-lane caption row.

   Calendar events are read-only: the block sits on a 40px lane and its title
   is drawn INSIDE the block when it fits (ellipsized when it partly fits); a
   block under ~44px wide labels itself outside, to the right and flipped to the
   left when the lane's right edge has no room, or title-only when neither fits.
   The Mint lane (32px) is hatched with
   "Off today" when nothing is selected; otherwise dragging across slots
   selects them (Every interaction has a keyboard equivalent: focus a slot,
   Space toggles it, Arrow keys move between slots). Anchor blocks are 38px
   tall on a 96px lane with two sub-rows (a block takes its README-preferred
   row when the interval is free, the other row when that is free, and is
   staggered rather than overlapped when both rows are busy); a block is as
   wide as its COUNTED duration (blocks × 30 minutes from its start, never the
   source start→end window) and shows its name plus (when it fits) the time
   and block count. A window-kind/template anchor also draws its allowed
   window as a light dashed track behind the counted block. Clicking one
   selects it; anchors that are off today, outside the frame or fully elapsed
   are not drawn at all (a "N hidden today" note opens All blocks); a live
   block is never narrower than 28px; pointer-dragging moves it (snaps to 15 minutes, clamped to the
   frame) and the arrow keys move it too.

   Pure presentation: selection state and the Wall-filtered id sets come from
   DaySetupPanel, so the strip never re-implements the Mint/wall contract. */

import { Fragment } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { display12h, toHHMM, toMinutes } from "../../model/time";
import type { CalendarCapacityClass } from "../../model/types";
import { dragTargetMinutes, keyboardTargetMinutes } from "./anchorDrag";
import { compactClock, compactHour, compactRange } from "./compactTime";

/** Pointer travel (px) before a press on an anchor becomes a drag. */
const DRAG_THRESHOLD_PX = 4;
/** One counted block is 30 minutes: block width = blocks × BLOCK_MIN. */
const BLOCK_MIN = 30;
/** Two anchor sub-rows: top 6px, each 38px tall with an 8px gap. */
const ANCHOR_ROW_TOP = 6;
const ANCHOR_ROW_PITCH = 46;
/** The floor that keeps a very short live block visible/clickable. Widths are
    never exaggerated past it; the full name/time/count stay in title + aria. */
const MIN_VISIBLE_W = 28;
/** Calendar label fit: estimated glyph width, padding, and the narrowest
    block that may carry (ellipsized) text inside itself. */
const CAL_CHAR_PX = 6.6;
const CAL_PAD_PX = 16;
const CAL_MIN_INSIDE_PX = 44;
const CAL_LABEL_GAP_PX = 8;
/** Visible gap between a clipped block's right edge and the next block. */
const STAGGER_GAP_PX = 2;
/** README row preference: these ride row 0, the rest of the catalogue
    (Foods Breakfast, Sudsing, Foods Dinner) rides row 1. */
const ANCHOR_ROW_PREFERENCE: Record<string, number> = {
  "Morning Routine": 0,
  Live: 0,
  "Night Routine": 0,
  "Foods Breakfast": 1,
  Sudsing: 1,
  "Foods Dinner": 1,
};

export interface StripSession {
  id: string;
  name: string;
  start: string;
  end: string;
}

export interface StripAnchor {
  id: string;
  name: string;
  /** 24h HH:MM today's start — the counted block's left edge. */
  start: string;
  /** Counted duration in 30-minute blocks: the visible width is exactly
      blocks × 30 minutes from `start`, never the source window span. */
  blocks: number;
  /** Window-kind/template anchors only: the window the item may float in,
      drawn as a light dashed non-interactive track behind the counted block. */
  window?: { start: string; end: string };
  counted: boolean;
  /** Counted config anchor that began and ended before the frame: it sits at
      the frame edge and is re-includable, but the strip does not draw it. */
  elapsed?: boolean;
  reason: string | null;
  /** Counted, config-sourced anchors only: calendar rows and anything not
      counted today stay put. Absent means not movable. */
  movable?: boolean;
}

export interface StripCalendarEvent {
  id: string;
  name: string;
  /** 24h HH:MM */
  start: string;
  durationMin: number;
  /** False when skipped today (dimmed). */
  counted: boolean;
  /** 'work' is a Mint work block and draws in the minting family, not the
      calendar blue. Absent is treated as 'fixed'. */
  capacityClass?: CalendarCapacityClass;
}

export interface DayStripProps {
  frameStart: string;
  frameEnd: string;
  sessions: StripSession[];
  selectedIds: string[];
  selectableIds: string[];
  onSelectIds: (ids: string[]) => void;
  onToggleSession: (id: string) => void;
  anchors: StripAnchor[];
  /** Click / Enter / Space on a block selects it (the popover opens under it). */
  onSelectAnchor: (id: string) => void;
  /** Today's calendar events, drawn on their own read-only lane. */
  calendar?: StripCalendarEvent[];
  /** Called once per completed move (pointer drop or arrow key) with the new
      24h HH:MM start. Without it no anchor is movable. */
  onMoveAnchor?: (id: string, start: string) => void;
  /** The anchor whose popover is open, drawn in the selected treatment. */
  selectedAnchorId?: string | null;
  /** Planning 2a "Already placed": draw the same lanes with no interaction at
      all — no slot buttons, no anchor buttons, no drag, no selection. The
      strip stays a read-only picture of what is already on the day. */
  readOnly?: boolean;
  /** Opens the All blocks modal from the "N hidden today" note. */
  onShowAllBlocks?: () => void;
}

export function DayStrip(props: DayStripProps) {
  const startMin = toMinutes(props.frameStart);
  const endMin = toMinutes(props.frameEnd);
  const span = Math.max(1, endMin - startMin);
  const pct = (min: number) =>
    Math.max(0, Math.min(100, ((min - startMin) / span) * 100));
  const rawPct = (min: number) => ((min - startMin) / span) * 100;
  const [drag, setDrag] = useState<{ anchor: number; moved: boolean } | null>(null);
  const [suppressClick, setSuppressClick] = useState(false);
  // Anchor move: the gesture lives in a ref (pointer events outrun renders);
  // `preview` is the render-side copy that draws the block and its label.
  const anchorDrag = useRef<{
    id: string;
    pointerId: number;
    x0: number;
    laneWidth: number;
    origMin: number;
    lengthMin: number;
    moved: boolean;
    target: number;
  } | null>(null);
  const suppressAnchorClick = useRef(false);
  const [preview, setPreview] = useState<{ id: string; from: number; min: number } | null>(null);
  const [announcement, setAnnouncement] = useState("");
  // The anchors-lane pixel width, so a block that cannot fit its meta line
  // shows the name only. 900 is only the first-paint guess.
  const laneRef = useRef<HTMLDivElement>(null);
  const [laneW, setLaneW] = useState(900);
  useEffect(() => {
    const el = laneRef.current;
    if (!el) return;
    const measure = () => {
      const w = el.getBoundingClientRect().width;
      if (w > 0) setLaneW(w);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const rangeIds = (a: number, b: number) =>
    props.sessions.slice(Math.min(a, b), Math.max(a, b) + 1).map((session) => session.id);
  const focusSlot = (index: number) => {
    const session = props.sessions[index];
    if (session) document.getElementById(`strip-slot-${session.id}`)?.focus();
  };

  const onSlotMouseDown = (index: number) => {
    if (!props.selectableIds.includes(props.sessions[index].id)) return;
    setSuppressClick(false);
    setDrag({ anchor: index, moved: false });
  };
  const onSlotMouseEnter = (index: number) => {
    if (!drag || index === drag.anchor) return;
    setDrag({ ...drag, moved: true });
    props.onSelectIds(rangeIds(drag.anchor, index));
  };
  const onSlotMouseUp = () => {
    if (drag?.moved) setSuppressClick(true);
    setDrag(null);
  };
  const onSlotClick = (index: number) => {
    if (suppressClick) {
      setSuppressClick(false);
      return;
    }
    props.onToggleSession(props.sessions[index].id);
  };
  const onSlotKeyDown = (event: KeyboardEvent, index: number) => {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      focusSlot(Math.min(props.sessions.length - 1, index + 1));
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      focusSlot(Math.max(0, index - 1));
    }
  };

  /** Counted duration in minutes: the block's visible width. */
  const lengthOf = (anchor: StripAnchor) => Math.max(0, anchor.blocks) * BLOCK_MIN;
  const isMovable = (anchor: StripAnchor) =>
    anchor.movable === true && anchor.counted && props.onMoveAnchor !== undefined;

  const endAnchorDrag = (target: HTMLElement | null, pointerId: number | undefined) => {
    anchorDrag.current = null;
    setPreview(null);
    if (target && pointerId !== undefined) {
      try {
        target.releasePointerCapture?.(pointerId);
      } catch {
        /* capture already gone */
      }
    }
  };
  const onAnchorPointerDown = (e: PointerEvent, anchor: StripAnchor) => {
    if (!isMovable(anchor) || e.button !== 0) return;
    const el = e.currentTarget as HTMLElement;
    const lane = el.parentElement;
    suppressAnchorClick.current = false;
    anchorDrag.current = {
      id: anchor.id,
      pointerId: e.pointerId,
      x0: e.clientX,
      laneWidth: lane ? lane.getBoundingClientRect().width : 0,
      origMin: toMinutes(anchor.start),
      lengthMin: lengthOf(anchor),
      moved: false,
      target: toMinutes(anchor.start),
    };
    try {
      el.setPointerCapture?.(e.pointerId);
    } catch {
      /* synthetic or already-released pointer */
    }
  };
  const onAnchorPointerMove = (e: PointerEvent) => {
    const dragState = anchorDrag.current;
    if (!dragState || e.pointerId !== dragState.pointerId) return;
    const dx = e.clientX - dragState.x0;
    if (!dragState.moved && Math.abs(dx) < DRAG_THRESHOLD_PX) return;
    dragState.moved = true;
    dragState.target = dragTargetMinutes({
      startMin: dragState.origMin,
      deltaPx: dx,
      laneWidthPx: dragState.laneWidth,
      dayStart: startMin,
      dayEnd: endMin,
      lengthMin: dragState.lengthMin,
    });
    setPreview({ id: dragState.id, from: dragState.origMin, min: dragState.target });
  };
  const onAnchorPointerUp = (e: PointerEvent) => {
    const dragState = anchorDrag.current;
    if (!dragState || e.pointerId !== dragState.pointerId) return;
    const { id, moved, target, origMin } = dragState;
    endAnchorDrag(e.currentTarget as HTMLElement, e.pointerId);
    if (!moved) return;
    // The click that follows this pointerup belongs to the drag, not to the
    // popover. Cleared on the next tick and on the next press.
    suppressAnchorClick.current = true;
    setTimeout(() => {
      suppressAnchorClick.current = false;
    }, 0);
    if (target !== origMin) {
      setAnnouncement(`${props.anchors.find((a) => a.id === id)?.name ?? id} moved to ${display12h(toHHMM(target))}`);
      props.onMoveAnchor?.(id, toHHMM(target));
    }
  };
  const onAnchorPointerCancel = (e: PointerEvent) => {
    if (anchorDrag.current) endAnchorDrag(e.currentTarget as HTMLElement, e.pointerId);
  };
  const onAnchorClick = (id: string) => {
    if (suppressAnchorClick.current) {
      suppressAnchorClick.current = false;
      return;
    }
    props.onSelectAnchor(id);
  };
  const onAnchorKeyDown = (e: KeyboardEvent, anchor: StripAnchor) => {
    if (e.key === "Escape" && anchorDrag.current) {
      // Abandon the gesture; the drop that follows must not commit it.
      anchorDrag.current = null;
      setPreview(null);
      suppressAnchorClick.current = true;
      setTimeout(() => {
        suppressAnchorClick.current = false;
      }, 0);
      return;
    }
    if (!isMovable(anchor) || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
    e.preventDefault();
    const from = toMinutes(anchor.start);
    const to = keyboardTargetMinutes(from, e.key === "ArrowRight" ? 1 : -1, e.shiftKey, {
      dayStart: startMin,
      dayEnd: endMin,
      lengthMin: lengthOf(anchor),
    });
    if (to === from) return;
    setAnnouncement(`${anchor.name} moved to ${display12h(toHHMM(to))}`);
    props.onMoveAnchor?.(anchor.id, toHHMM(to));
  };

  // Hour ticks: every whole hour inside the frame (8a … 11p for 7:30a–11:45p).
  const hours: number[] = [];
  for (let m = Math.ceil(startMin / 60) * 60; m < endMin; m += 60) hours.push(m);

  // A Mint block wholly outside the frame has no place on the strip; one that
  // straddles a frame edge is clipped to it. Hidden blocks are counted in a
  // note.
  const sessionOutside = (session: StripSession) =>
    toMinutes(session.end) <= startMin || toMinutes(session.start) >= endMin;
  const hiddenSessions = props.sessions.filter(sessionOutside).length;

  // Anchors draw across their counted span (blocks × 30 minutes from start).
  // Each takes its README preferred row when the interval is free, the other
  // row when that is free, and otherwise stays on its preferred row to be
  // staggered sideways, so two blocks never draw on top of each other.
  const pxPerMin = laneW / span;
  const rowIntervals: Array<Array<{ min: number; max: number }>> = [[], []];
  const overlapsRow = (row: number, min: number, max: number) =>
    rowIntervals[row].some((iv) => min < iv.max && max > iv.min);
  // Off, outside-frame and fully elapsed anchors are not drawn (All blocks
  // lists them); the open one stays so its popover keeps its anchor.
  const isHiddenAnchor = (anchor: StripAnchor) =>
    anchor.id !== props.selectedAnchorId && (!anchor.counted || anchor.elapsed === true);
  const hiddenAnchors = props.anchors.filter(isHiddenAnchor).length;
  const drawnAnchors = props.anchors.filter((anchor) => !isHiddenAnchor(anchor));
  const floorMin = MIN_VISIBLE_W / pxPerMin;
  const placedAnchors = drawnAnchors
    .map((anchor) => ({
      anchor,
      min: toMinutes(anchor.start),
      length: lengthOf(anchor),
    }))
    .filter(({ min, length }) => min < endMin && min + length > startMin)
    .sort((a, b) => a.min - b.min)
    .map(({ anchor, min, length }) => {
      // Rows pack on the drawn width, so a short block's 28px floor counts.
      const max = min + Math.max(length, floorMin);
      const preferred = ANCHOR_ROW_PREFERENCE[anchor.name] ?? 0;
      const other = preferred === 0 ? 1 : 0;
      let row = preferred;
      if (overlapsRow(preferred, min, max) && !overlapsRow(other, min, max)) row = other;
      rowIntervals[row].push({ min, max });
      return { anchor, min, length, row };
    });
  // Stagger: inside a row the earlier of two overlapping blocks keeps its true
  // start but its visible width stops 2px before the next block starts, never
  // narrower than MIN_VISIBLE_W. When that floor cannot fit, the later block's
  // visible left edge is nudged right just enough that neither block is
  // hidden. Widths stay true to the counted span; the floor is the only
  // exaggeration, and true times/counts stay in title/aria-label.
  const anchorGeometry = new Map<string, { leftPct: number; widthPct: number }>();
  rowIntervals.forEach((_, row) => {
    const rowBlocks = placedAnchors.filter((p) => p.row === row).sort((a, b) => a.min - b.min);
    let prevRight = Number.NEGATIVE_INFINITY;
    rowBlocks.forEach((block, i) => {
      const trueLeft = Math.max(0, (block.min - startMin) * pxPerMin);
      const trueRight = Math.min(laneW, (block.min + block.length - startMin) * pxPerMin);
      const left = Math.max(trueLeft, prevRight + STAGGER_GAP_PX);
      // The counted width wins; the 12px floor only keeps a short block
      // visible and never stretches it toward its source span.
      let right = Math.max(trueRight, left + MIN_VISIBLE_W);
      let drawLeft = left;
      if (right > laneW) {
        right = laneW;
        drawLeft = Math.max(0, Math.min(left, laneW - MIN_VISIBLE_W));
      }
      const next = rowBlocks[i + 1];
      if (next) {
        const nextLeft = Math.max(0, (next.min - startMin) * pxPerMin);
        const clipLimit = nextLeft - STAGGER_GAP_PX;
        if (right > clipLimit) right = Math.max(left + MIN_VISIBLE_W, clipLimit);
      }
      anchorGeometry.set(block.anchor.id, {
        leftPct: (drawLeft / laneW) * 100,
        widthPct: (Math.max(1, right - drawLeft) / laneW) * 100,
      });
      prevRight = right;
    });
  });
  const anchorRowCount = Math.max(
    1,
    rowIntervals.reduce((count, blocks, row) => (blocks.length > 0 ? row + 1 : count), 1),
  );
  const anchorLaneH = Math.max(96, anchorRowCount * ANCHOR_ROW_PITCH + 4);

  // Window-kind/template anchors keep their allowed window visible as a light
  // dashed track behind the solid counted block. The track is background: it
  // never takes part in row packing or staggering, carries no text and is not
  // interactive; the solid block is the click/drag target.
  const anchorRowById = new Map(placedAnchors.map((placed) => [placed.anchor.id, placed.row]));
  const anchorWindows = drawnAnchors
    .filter((anchor) => anchor.window != null)
    .map((anchor) => {
      const windowRange = anchor.window as { start: string; end: string };
      const left = Math.max(toMinutes(windowRange.start), startMin);
      const right = Math.min(toMinutes(windowRange.end), endMin);
      return {
        anchor,
        left,
        right,
        row: anchorRowById.get(anchor.id) ?? ANCHOR_ROW_PREFERENCE[anchor.name] ?? 0,
      };
    })
    .filter(({ left, right }) => right > left);
  const blockAmount = (blocks: number) => `${blocks} ${blocks === 1 ? "block" : "blocks"}`;
  const metaText = (anchor: StripAnchor) =>
    `${compactClock(anchor.start)} · ${blockAmount(anchor.blocks)}`;
  const showMeta = (anchor: StripAnchor, visiblePx: number) =>
    visiblePx >= Math.max(
      anchor.name.length * 6.6 + 20,
      metaText(anchor).length * 6.4 + 20,
    );

  // Calendar events draw read-only, clipped to the frame. The title goes
  // inside the block when it fits (ellipsized when it partly fits); a block
  // under CAL_MIN_INSIDE_PX labels itself outside, right then left, never past
  // the lane. Rows reserve any outside label, so labels never collide.
  const calendarRowsEnd: number[] = [];
  const placedCalendar = (props.calendar ?? [])
    .map((event) => ({
      event,
      min: Math.max(toMinutes(event.start), startMin),
      max: Math.min(toMinutes(event.start) + Math.max(15, event.durationMin), endMin),
    }))
    .filter(({ min, max }) => max > min && min < endMin)
    .sort((a, b) => a.min - b.min)
    .map(({ event, min, max }) => {
      const text = `${event.name} ${compactClock(event.start)}`;
      const textPx = text.length * CAL_CHAR_PX;
      const blockPx = (max - min) * pxPerMin;
      const rightRoom = laneW - (max - startMin) * pxPerMin - CAL_LABEL_GAP_PX;
      const leftRoom = (min - startMin) * pxPerMin - CAL_LABEL_GAP_PX;
      let fit: "inside" | "clip" | "right" | "left" | "none";
      if (blockPx >= textPx + CAL_PAD_PX) fit = "inside";
      else if (blockPx >= CAL_MIN_INSIDE_PX) fit = "clip";
      else if (rightRoom >= textPx) fit = "right";
      else if (leftRoom >= textPx) fit = "left";
      else fit = "none";
      const reserve = (textPx + CAL_LABEL_GAP_PX) / pxPerMin;
      const occMin = fit === "left" ? min - reserve : min;
      const occMax = fit === "right" ? max + reserve : max;
      let row = calendarRowsEnd.findIndex((end) => end <= occMin);
      if (row === -1) {
        row = calendarRowsEnd.length;
        calendarRowsEnd.push(0);
      }
      calendarRowsEnd[row] = occMax;
      return { event, min, max, row, text, fit };
    });
  const calendarLaneH = Math.max(40, calendarRowsEnd.length * 30 + 10);

  // Contiguous selected / wall-blocked Mint slots read as one block: a run
  // gets rounded ends and a caption ("3-5 PM", "Wall") laid over it.
  const visible = props.sessions
    .map((session, index) => ({ session, index }))
    .filter(({ session }) => !sessionOutside(session));
  const stateOf = (session: StripSession) =>
    !props.selectableIds.includes(session.id)
      ? "blocked"
      : props.selectedIds.includes(session.id)
        ? "selected"
        : "available";
  const clipStart = (session: StripSession) => Math.max(toMinutes(session.start), startMin);
  const clipEnd = (session: StripSession) => Math.min(toMinutes(session.end), endMin);
  const runPos = new Map<string, { first: boolean; last: boolean }>();
  const runs: Array<{ state: string; start: number; end: number }> = [];
  visible.forEach(({ session }, i) => {
    const state = stateOf(session);
    const prev = visible[i - 1]?.session;
    const next = visible[i + 1]?.session;
    const joins = (a: StripSession | undefined, b: StripSession) =>
      a !== undefined && stateOf(a) === state && clipEnd(a) === clipStart(b);
    const first = !joins(prev, session);
    const last = !(next !== undefined && joins(session, next));
    runPos.set(session.id, { first, last });
    if (state === "available") return;
    if (first) runs.push({ state, start: clipStart(session), end: clipEnd(session) });
    else runs[runs.length - 1].end = clipEnd(session);
  });
  const runCaption = (run: { state: string; start: number; end: number }) => {
    const length = run.end - run.start;
    if (run.state === "selected") return length >= 90 ? compactRange(run.start, run.end) : null;
    if (length >= 300) return "Blocked by Wall";
    return length >= 60 ? "Wall" : null;
  };

  return (
    <div class="day-strip" data-testid="day-strip">
      <div class="day-strip__row day-strip__row--ruler">
        <span class="day-strip__lane-label" aria-hidden="true" />
        <div class="day-strip__ticks" aria-hidden="true">
          {hours.map((m) => (
            <span class="day-strip__tick" style={{ left: `${pct(m)}%` }} key={m}>
              {compactHour(m)}
            </span>
          ))}
        </div>
      </div>

      {placedCalendar.length > 0 && (
        <div class="day-strip__row">
          <span class="day-strip__lane-label">Calendar</span>
          <div
            class="day-strip__lane day-strip__lane--calendar"
            aria-label="Calendar"
            style={{ height: `${calendarLaneH}px` }}
          >
            {placedCalendar.map(({ event, min, max, row, text, fit }) => (
              <Fragment key={event.id}>
                <span
                  class={`strip-cal${event.counted ? "" : " strip-cal--skipped"}${
                    event.capacityClass === "work" ? " strip-cal--work" : ""
                  }`}
                  style={{
                    left: `${rawPct(min)}%`,
                    width: `${((max - min) / span) * 100}%`,
                    top: `${5 + row * 30}px`,
                  }}
                  title={`${event.name} ${display12h(event.start)}`}
                  role="img"
                  aria-label={`${event.name} ${display12h(event.start)} (calendar)`}
                >
                  {(fit === "inside" || fit === "clip") && (
                    <span
                      class={`strip-cal__label strip-cal__label--inside${
                        event.counted ? "" : " strip-cal__label--not-counted"
                      }`}
                      data-fit={fit}
                    >
                      {text}
                    </span>
                  )}
                </span>
                {(fit === "right" || fit === "left") && (
                  <span
                    class={`strip-cal__label strip-cal__label--${fit}${
                      event.counted ? "" : " strip-cal__label--not-counted"
                    }`}
                    data-fit={fit}
                    style={
                      fit === "right"
                        ? { left: `calc(${rawPct(max)}% + ${CAL_LABEL_GAP_PX}px)`, top: `${5 + row * 30}px` }
                        : { right: `calc(${100 - rawPct(min)}% + ${CAL_LABEL_GAP_PX}px)`, top: `${5 + row * 30}px` }
                    }
                  >
                    {text}
                  </span>
                )}
              </Fragment>
            ))}
          </div>
        </div>
      )}

      <div class="day-strip__row">
        <span class="day-strip__lane-label">Mint</span>
        <div
          class={`day-strip__lane day-strip__lane--mint${
            props.sessions.length === 0 ? " day-strip__lane--mint-off" : ""
          }`}
          aria-label="Mint sessions"
        >
          {props.sessions.length === 0 ? (
            <span class="day-strip__empty">Off today</span>
          ) : (
            visible.map(({ session, index }) => {
              const state = stateOf(session);
              const blocked = state === "blocked";
              const selected = state === "selected";
              const left = pct(clipStart(session));
              const width = Math.max(1.5, pct(clipEnd(session)) - left);
              const pos = runPos.get(session.id) ?? { first: true, last: true };
              const slotClass = `strip-slot strip-slot--${state}${pos.first ? " strip-slot--run-first" : ""}${
                pos.last ? " strip-slot--run-last" : ""
              }`;
              const slotStyle = { left: `${left}%`, width: `${width}%` };
              const slotLabel = `${session.name} ${display12h(session.start)}–${display12h(session.end)} — ${
                blocked ? "blocked by wall" : selected ? "selected" : "available"
              }`;
              if (props.readOnly) {
                return (
                  <span
                    key={session.id}
                    class={slotClass}
                    style={slotStyle}
                    data-slot-id={session.id}
                    data-state={state}
                    aria-hidden="true"
                  >
                    <span class="strip-slot__label">{session.name}</span>
                  </span>
                );
              }
              return (
                <button
                  type="button"
                  id={`strip-slot-${session.id}`}
                  class={slotClass}
                  style={slotStyle}
                  data-slot-id={session.id}
                  data-state={state}
                  aria-pressed={selected}
                  aria-label={slotLabel}
                  onMouseDown={() => onSlotMouseDown(index)}
                  onMouseEnter={() => onSlotMouseEnter(index)}
                  onMouseUp={onSlotMouseUp}
                  onClick={() => onSlotClick(index)}
                  onKeyDown={(e) => onSlotKeyDown(e as unknown as KeyboardEvent, index)}
                  key={session.id}
                >
                  <span class="strip-slot__label">{session.name}</span>
                </button>
              );
            })
          )}
          {runs.map((run) => {
            const caption = runCaption(run);
            if (caption === null) return null;
            return (
              <span
                class={`strip-run strip-run--${run.state}`}
                aria-hidden="true"
                style={{ left: `${pct(run.start)}%`, width: `${pct(run.end) - pct(run.start)}%` }}
                key={`${run.state}-${run.start}`}
              >
                {caption}
              </span>
            );
          })}
        </div>
      </div>

      <div class="day-strip__row">
        <span class="day-strip__lane-label day-strip__lane-label--anchors">Anchors</span>
        <div
          class="day-strip__lane day-strip__lane--anchors"
          ref={laneRef}
          aria-label="Anchors"
          style={{ height: `${anchorLaneH}px` }}
        >
          {anchorWindows.map(({ anchor, left, right, row }) => (
            <span
              key={`window-${anchor.id}`}
              class="strip-anchor__window"
              data-anchor-window-id={anchor.id}
              aria-hidden="true"
              style={{
                left: `${rawPct(left)}%`,
                width: `${((right - left) / span) * 100}%`,
                top: `${ANCHOR_ROW_TOP + row * ANCHOR_ROW_PITCH}px`,
              }}
            />
          ))}
          {placedAnchors.map(({ anchor, min, length, row }) => {
            const dragging = preview?.id === anchor.id;
            const drawMin = dragging ? preview.min : min;
            const max = Math.min(drawMin + length, endMin);
            const left = Math.max(drawMin, startMin);
            const geom = anchorGeometry.get(anchor.id);
            const leftPct = dragging || !geom ? rawPct(left) : geom.leftPct;
            const widthPct = dragging || !geom ? ((max - left) / span) * 100 : geom.widthPct;
            const visiblePx = (widthPct / 100) * laneW;
            const movable = isMovable(anchor);
            const reason = anchor.reason ?? "";
            const selected = props.selectedAnchorId === anchor.id;
            const windowRange = anchor.window;
            const windowText = windowRange
              ? ` (window ${display12h(windowRange.start)}–${display12h(windowRange.end)})`
              : "";
            const anchorClass = `strip-anchor${anchor.counted ? "" : " strip-anchor--not-counted"}${
              reason === "skipped" ? " strip-anchor--skipped" : ""
            }${movable ? " strip-anchor--movable" : ""}${
              dragging ? " strip-anchor--dragging" : ""
            }${selected ? " strip-anchor--selected" : ""}${props.readOnly ? " strip-anchor--readonly" : ""}`;
            const anchorStyle = {
              left: `${leftPct}%`,
              width: `${widthPct}%`,
              top: `${ANCHOR_ROW_TOP + row * ANCHOR_ROW_PITCH}px`,
            };
            const anchorTitle = `${anchor.name} ${display12h(anchor.start)} · ${blockAmount(anchor.blocks)}${windowText}${
              movable ? " — drag to move, or use the arrow keys" : ""
            }`;
            const anchorLabel = `${anchor.name} ${display12h(anchor.start)}${
              anchor.counted
                ? ` · ${blockAmount(anchor.blocks)}${windowText}`
                : ` — not counted (${anchor.reason})`
            }`;
            if (props.readOnly) {
              return (
                <span
                  key={anchor.id}
                  class={anchorClass}
                  style={anchorStyle}
                  data-anchor-id={anchor.id}
                  title={anchorTitle}
                  aria-hidden="true"
                >
                  <span class="strip-anchor__name">{anchor.name}</span>
                  {showMeta(anchor, visiblePx) && (
                    <span class="strip-anchor__meta">{metaText(anchor)}</span>
                  )}
                </span>
              );
            }
            return (
              <button
                type="button"
                class={anchorClass}
                style={anchorStyle}
                data-anchor-id={anchor.id}
                title={anchorTitle}
                aria-label={anchorLabel}
                onPointerDown={(e) => onAnchorPointerDown(e as unknown as PointerEvent, anchor)}
                onPointerMove={(e) => onAnchorPointerMove(e as unknown as PointerEvent)}
                onPointerUp={(e) => onAnchorPointerUp(e as unknown as PointerEvent)}
                onPointerCancel={(e) => onAnchorPointerCancel(e as unknown as PointerEvent)}
                onKeyDown={(e) => onAnchorKeyDown(e as unknown as KeyboardEvent, anchor)}
                onClick={() => onAnchorClick(anchor.id)}
                key={anchor.id}
              >
                <span class="strip-anchor__name">{anchor.name}</span>
                {showMeta(anchor, visiblePx) && (
                  <span class="strip-anchor__meta">{metaText(anchor)}</span>
                )}
              </button>
            );
          })}
          {preview && (
            <span
              class="day-strip__drag-label"
              style={{
                left: `${Math.max(
                  10,
                  Math.min(
                    90,
                    pct(
                      preview.min +
                        (placedAnchors.find((p) => p.anchor.id === preview.id)?.length ?? BLOCK_MIN) / 2,
                    ),
                  ),
                )}%`,
              }}
            >
              {display12h(toHHMM(preview.from))} → {display12h(toHHMM(preview.min))}
            </span>
          )}
          <span class="sr-only" role="status" data-testid="anchor-move-status">
            {announcement}
          </span>
        </div>
      </div>

      {hiddenAnchors > 0 &&
        (props.onShowAllBlocks && !props.readOnly ? (
          <button type="button" class="day-strip__hidden-note" onClick={props.onShowAllBlocks}>
            {hiddenAnchors} hidden today — All blocks
          </button>
        ) : (
          <p class="day-strip__note">{hiddenAnchors} hidden today</p>
        ))}

      {hiddenSessions > 0 && (
        <p class="day-strip__note">
          {hiddenSessions} Mint block{hiddenSessions === 1 ? "" : "s"} outside the frame
        </p>
      )}
    </div>
  );
}
