/* U12 day-frame strip (R20a). One hour strip spans the day frame with a Mint
   lane (30-minute session slots) and an Anchors lane. Dragging across Mint
   slots selects those sessions; slots blocked by a fixed/work wall are not
   selectable. Every interaction has a keyboard equivalent: focus a slot,
   Space toggles it, Arrow keys move between slots. Anchor blocks open the
   existing anchored editor.

   Pure presentation: selection state and the Wall-filtered id sets come from
   DaySetupPanel, so the strip never re-implements the Mint/wall contract. */

import { useState } from "preact/hooks";
import { display12h, toMinutes } from "../../model/time";

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
}

function hhmm(minutes: number): string {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

export function DayStrip(props: DayStripProps) {
  const startMin = toMinutes(props.frameStart);
  const endMin = toMinutes(props.frameEnd);
  const span = Math.max(1, endMin - startMin);
  const pct = (min: number) => Math.max(0, Math.min(100, ((min - startMin) / span) * 100));
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
  for (let m = Math.ceil(startMin / 120) * 120; m <= endMin; m += 120) ticks.push(m);

  // A Mint block wholly outside the frame has no place on the strip; one that
  // straddles a frame edge is clipped to it. Hidden blocks are counted in a note.
  const sessionOutside = (session: StripSession) =>
    toMinutes(session.end) <= startMin || toMinutes(session.start) >= endMin;
  const hiddenSessions = props.sessions.filter(sessionOutside).length;

  // Anchors are absolutely positioned by time inside the lane. Blocks that
  // overlap in time are stacked into sub-rows (greedy interval scheduling with
  // a 45-minute minimum gap) so none of them collide or spill past the frame.
  // An anchor outside the frame is not drawn here; its Not counted chip opens
  // the same editor instead.
  const rowEnds: number[] = [];
  const placedAnchors = props.anchors
    .map((anchor) => ({ anchor, min: toMinutes(anchor.start) }))
    .filter(({ min }) => min >= startMin && min <= endMin)
    .sort((a, b) => a.min - b.min)
    .map(({ anchor, min }) => {
      let row = rowEnds.findIndex((end) => end <= min);
      if (row === -1) {
        row = rowEnds.length;
        rowEnds.push(0);
      }
      rowEnds[row] = min + 120;
      return { anchor, min, row };
    });
  const anchorRows = Math.max(1, rowEnds.length);
  const anchorRowH = 24;

  return (
    <div class="day-strip" data-testid="day-strip">
      <div class="day-strip__axis" aria-hidden="true">
        {ticks.map((m) => (
          <span class="day-strip__tick" style={{ left: `${pct(m)}%` }} key={m}>
            {display12h(hhmm(m))}
          </span>
        ))}
      </div>
      <div class="day-strip__frame" aria-hidden="true">
        <span class="day-strip__frame-label">frame</span>
      </div>
      <div class="day-strip__lane day-strip__lane--mint" aria-label="Mint sessions">
        {props.sessions.length === 0 ? (
          <span class="day-strip__empty">Mint off today</span>
        ) : (
          props.sessions.map((session, index) => {
            if (sessionOutside(session)) return null;
            const blocked = !props.selectableIds.includes(session.id);
            const selected = props.selectedIds.includes(session.id);
            const state = blocked ? "blocked" : selected ? "selected" : "available";
            const left = pct(Math.max(toMinutes(session.start), startMin));
            const width = Math.max(
              1.5,
              pct(Math.min(toMinutes(session.end), endMin)) - left,
            );
            return (
              <button
                type="button"
                id={`strip-slot-${session.id}`}
                class={`strip-slot strip-slot--${state}`}
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
      </div>
      <div
        class="day-strip__lane day-strip__lane--anchors"
        aria-label="Anchors"
        style={{ height: `${anchorRows * anchorRowH + 6}px` }}
      >
        {placedAnchors.map(({ anchor, min, row }) => (
          <button
            type="button"
            class={`strip-anchor${anchor.counted ? "" : " strip-anchor--not-counted"}`}
            style={{ left: `${pct(min)}%`, top: `${3 + row * anchorRowH}px` }}
            data-anchor-id={anchor.id}
            title={`${anchor.name} ${display12h(anchor.start)}`}
            aria-label={`${anchor.name} ${display12h(anchor.start)}${
              anchor.counted ? ` · ${anchor.blocks}` : ` — not counted (${anchor.reason})`
            }`}
            onClick={() => props.onEditAnchor(anchor.id)}
            key={anchor.id}
          >
            {anchor.name}
          </button>
        ))}
      </div>
      {hiddenSessions > 0 && (
        <p class="day-strip__note">
          {hiddenSessions} Mint block{hiddenSessions === 1 ? "" : "s"} outside the frame
        </p>
      )}
    </div>
  );
}
