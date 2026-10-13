/* U12 day-frame strip (R20a). One hour strip spans the day frame with a Mint
   lane (30-minute session slots) and an Anchors lane. Dragging across Mint
   slots selects those sessions; slots blocked by a fixed/work wall are not
   selectable. Every interaction has a keyboard equivalent: focus a slot,
   Space toggles it, Arrow keys move between slots. Anchor blocks open the
   existing anchored editor, and a counted config anchor can also be moved:
   pointer-drag it (snaps to 15 minutes, clamped to the ruler) or use the
   Left/Right arrows (Shift = an hour). A press that never moves is still a
   click, so the editor opens exactly as before.

   Pure presentation: selection state and the Wall-filtered id sets come from
   DaySetupPanel, so the strip never re-implements the Mint/wall contract. */

import { Fragment, type ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { display12h, toHHMM, toMinutes } from "../../model/time";
import type { CalendarCapacityClass } from "../../model/types";
import { dragTargetMinutes, keyboardTargetMinutes } from "./anchorDrag";
import { compactClock, compactHour, compactRange } from "./compactTime";

/** Pointer travel (px) before a press on an anchor becomes a drag. */
const DRAG_THRESHOLD_PX = 4;

export interface StripSession {
  id: string;
  name: string;
  start: string;
  end: string;
}

export interface StripAnchor {
  id: string;
  name: string;
  start: string;
  counted: boolean;
  reason: string | null;
  blocks: number;
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
  onEditAnchor: (id: string) => void;
  /** Today's calendar events, drawn on their own read-only lane. */
  calendar?: StripCalendarEvent[];
  /** Called once per completed move (pointer drop or arrow key) with the new
      24h HH:MM start. Without it no anchor is movable. */
  onMoveAnchor?: (id: string, start: string) => void;
  /** Control rendered at the right of the Mint caption row (the Mint blocks
      slider). Optional so the strip stays usable on its own. */
  mintControl?: ComponentChildren;
}

export function DayStrip(props: DayStripProps) {
  // The frame is the confirmed day window; the strip itself spans the whole
  // waking day (8a to midnight, widened when the frame reaches outside it) so
  // the frame reads as an outlined region on the ruler and time outside it is
  // visibly unavailable. Selection logic below still works on frame minutes.
  const startMin = toMinutes(props.frameStart);
  const endMin = toMinutes(props.frameEnd);
  const dayStart = Math.min(8 * 60, Math.floor(startMin / 60) * 60);
  const dayEnd = Math.max(24 * 60, Math.ceil(endMin / 60) * 60);
  const daySpan = Math.max(1, dayEnd - dayStart);
  const pct = (min: number) =>
    Math.max(0, Math.min(100, ((min - dayStart) / daySpan) * 100));
  const rawPct = (min: number) => ((min - dayStart) / daySpan) * 100;
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
  // The strip's pixel width, so labels that do not fit inside a short block
  // can be placed beside it. 900 is only the first-paint guess.
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

  const BLOCK_MIN = 30;
  const lengthOf = (anchor: StripAnchor) =>
    anchor.counted ? Math.max(1, anchor.blocks) * BLOCK_MIN : BLOCK_MIN;
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
    const drag = anchorDrag.current;
    if (!drag || e.pointerId !== drag.pointerId) return;
    const dx = e.clientX - drag.x0;
    if (!drag.moved && Math.abs(dx) < DRAG_THRESHOLD_PX) return;
    drag.moved = true;
    drag.target = dragTargetMinutes({
      startMin: drag.origMin,
      deltaPx: dx,
      laneWidthPx: drag.laneWidth,
      dayStart,
      dayEnd,
      lengthMin: drag.lengthMin,
    });
    setPreview({ id: drag.id, from: drag.origMin, min: drag.target });
  };
  const onAnchorPointerUp = (e: PointerEvent) => {
    const drag = anchorDrag.current;
    if (!drag || e.pointerId !== drag.pointerId) return;
    const { id, moved, target, origMin } = drag;
    endAnchorDrag(e.currentTarget as HTMLElement, e.pointerId);
    if (!moved) return;
    // The click that follows this pointerup belongs to the drag, not to the
    // editor. Cleared on the next tick and on the next press.
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
    props.onEditAnchor(id);
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
      dayStart,
      dayEnd,
      lengthMin: lengthOf(anchor),
    });
    if (to === from) return;
    setAnnouncement(`${anchor.name} moved to ${display12h(toHHMM(to))}`);
    props.onMoveAnchor?.(anchor.id, toHHMM(to));
  };

  // Hour marks: every hour is a gridline through all lanes; labels are every
  // hour when the strip is wide enough for them, else every two.
  const hourly = laneW >= 760;
  const ticks: number[] = [];
  for (let m = dayStart; m < dayEnd; m += hourly ? 60 : 120) ticks.push(m);
  const gridHours: number[] = [];
  for (let m = dayStart; m <= dayEnd; m += 60) gridHours.push(m);

  // A Mint block wholly outside the frame has no place on the strip; one that
  // straddles a frame edge is clipped to it. Hidden blocks are counted in a note.
  const sessionOutside = (session: StripSession) =>
    toMinutes(session.end) <= startMin || toMinutes(session.start) >= endMin;
  const hiddenSessions = props.sessions.filter(sessionOutside).length;

  // Anchors are drawn across the whole day, one block-length wide, and
  // dimmed when they are not counted today (outside the frame or skipped).
  // Blocks that would overlap are stacked into sub-rows (greedy interval
  // scheduling); a 30-minute overlap is tolerated so a neighbouring pair such
  // as Sudsing and Dinner share one row like the reference.
  // A name that does not fit inside its block is drawn beside it (right,
  // or left when the right would run off the strip) and counts toward the
  // row's occupied span so neighbouring labels never collide.
  const pxPerMin = laneW / daySpan;
  const place = <T,>(
    entries: Array<{ item: T; min: number; length: number; text: string; solid: boolean }>,
  ) => {
    const rowState: Array<{ end: number; outside: boolean }> = [];
    const placed = entries
      .filter(({ min, length }) => min + length > dayStart && min < dayEnd)
      .sort((x, y) => x.min - y.min)
      .map(({ item, min, length, text, solid }) => {
        const textPx = text.length * 6.4 + 10;
        const inside = solid && length * pxPerMin >= textPx;
        const labelMin = textPx / pxPerMin;
        const side: "right" | "left" =
          (min + length - dayStart) * pxPerMin + textPx + 6 > laneW ? "left" : "right";
        const lo = inside || side === "right" ? min : min - labelMin;
        const hi = inside || side === "left" ? min + length : min + length + labelMin;
        let row = rowState.findIndex(
          (r) => r.end - (r.outside || !inside ? 0 : BLOCK_MIN) <= lo,
        );
        if (row === -1) {
          row = rowState.length;
          rowState.push({ end: 0, outside: false });
        }
        rowState[row] = { end: hi, outside: !inside };
        return { item, min, length, row, inside, side, text };
      });
    return { placed, rows: Math.max(1, rowState.length) };
  };
  const anchorLayout = place(
    props.anchors.map((anchor) => ({
      item: anchor,
      min: toMinutes(anchor.start),
      length: lengthOf(anchor),
      text: `${anchor.name} ${compactClock(anchor.start)}`,
      solid: anchor.counted,
    })),
  );
  const placedAnchors = anchorLayout.placed.map((p) => ({
    anchor: p.item,
    min: p.min,
    length: p.length,
    row: p.row,
    inside: p.inside,
    side: p.side,
    text: p.text,
  }));
  const calendarLayout = place(
    (props.calendar ?? []).map((event) => ({
      item: event,
      min: toMinutes(event.start),
      length: Math.max(15, event.durationMin),
      text: `${event.name} ${compactClock(event.start)}`,
      solid: event.counted,
    })),
  );
  const anchorRows = anchorLayout.rows;
  const calendarRows = calendarLayout.rows;
  const anchorRowH = 28;

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
      <div class="day-strip__grid" aria-hidden="true">
        {gridHours.map((m) => (
          <span
            class={`day-strip__gridline${(m / 60) % 2 === 0 ? " day-strip__gridline--major" : ""}`}
            style={{ left: `${pct(m)}%` }}
            key={m}
          />
        ))}
      </div>
      <div class="day-strip__axis" aria-hidden="true">
        {ticks
          // The "frame" marker sits on the frame's start; an hour label that
          // close to it would print over it.
          .filter((m) => Math.abs(m - startMin) * pxPerMin >= 32)
          .map((m) => (
          <span class="day-strip__tick" style={{ left: `${pct(m)}%` }} key={m}>
            {compactHour(m)}
          </span>
        ))}
      </div>
      <div class="day-strip__frame" aria-hidden="true">
        <span class="day-strip__frame-box" style={{ left: `${pct(startMin)}%`, width: `${pct(endMin) - pct(startMin)}%` }} />
        <span class="day-strip__frame-label" style={{ left: `${pct(startMin)}%` }}>frame</span>
        <span
          class="day-strip__frame-range"
          style={{ left: `${pct(startMin)}%`, width: `${pct(endMin) - pct(startMin)}%` }}
        >
          {compactClock(props.frameStart)} – {compactClock(props.frameEnd)}
        </span>
      </div>
      {calendarLayout.placed.length > 0 && (
        <>
          <div class="day-strip__caption">
            <span class="day-strip__caption-label">Calendar</span>
          </div>
          <div
            class="day-strip__lane day-strip__lane--calendar"
            aria-label="Calendar"
            style={{ height: `${calendarRows * anchorRowH + 2}px` }}
          >
            {calendarLayout.placed.map(({ item: event, min, length, row, inside, side, text }) => (
              <Fragment key={event.id}>
                <span
                  class={`strip-cal${event.counted ? "" : " strip-cal--skipped"}${
                    event.capacityClass === "work" ? " strip-cal--work" : ""
                  }`}
                  style={{
                    left: `${rawPct(min)}%`,
                    width: `${(length / daySpan) * 100}%`,
                    top: `${2 + row * anchorRowH}px`,
                  }}
                  title={`${event.name} ${display12h(event.start)}`}
                  role="img"
                  aria-label={`${event.name} ${display12h(event.start)} (calendar)`}
                >
                  {inside ? text : null}
                </span>
                {!inside && (
                  <span
                    class={`strip-anchor__label strip-anchor__label--${side}${
                      event.counted ? "" : " strip-anchor__label--not-counted"
                    }`}
                    aria-hidden="true"
                    style={
                      side === "right"
                        ? { left: `calc(${rawPct(min + length)}% + 4px)`, top: `${2 + row * anchorRowH}px` }
                        : { right: `calc(${100 - rawPct(min)}% + 4px)`, top: `${2 + row * anchorRowH}px` }
                    }
                  >
                    {text}
                  </span>
                )}
              </Fragment>
            ))}
          </div>
        </>
      )}
      <div class="day-strip__caption">
        <span class="day-strip__caption-label">Mint</span>
        {props.mintControl}
      </div>
      <div class="day-strip__lane day-strip__lane--mint" aria-label="Mint sessions">
        <span
          class="day-strip__free"
          aria-hidden="true"
          style={{ left: `${pct(startMin)}%`, width: `${pct(endMin) - pct(startMin)}%` }}
        />
        {props.sessions.length === 0 ? (
          <span class="day-strip__empty">Mint off today</span>
        ) : (
          props.sessions.map((session, index) => {
            if (sessionOutside(session)) return null;
            const state = stateOf(session);
            const blocked = state === "blocked";
            const selected = state === "selected";
            const left = pct(clipStart(session));
            const width = Math.max(1.5, pct(clipEnd(session)) - left);
            const pos = runPos.get(session.id) ?? { first: true, last: true };
            return (
              <button
                type="button"
                id={`strip-slot-${session.id}`}
                class={`strip-slot strip-slot--${state}${pos.first ? " strip-slot--run-first" : ""}${
                  pos.last ? " strip-slot--run-last" : ""
                }`}
                style={{ left: `${left}%`, width: `${width}%` }}
                data-slot-id={session.id}
                data-state={state}
                aria-pressed={selected}
                aria-label={`${session.name} ${display12h(session.start)}–${display12h(session.end)} — ${
                  blocked ? "blocked by wall" : selected ? "selected" : "available"
                }`}
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
      <div class="day-strip__caption">
        <span class="day-strip__caption-label">Anchors</span>
        {preview && (
          <span
            class="day-strip__drag-label"
            style={{
              left: `${Math.max(10, Math.min(90, pct(preview.min + (placedAnchors.find((p) => p.anchor.id === preview.id)?.length ?? BLOCK_MIN) / 2)))}%`,
            }}
          >
            {display12h(toHHMM(preview.from))} → {display12h(toHHMM(preview.min))}
          </span>
        )}
        <span class="sr-only" role="status" data-testid="anchor-move-status">
          {announcement}
        </span>
      </div>
      <div
        class="day-strip__lane day-strip__lane--anchors"
        ref={laneRef}
        aria-label="Anchors"
        style={{ height: `${anchorRows * anchorRowH + 2}px` }}
      >
        {placedAnchors.map(({ anchor, min: placedMin, length, row, inside, side, text }) => {
          const dragging = preview?.id === anchor.id;
          const min = dragging ? preview.min : placedMin;
          const movable = isMovable(anchor);
          const widthPct = (length / daySpan) * 100;
          const reason = anchor.reason ?? "";
          return (
            <Fragment key={anchor.id}>
            <button
              type="button"
              class={`strip-anchor${anchor.counted ? "" : " strip-anchor--not-counted"}${
                reason === "skipped" ? " strip-anchor--skipped" : ""
              }${widthPct < 5 ? " strip-anchor--tiny" : ""}${movable ? " strip-anchor--movable" : ""}${
                dragging ? " strip-anchor--dragging" : ""
              }`}
              style={{
                left: `${rawPct(min)}%`,
                width: `${widthPct}%`,
                top: `${2 + row * anchorRowH}px`,
              }}
              data-anchor-id={anchor.id}
              title={`${anchor.name} ${display12h(anchor.start)}${
                movable ? " — drag to move, or use the arrow keys" : ""
              }`}
              aria-label={`${anchor.name} ${display12h(anchor.start)}${
                anchor.counted ? ` · ${anchor.blocks}` : ` — not counted (${anchor.reason})`
              }`}
              onPointerDown={(e) => onAnchorPointerDown(e as unknown as PointerEvent, anchor)}
              onPointerMove={(e) => onAnchorPointerMove(e as unknown as PointerEvent)}
              onPointerUp={(e) => onAnchorPointerUp(e as unknown as PointerEvent)}
              onPointerCancel={(e) => onAnchorPointerCancel(e as unknown as PointerEvent)}
              onKeyDown={(e) => onAnchorKeyDown(e as unknown as KeyboardEvent, anchor)}
              onClick={() => onAnchorClick(anchor.id)}
            >
              {inside ? text : null}
            </button>
            {!inside && (
              <span
                class={`strip-anchor__label strip-anchor__label--${side}${
                  anchor.counted ? "" : " strip-anchor__label--not-counted"
                }`}
                aria-hidden="true"
                style={
                  side === "right"
                    ? { left: `calc(${rawPct(min + length)}% + 4px)`, top: `${2 + row * anchorRowH}px` }
                    : { right: `calc(${100 - rawPct(min)}% + 4px)`, top: `${2 + row * anchorRowH}px` }
                }
              >
                {text}
              </span>
            )}
            </Fragment>
          );
        })}
      </div>
      {hiddenSessions > 0 && (
        <p class="day-strip__note">
          {hiddenSessions} Mint block{hiddenSessions === 1 ? "" : "s"} outside the frame
        </p>
      )}
    </div>
  );
}
