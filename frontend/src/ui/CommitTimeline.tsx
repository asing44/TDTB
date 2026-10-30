/* CommitTimeline — the Commit step's single timeline card: Calendar / Anchors
   / Mint / Tasks & habits across today's day frame, showing the FINAL
   sequenced placements as solid blocks. Pure projection of existing state:
   the same selectors Already placed uses plus the staged sequence rows. */

import { useAppState } from "./context";
import { effectiveAnchoredBlocks, selectDayFrame, type AppState } from "../store/store";
import { displayAnchored } from "../model/calendarEvents";
import { anchoredBlocks, anchoredOverrideOf } from "../model/anchored";
import { explicitMintCapMinutes, initialMintSessionIds } from "../model/mint";
import { calendarWalls } from "../model/overflow";
import { normalizeUrgency } from "../model/urgency";
import { compactHour } from "./day-setup/compactTime";
import { compactDuration, toMinutes } from "../model/time";
import "./commit-r3.css";

export type PlacedKind = "critical" | "room";

export interface PlacedBlock {
  id: string;
  name: string;
  start: string;
  end: string;
  lane: "calendar" | "anchors" | "mint" | "tasks";
  kind?: PlacedKind;
}

export interface Unplaced {
  name: string;
  minutes: number;
}

/** Final sequenced work rows, tiered critical (Todoist p1) or when-there's-room. */
export function placedBlocks(s: AppState): { blocks: PlacedBlock[]; unplaced: Unplaced[] } {
  if (!s.inputs) return { blocks: [], unplaced: [] };
  const frame = selectDayFrame(s)!;
  const blocks: PlacedBlock[] = [];

  const effective = effectiveAnchoredBlocks(s);
  const shown = displayAnchored(effective, { sessionCalendars: true });
  for (const row of shown) {
    if (row.start == null) continue;
    const override = anchoredOverrideOf(row, s.daySetup.anchored[row.id]);
    if (!override.on || override.skipToday) continue;
    if (row.kind === "calendar") {
      if (row.durationMin <= 0 || (row.capacityClass ?? "fixed") === "ignored") continue;
      const end = toMinutes(row.start) + row.durationMin;
      blocks.push({
        id: row.id,
        name: row.name,
        start: row.start,
        end: `${String(Math.floor(end / 60)).padStart(2, "0")}:${String(end % 60).padStart(2, "0")}`,
        lane: "calendar",
      });
    } else {
      const span = anchoredBlocks(row, override) * 30;
      const end = toMinutes(override.time ?? row.start) + span;
      blocks.push({
        id: row.id,
        name: row.name,
        start: override.time ?? row.start,
        end: `${String(Math.floor(end / 60)).padStart(2, "0")}:${String(end % 60).padStart(2, "0")}`,
        lane: "anchors",
      });
    }
  }

  const sessions = s.inputs.daySemantics.mintSessions ?? [];
  const ids = initialMintSessionIds(
    sessions,
    s.daySetup.schedulable?.minting,
    explicitMintCapMinutes(s.daySetup, s.inputs.daySemantics.effectiveAllotmentMinutes),
    frame.start,
    calendarWalls(effective),
  );
  for (const m of sessions.filter((x) => ids.includes(x.id))) {
    blocks.push({ id: m.id, name: m.name, start: m.start, end: m.end, lane: "mint" });
  }

  const work = (s.sequence ?? []).filter((r) => r.kind === "work");
  const placedIds = new Set(work.map((r) => r.id));
  for (const r of work) {
    const item = s.inputs.assigned.find((a) => a.id === r.id);
    const tier = item ? normalizeUrgency(item)?.tier : null;
    blocks.push({
      id: r.id,
      name: item?.name ?? r.id,
      start: r.start,
      end: r.end,
      lane: "tasks",
      kind: tier === "crit" ? "critical" : "room",
    });
  }
  const unplaced = s.sequence
    ? s.inputs.assigned
        .filter((a) => a.blocks > 0 && !placedIds.has(a.id))
        .map((a) => ({ name: a.name, minutes: a.blocks * 30 }))
    : [];
  return { blocks, unplaced };
}

const LANES: Array<{ key: PlacedBlock["lane"]; label: string }> = [
  { key: "calendar", label: "Calendar" },
  { key: "anchors", label: "Anchors" },
  { key: "mint", label: "Mint" },
  { key: "tasks", label: "Tasks & habits" },
];

export function CommitTimeline() {
  const s = useAppState();
  if (!s.inputs) return null;
  const frame = selectDayFrame(s);
  if (!frame) return null;
  const { blocks, unplaced } = placedBlocks(s);
  const startMin = toMinutes(frame.start);
  const span = Math.max(1, toMinutes(frame.end) - startMin);
  const pct = (hhmm: string) =>
    Math.max(0, Math.min(100, ((toMinutes(hhmm) - startMin) / span) * 100));
  const firstHour = Math.ceil(startMin / 60);
  const ticks: number[] = [];
  for (let h = firstHour; h * 60 < startMin + span; h += 1) ticks.push(h);

  return (
    <section id="commit-timeline" class="commit-timeline" aria-label="Where today lands">
      <div class="commit-timeline__axis" aria-hidden="true">
        {ticks.map((h) => (
          <span key={h} style={{ left: `${((h * 60 - startMin) / span) * 100}%` }}>
            {compactHour(h * 60)}
          </span>
        ))}
      </div>
      {LANES.map((lane) => (
        <div class="commit-lane" key={lane.key} data-lane={lane.key}>
          <div class="commit-lane__label">{lane.label}</div>
          <div class="commit-lane__track">
            {blocks
              .filter((b) => b.lane === lane.key)
              .map((b) => (
                <span
                  key={`${lane.key}-${b.id}`}
                  class={`commit-block commit-block--${b.kind ?? lane.key}`}
                  style={{ left: `${pct(b.start)}%`, width: `${Math.max(0.8, pct(b.end) - pct(b.start))}%` }}
                  title={`${b.name} · ${b.start}–${b.end}`}
                >
                  {b.name}
                </span>
              ))}
          </div>
        </div>
      ))}
      <div class="commit-legend">
        <span class="commit-legend__keys">
          <span data-k="critical">Critical</span>
          <span data-k="fixed">Fixed</span>
          <span data-k="room">When there's room</span>
          <span data-k="habit">Habits</span>
          <span data-k="mint">Mint</span>
        </span>
        {unplaced.length > 0 && (
          <span class="commit-legend__miss">
            Didn't fit: <strong>{unplaced[0].name}</strong> ({compactDuration(unplaced[0].minutes)})
            {unplaced.length > 1 ? ` +${unplaced.length - 1} more` : ""} · stays in Todoist
          </span>
        )}
      </div>
    </section>
  );
}
