/* U12 day-frame strip (R20a). One hour strip spans the day frame with a Mint
   lane (30-minute session slots) and an Anchors lane. Dragging across Mint
   slots selects those sessions; slots blocked by a fixed/work wall are not
   selectable. Every interaction has a keyboard equivalent: focus a slot,
   Space toggles it, Arrow keys move between slots. Anchor blocks open the
   existing anchored editor.

   Pure presentation: selection state and the Wall-filtered id sets come from
   DaySetupPanel, so the strip never re-implements the Mint/wall contract. */

import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";
import { display12h, toMinutes } from "../../model/time";
import { compactHour, compactRange } from "./compactTime";

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

  const ticks: number[] = [];
  for (let m = dayStart; m < dayEnd; m += 120) ticks.push(m);

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
  const BLOCK_MIN = 30;
  const rowEnds: number[] = [];
  const placedAnchors = props.anchors
    .map((anchor) => {
      const min = toMinutes(anchor.start);
      const length = anchor.counted ? Math.max(1, anchor.blocks) * BLOCK_MIN : BLOCK_MIN;
      return { anchor, min, length };
    })
    .filter(({ min, length }) => min + length > dayStart && min < dayEnd)
    .sort((a, b) => a.min - b.min)
    .map(({ anchor, min, length }) => {
      let row = rowEnds.findIndex((end) => end - BLOCK_MIN <= min);
      if (row === -1) {
        row = rowEnds.length;
        rowEnds.push(0);
      }
      rowEnds[row] = min + length;
      return { anchor, min, length, row };
    });
  const anchorRows = Math.max(1, rowEnds.length);
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
      <div class="day-strip__axis" aria-hidden="true">
        {ticks.map((m) => (
          <span class="day-strip__tick" style={{ left: `${pct(m)}%` }} key={m}>
            {compactHour(m)}
          </span>
        ))}
      </div>
      <div class="day-strip__frame" aria-hidden="true">
        <span class="day-strip__frame-box" style={{ left: `${pct(startMin)}%`, width: `${pct(endMin) - pct(startMin)}%` }} />
        <span class="day-strip__frame-label" style={{ left: `${pct(startMin)}%` }}>frame</span>
      </div>
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
      </div>
      <div
        class="day-strip__lane day-strip__lane--anchors"
        aria-label="Anchors"
        style={{ height: `${anchorRows * anchorRowH + 2}px` }}
      >
        {placedAnchors.map(({ anchor, min, length, row }) => {
          const widthPct = (length / daySpan) * 100;
          const reason = anchor.reason ?? "";
          return (
            <button
              type="button"
              class={`strip-anchor${anchor.counted ? "" : " strip-anchor--not-counted"}${
                reason === "skipped" ? " strip-anchor--skipped" : ""
              }${widthPct < 5 ? " strip-anchor--tiny" : ""}`}
              style={{
                left: `${rawPct(min)}%`,
                width: `${widthPct}%`,
                top: `${2 + row * anchorRowH}px`,
              }}
              data-anchor-id={anchor.id}
              title={`${anchor.name} ${display12h(anchor.start)}`}
              aria-label={`${anchor.name} ${display12h(anchor.start)}${
                anchor.counted ? ` · ${anchor.blocks}` : ` — not counted (${anchor.reason})`
              }`}
              onClick={() => props.onEditAnchor(anchor.id)}
              key={anchor.id}
            >
              {anchor.counted && widthPct >= 5 ? anchor.name : null}
            </button>
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
