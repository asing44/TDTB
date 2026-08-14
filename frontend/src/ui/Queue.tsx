/* Queue — the allocator table, T12e redesign (3a spec) → IMP-07. Urgency
   BANDS (Critical / High / Everything else) replace planning-state as the
   primary grouping so critical rows surface in one scan (brief problem 3);
   Scheduled and Excluded today stay as trailing sections, and Dropped today
   (IMP-07 Drop from plan) renders as its own collapsible section. Each row
   is one aligned grid (stripe | name | due | time | actions) so a row's
   controls sit in fixed columns instead of drifting to the far edge (brief
   problem 2).

   IMP-07 final verb model (frozen action table): direct Done and
   Drop from plan on the row; Unassign and Delete live behind a per-row More
   disclosure menu. FEEDBACK-10 (A13): the row-shaping actions (Place at a
   specific time, Unschedule) also moved into More, so the row action cluster
   stays Done / Drop / two shaping icons. Defer is not a product verb and is
   not rendered. The surface is named "Today's work" — "Queue" is never
   user-facing language.

   Band headers are disclosure buttons (contract item 6): keyboard-accessible,
   stateful (session) collapse/expand with row counts; collapsed bands keep
   their header and count visible while hiding rows. FEEDBACK-10 (A11): the
   collapsed state is announced in words (state chip + "N rows hidden"), not
   only a chevron glyph.

   The duration slider renders the day's budget line at the point where
   cumulative spend crosses it (per-row track, model/bands.ts) — the number
   that moves when a slider drags is on the slider itself (brief problem 1).

   All-day (0-block) items stay visible but never occupy capacity (locked
   decision 7). Source is a demoted dot + text under the name, never a
   grouping axis (locked decision 6). All controls keep the 44px floor. */

import { useEffect, useRef, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import {
  effectiveBlocks,
  queueState,
  type AppState,
} from "../store/store";
import type { QueueState } from "../model/types";
import {
  bandedRows,
  bandSpend,
  budgetTotal,
  includedDisplayOrder,
  localSelected,
  trimForState,
} from "../store/allocatorView";
import { blocksLabel, display12h } from "../model/time";
import {
  HALF_BLOCK,
  MAX_BLOCKS,
  MIN_BLOCKS,
  clampHalfBlocks,
} from "../model/allocator";
import {
  BANDS,
  BAND_COLOR,
  stripeColor,
  trackFor,
  trackGradient,
  type BandSpec,
} from "../model/bands";
import { DIRECT_VERBS, MORE_VERBS } from "../model/staging";
import { sourceDetail, typeToken } from "../model/sourceContext";
import { dueLabel, normalizeUrgency } from "../model/urgency";
import type { AssignedItem } from "../model/types";
import { Tooltip } from "./Tooltip";

function sourceDot(item: AssignedItem): string {
  return item.source === "vault" ? "var(--c-projects)" : "var(--c-tasks)";
}

/** Stable id for aria-controls/aria-labelledby pairs — item ids are human
    names with spaces and punctuation, and ids must not contain them. */
function rowId(item: AssignedItem, suffix: string): string {
  return `${suffix}-${item.id.replace(/[^A-Za-z0-9_-]+/g, "-")}`;
}

/** Drop timestamps arrive as ISO ("2026-08-13T08:00:00Z"); render as a
    compact 12-hour time like every other wall-clock readout (user
    preference; FEEDBACK-09/11 24h presentation superseded). */
function droppedTimeLabel(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const hhmm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return display12h(hhmm);
}

/** IMP-07: direct verbs (Done, Drop from plan) render on the row — one click,
    journaled through the staging path. Unassign and Delete live in MoreMenu. */
function StagingVerbs({ item, busy }: { item: AssignedItem; busy: boolean }) {
  const { controller } = useApp();
  const [confirming, setConfirming] = useState<string | null>(null);

  return (
    <>
      {DIRECT_VERBS.map((spec) => {
        const armed = confirming === spec.verb;
        return (
          <button
            key={spec.verb}
            class={`alloc-verb alloc-verb--${spec.verb}${armed ? " alloc-verb--armed" : ""}`}
            disabled={busy}
            title={`${spec.aria}: ${item.name}`}
            aria-label={
              armed
                ? `Confirm ${spec.aria.toLowerCase()}: ${item.name}`
                : `${spec.aria}: ${item.name}`
            }
            onClick={() => {
              if (spec.destructive && !armed) {
                setConfirming(spec.verb);
                return;
              }
              setConfirming(null);
              void controller.stagingAction(spec.verb, item.id);
            }}
            onBlur={() => armed && setConfirming(null)}
          >
            {armed ? "Sure?" : spec.label}
          </button>
        );
      })}
    </>
  );
}

/** IMP-07: per-row More disclosure exposing the frozen staging verbs
    (Unassign, Delete) plus — FEEDBACK-10 (A13) — the row-shaping actions that
    used to crowd the row (Place at a specific time, Unschedule). Menu-button
    pattern: aria-haspopup, aria-expanded, aria-controls; Arrow keys move
    between items; Escape closes and returns focus to the trigger; outside
    click closes. Delete arms to "Sure?" before it fires (permanent). */
function MoreMenu({
  item,
  busy,
  state,
}: {
  item: AssignedItem;
  busy: boolean;
  state: QueueState;
}) {
  const { controller, store } = useApp();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuId = rowId(item, "more");

  const close = (refocus: boolean) => {
    setOpen(false);
    setConfirming(null);
    if (refocus) triggerRef.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent | TouchEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        close(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close(true);
      }
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("touchstart", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("touchstart", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const onMenuKeyDown = (e: KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = Array.from(
      wrapRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [],
    );
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (i < 0) return;
    const next =
      items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length];
    next.focus();
  };

  return (
    <div class="row-more" ref={wrapRef}>
      <Tooltip label="More actions" align="end">
        <button
          ref={triggerRef}
          class="iconbtn row-more__trigger"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={menuId}
          aria-label={`More actions for ${item.name}`}
          disabled={busy}
          onClick={() => {
            setOpen((o) => !o);
            setConfirming(null);
          }}
        >
          ⋯
        </button>
      </Tooltip>
      {open && (
        <div
          class="row-more__menu"
          id={menuId}
          role="menu"
          aria-label={`Actions for ${item.name}`}
          onKeyDown={onMenuKeyDown}
        >
          {state === "needs-placement" && (
            <button
              role="menuitem"
              class="row-more__item"
              aria-label={`Place ${item.name} at a specific time`}
              onClick={() => {
                close(false);
                store.dispatch({
                  type: "UI",
                  patch: { editorItem: item.id, editorIntent: "place" },
                });
              }}
            >
              Place at a specific time
            </button>
          )}
          {state === "scheduled" && !item.isRecurring && (
            <button
              role="menuitem"
              class="row-more__item"
              aria-label={`Unschedule ${item.name}`}
              onClick={() => {
                close(false);
                controller.releasePlacement(item.id);
              }}
            >
              Unschedule
            </button>
          )}
          {MORE_VERBS.map((spec) => {
            const armed = confirming === spec.verb;
            return (
              <button
                key={spec.verb}
                role="menuitem"
                class={`alloc-verb alloc-verb--${spec.verb} row-more__item${armed ? " alloc-verb--armed" : ""}`}
                aria-label={
                  armed
                    ? `Confirm ${spec.aria.toLowerCase()}: ${item.name}`
                    : `${spec.aria}: ${item.name}`
                }
                onClick={() => {
                  if (spec.destructive && !armed) {
                    setConfirming(spec.verb);
                    return;
                  }
                  close(false);
                  void controller.stagingAction(spec.verb, item.id);
                }}
              >
                {armed ? "Sure?" : spec.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Keys-card navigation (3a spec): ↑/↓ move between rows, ←/→ shape the
    focused row's duration, x toggles exclude, ⏎ marks done. Skips when the
    event originates in a control that owns the key (the range input already
    handles arrows; buttons own Enter). */
function rowKeydown(
  e: KeyboardEvent,
  item: AssignedItem,
  s: AppState,
  controller: { setOverride: (id: string, inc: boolean, b: number | null) => void; stagingAction: (v: string, id: string) => Promise<unknown> },
) {
  const target = e.target as HTMLElement;
  const inControl = target.tagName === "INPUT" || target.tagName === "BUTTON";
  const row = (target.closest(".qrow") ?? target) as HTMLElement;
  const included = s.overrides[item.id]?.included ?? true;

  if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !inControl) {
    const rows = Array.from(
      row.closest(".queue")?.querySelectorAll<HTMLElement>(".qrow") ?? [],
    );
    const i = rows.indexOf(row);
    const next = rows[e.key === "ArrowDown" ? i + 1 : i - 1];
    if (next) next.focus();
    e.preventDefault();
  } else if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && !inControl) {
    if (!included) return;
    const delta = e.key === "ArrowRight" ? HALF_BLOCK : -HALF_BLOCK;
    controller.setOverride(item.id, included, clampHalfBlocks(effectiveBlocks(s, item.id) + delta));
    e.preventDefault();
  } else if (e.key === "x" && !inControl) {
    controller.setOverride(item.id, !included, s.overrides[item.id]?.blocks ?? null);
    e.preventDefault();
  } else if (e.key === "Enter" && !inControl) {
    void controller.stagingAction("done", item.id);
    e.preventDefault();
  }
}

function Row({
  item,
  s,
  cumBefore,
  flagged,
}: {
  item: AssignedItem;
  s: AppState;
  cumBefore: number;
  flagged: boolean;
}) {
  const { controller, store } = useApp();
  const state = queueState(s, item.id);
  const urgency = normalizeUrgency(item);
  const due = dueLabel(item.deadline, s.inputs?.validDate ?? "");
  const blocks = effectiveBlocks(s, item.id);
  const included = s.overrides[item.id]?.included ?? true;
  const overridden = s.overrides[item.id]?.blocks != null;
  const scheduledRow = s.sequence?.find((r) => r.id === item.id && r.kind === "work");
  const track = trackFor(cumBefore, blocks, budgetTotal(s), MAX_BLOCKS);

  return (
    <div
      class={`qrow ${state === "excluded" ? "qrow--excluded" : ""} ${state === "background" ? "qrow--background" : ""} ${flagged ? "qrow--flagged" : ""}`}
      tabIndex={0}
      onKeyDown={(e) => rowKeydown(e as unknown as KeyboardEvent, item, s, controller)}
    >
      <span class="qrow__stripe" style={{ background: stripeColor(item) }} />
      <div class="qrow__body">
        <div class="qrow__nameline">
          <span class="qrow__name" title={item.path ?? item.name}>
            {item.name}
          </span>
        </div>
        {/* The would-drop flag rides the source line, not the name line: on
            the name line it stole width from a 2-line-clamped title, so a row
            gaining or losing the flag mid-drag changed its own height and
            shoved everything below it. */}
        <span class="qrow__source" title={item.path ?? undefined}>
          {/* The kind of thing leads the line as a coloured chip — scanning 19
              rows for "which of these are projects" was a read-every-word job
              when the type was prose in the middle of the source string. */}
          {typeToken(item) && (
            <span class={`qrow__type qrow__type--${typeToken(item)}`}>{typeToken(item)}</span>
          )}
          <span class="qrow__dot" style={{ background: sourceDot(item) }} />
          {item.source}
          {sourceDetail(item) ? ` · ${sourceDetail(item)}` : ""}
          {scheduledRow && ` · ${display12h(scheduledRow.start)}`}
          {item.isRecurring && item.scheduledStart && ` · fixed · recurring · ${display12h(item.scheduledStart)}`}
          {/* A zero-duration row stays exactly where it is and says what it
              became. Previously it jumped to a trailing All-day section the
              moment the slider hit bottom. */}
          {state === "background" && (
            <span class="qrow__allday" title="All day — included, unscheduled, uses no capacity">
              all day
            </span>
          )}
          {flagged && <span class="qrow__drop">would drop</span>}
        </span>
      </div>
      <div class="qrow__due">
        {urgency && (urgency.tier === "crit" || urgency.tier === "high") && (
          <span class={`badge badge--u${urgency.tier}`}>{urgency.text}</span>
        )}
        {due && (
          <span
            class={due.tone ? `due due--${due.tone}` : "due"}
            title={item.deadline ?? undefined}
          >
            {due.text}
          </span>
        )}
      </div>
      <div class="qrow__time">
        {included ? (
          <>
            {/* The slider's own notch is a 30-minute block; these are the
                half-block trim the slider can't express without becoming
                twice as twitchy to drag. They snap to the HALF-block grid —
                routing them through clampBlocks made − a no-op at every
                integer and turned + into a 30-minute jump. */}
            <Tooltip
              label={blocks <= HALF_BLOCK ? "All day (no capacity)" : "−15 min"}
            >
              <button
                class="iconbtn alloc-step"
                aria-label={
                  blocks <= HALF_BLOCK
                    ? `Make ${item.name} all day`
                    : `15 minutes less for ${item.name}`
                }
                disabled={blocks <= MIN_BLOCKS}
                onClick={() =>
                  controller.setOverride(item.id, included, clampHalfBlocks(blocks - HALF_BLOCK))
                }
              >
                −
              </button>
            </Tooltip>
            <span class="alloc-track" style={{ "--trackbg": trackGradient(track) }}>
              <input
                type="range"
                class="alloc-track__input"
                /* Half-block notches, all the way down to All day: 30min →
                   15min → All day. The row no longer relocates when it hits
                   zero (see bandedRows), so the bottom of the track is a
                   place you can safely drag to. Same grid as the ± steppers,
                   so the two controls can't disagree. */
                min={MIN_BLOCKS}
                max={MAX_BLOCKS}
                step={HALF_BLOCK}
                value={blocks}
                aria-label={`${item.name} duration in 15-minute steps`}
                /* FEEDBACK-10 (A08): the spoken value names WHERE the duration
                   came from — a session memory override or the source. */
                aria-valuetext={`${blocksLabel(blocks)} (${overridden ? "memory" : "source"})`}
                onInput={(e) =>
                  controller.setOverride(
                    item.id,
                    included,
                    clampHalfBlocks(Number((e.target as HTMLInputElement).value)),
                    { defer: true },
                  )
                }
              />
              {track.markPct != null && (
                <span class="alloc-track__mark" style={{ left: `${track.markPct}%` }} />
              )}
            </span>
            <Tooltip label="+15 min">
              <button
                class="iconbtn alloc-step"
                aria-label={`15 minutes more for ${item.name}`}
                disabled={blocks >= MAX_BLOCKS}
                onClick={() =>
                  controller.setOverride(item.id, included, clampHalfBlocks(blocks + HALF_BLOCK))
                }
              >
                +
              </button>
            </Tooltip>
            <span
              class={`alloc-track__value ${track.pastBudget ? "alloc-track__value--over" : ""}`}
              aria-live="polite"
            >
              {blocksLabel(blocks)}
              {overridden ? "*" : ""}
            </span>
            {/* FEEDBACK-10 (A08): duration-memory state is a visible chip —
                "memory" when a session override holds the value, "source"
                otherwise. The * on the value stays as a redundant signal. */}
            <span
              class={`qrow__src-tag qrow__src-tag--${overridden ? "memory" : "source"}`}
            >
              {overridden ? "memory" : "source"}
            </span>
          </>
        ) : (
          <span class="qrow__excluded-note">excluded today</span>
        )}
      </div>
      <div class="qrow__actions">
        <Tooltip label={included ? "Exclude today" : "Include today"}>
          <button
            class="iconbtn"
            aria-label={`${included ? "Exclude" : "Include"} ${item.name} today`}
            onClick={() => controller.setOverride(item.id, !included, s.overrides[item.id]?.blocks ?? null)}
          >
            {included ? "⊘" : "＋"}
          </button>
        </Tooltip>
        {/* T7: fine shaping on EVERY included row — the slider's 30-minute
            notches can't express 15/5-minute shaping. T12e: this editor is
            duration-only now; placement is its own More-menu action. */}
        {included && (
          <Tooltip label="Exact duration">
            <button
              class="iconbtn"
              aria-label={`Exact duration for ${item.name}`}
              onClick={() =>
                store.dispatch({
                  type: "UI",
                  patch: { editorItem: item.id, editorIntent: "duration" },
                })
              }
            >
              ✎
            </button>
          </Tooltip>
        )}
        {/* FEEDBACK-10 (A13): the row-shaping actions (Place, Unschedule)
            moved into the More menu — the row keeps Done/Drop and the two
            shaping icons, and the cluster no longer overlaps. */}
        <span class="qrow__divider" />
        <StagingVerbs item={item} busy={s.runtimeBusy} />
        <MoreMenu item={item} busy={s.runtimeBusy} state={state} />
      </div>
    </div>
  );
}

function BandHeader({
  band,
  spend,
  count,
  open,
  onToggle,
  controlsId,
}: {
  band: BandSpec;
  spend: number;
  count: number;
  open: boolean;
  onToggle: () => void;
  controlsId: string;
}) {
  const over = spend - band.share;
  const rows = `${count} row${count === 1 ? "" : "s"}`;
  return (
    <button
      type="button"
      class={`band${open ? "" : " band--collapsed"}`}
      aria-expanded={open}
      aria-controls={controlsId}
      /* FEEDBACK-10 (A11): the state and hidden count are announced, not
         implied by a chevron glyph. */
      aria-label={`Band ${band.label}, ${open ? "expanded" : "collapsed"}, ${rows}${open ? "" : " hidden"}, activate to ${open ? "collapse" : "expand"}`}
      onClick={onToggle}
    >
      <span class="band__tick" />
      <span class="band__label">{band.label}</span>
      <span class="band__note">{band.note}</span>
      <span class="band__rule" />
      <span class="band__count">{rows}{open ? "" : " hidden"}</span>
      <span class="band__bar">
        <span
          class="band__bar-fill"
          style={{
            width: `${Math.min(100, band.share > 0 ? (spend / band.share) * 100 : 100)}%`,
            background: over > 0 ? "var(--c-overflow)" : BAND_COLOR[band.key],
          }}
        />
      </span>
      <span class="band__share">
        {spend} / {band.share} blk
      </span>
      <span class={`band__over ${over > 0 ? "band__over--over" : ""}`}>
        {over > 0 ? `${over} over share` : "within share"}
      </span>
      <span class={`band__state${open ? "" : " band__state--closed"}`}>
        {open ? "expanded" : "collapsed"}
      </span>
      <span class="band__chevron" aria-hidden="true">{open ? "▾" : "▸"}</span>
    </button>
  );
}

function DroppedHeader({
  count,
  open,
  onToggle,
  controlsId,
}: {
  count: number;
  open: boolean;
  onToggle: () => void;
  controlsId: string;
}) {
  const rows = `${count} row${count === 1 ? "" : "s"}`;
  return (
    <button
      type="button"
      class={`band band--drop${open ? "" : " band--collapsed"}`}
      aria-expanded={open}
      aria-controls={controlsId}
      aria-label={`Dropped today, ${open ? "expanded" : "collapsed"}, ${rows}${open ? "" : " hidden"}, activate to ${open ? "collapse" : "expand"}`}
      onClick={onToggle}
    >
      <span class="band__tick" />
      <span class="band__label">Dropped today</span>
      <span class="band__note">excluded from today — eligible again tomorrow</span>
      <span class="band__rule" />
      <span class="band__count">{rows}{open ? "" : " hidden"}</span>
      <span class={`band__state${open ? "" : " band__state--closed"}`}>
        {open ? "expanded" : "collapsed"}
      </span>
      <span class="band__chevron" aria-hidden="true">{open ? "▾" : "▸"}</span>
    </button>
  );
}

export function Queue() {
  const s = useAppState();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  if (!s.inputs) return null;

  const toggle = (key: string) =>
    setCollapsed((c) => ({ ...c, [key]: !(c[key] ?? false) }));
  const isOpen = (key: string) => !(collapsed[key] ?? false);

  const items = s.inputs.assigned;
  if (items.length === 0) {
    return (
      <section class="queue" aria-label="Today's work">
        <div class="center-note">
          No assigned items today. Assignment happens upstream — Obsidian
          `daily-assigned` and Todoist Today. A quiet day is a valid day.
        </div>
      </section>
    );
  }

  const groups = bandedRows(s);
  const needsPlacement = groups.crit.length + groups.high.length + groups.else.length;
  const dropped = s.inputs.droppedToday ?? [];

  // One cumulative-spend walk shared with the rail (allocatorView), so the
  // budget line lands on exactly one row's track.
  const ordered = includedDisplayOrder(s);
  const cumBefore = new Map<string, number>();
  let cum = 0;
  for (const r of ordered) {
    cumBefore.set(r.id, cum);
    cum += r.blocks;
  }
  const trim = trimForState(s);
  const flagged = new Set(trim.drop);

  // FEEDBACK-10 (A10): one scan-distance readout of the day's balance — over
  // capacity gets a strong overflow line above the bands; a balanced day
  // stays clean (the rail and band bars carry the quiet state).
  const selected = localSelected(s);
  const budget = budgetTotal(s);
  const over = Math.max(0, selected - budget);

  const row = (i: AssignedItem) => (
    <Row
      key={i.id}
      item={i}
      s={s}
      cumBefore={cumBefore.get(i.id) ?? cum}
      flagged={flagged.has(i.id)}
    />
  );

  return (
    <section class="queue" aria-label="Today's work">
      <p class="queue__subtractive">
        Every digest-assigned row starts selected. Remove or trim what will not fit;
        chosen task effort stays additive until Send.
      </p>
      <div class="queue__cols" aria-hidden="true">
        <span />
        <span>Needs placement · {needsPlacement}</span>
        <span>Due</span>
        <span>Time today · budget line</span>
        <span />
      </div>
      {over > 0 && (
        <p class="queue__remaining queue__remaining--over" role="status">
          {selected} blk selected of {budget} capacity - {over} over
        </p>
      )}
      {BANDS.map((band) => {
        const rows = groups[band.key];
        const open = isOpen(band.key);
        const regionId = `band-${band.key}`;
        return (
          <div
            key={band.key}
            class="queue__band"
            style={{ "--band-color": BAND_COLOR[band.key] }}
          >
            <BandHeader
              band={band}
              spend={bandSpend(s, band.key)}
              count={rows.length}
              open={open}
              onToggle={() => toggle(band.key)}
              controlsId={regionId}
            />
            {open && (
              <div id={regionId} class="queue__band-rows">
                {rows.map(row)}
                {rows.length === 0 && <div class="queue__empty">nothing here</div>}
              </div>
            )}
          </div>
        );
      })}
      <div class="queue__band queue__band--drop">
        <DroppedHeader
          count={dropped.length}
          open={isOpen("dropped")}
          onToggle={() => toggle("dropped")}
          controlsId="queue-dropped"
        />
        {isOpen("dropped") && (
          <ul id="queue-dropped" class="queue__dropped">
            {dropped.length === 0 && <li class="queue__empty">nothing here</li>}
            {dropped.map((d) => (
              <li key={d.identity} class="queue__dropped-row">
                <span class="queue__dropped-name">{d.name}</span>
                {d.droppedAt && (
                  <span class="queue__dropped-at" title={d.droppedAt}>
                    dropped {droppedTimeLabel(d.droppedAt)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      <h2 class="queue__section">Scheduled ({groups.scheduled.length})</h2>
      {groups.scheduled.map(row)}
      <h2 class="queue__section">Excluded today ({groups.excluded.length})</h2>
      {groups.excluded.map(row)}
    </section>
  );
}
