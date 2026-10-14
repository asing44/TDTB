/* U12 Anchors card. Counted anchors render as chips that open the existing
   anchored editor; anchors outside the day frame or skipped today drop into a
   dashed "Not counted today" row with the reason.

   A moved anchor (dragged on the strip, or edited) whose start differs from
   its configured start carries an inline control: "Today only" with Save time
   (persist to the repo config) and Reset (back to the configured start). After
   a save the tile says "Saved". A failed save is shown inline as an alert. */

import { display12h } from "../../model/time";
import { compactClock } from "./compactTime";
import { Tooltip } from "../Tooltip";
import { RowIcon } from "../RowIcon";

/** Where an anchor's start stands against the configured start. */
export interface AnchorTimeState {
  /** Configured start (24h HH:MM) the anchor returns to on Reset. */
  configTime: string | null;
  /** Today's start differs from the configured one. */
  dirty: boolean;
  /** The current start was just persisted to config. */
  saved: boolean;
  saving: boolean;
  error: string | null;
  /** Window anchors save their window-open time; the end does not move. */
  windowOpen?: boolean;
}

export interface AnchorChip {
  id: string;
  name: string;
  time: string;
  blocks: number;
  /** The anchor is skipped for today, so the chip's toggle offers Include. */
  skipped?: boolean;
  timeState?: AnchorTimeState;
}

export interface NotCountedChip {
  id: string;
  name: string;
  time: string;
  reason: string;
  /** The anchor is skipped for today, so the chip's toggle offers Include. */
  skipped?: boolean;
  timeState?: AnchorTimeState;
}

export interface AnchorsCardProps {
  counted: AnchorChip[];
  notCounted: NotCountedChip[];
  onEdit: (id: string) => void;
  onSaveTime?: (id: string) => void;
  onResetTime?: (id: string) => void;
  /** Toggle the same today's override the anchored editor's On/Skip writes. */
  onToggleSkip?: (id: string) => void;
  /** Open the shared anchored editor with its Start field focused. */
  onAdjustTime?: (id: string) => void;
  /** Open the shared anchored editor with its duration stepper focused. */
  onAdjustDuration?: (id: string) => void;
}

/** Per-anchor icon actions: the actions that used to live only inside the
    anchored editor, surfaced on the chip itself. Each calls the same store or
    controller path the editor uses — Skip/Include the editor's Today toggle
    override, Adjust time/duration the editor opened on that field. */
function AnchorActions(props: {
  id: string;
  name: string;
  skipped: boolean;
  onToggleSkip?: (id: string) => void;
  onAdjustTime?: (id: string) => void;
  onAdjustDuration?: (id: string) => void;
}) {
  const { id, name, skipped } = props;
  const skipLabel = skipped ? `Include ${name} today` : `Skip ${name} today`;
  return (
    <div class="anchor-actions" role="group" aria-label={`Actions for ${name}`}>
      <Tooltip
        label={skipped ? "Include today" : "Skip today"}
        detail={
          skipped
            ? "Puts it back in today's plan; source unchanged."
            : "Skips it for today only; reversible, source unchanged."
        }
      >
        <button
          type="button"
          class="anchor-action"
          aria-label={skipLabel}
          aria-pressed={!skipped}
          onClick={() => props.onToggleSkip?.(id)}
        >
          <RowIcon name={skipped ? "eye" : "eye-off"} />
        </button>
      </Tooltip>
      <Tooltip label="Adjust time" detail="Opens the anchor editor at its start time.">
        <button
          type="button"
          class="anchor-action"
          aria-label={`Adjust time for ${name}`}
          onClick={() => props.onAdjustTime?.(id)}
        >
          <RowIcon name="clock" />
        </button>
      </Tooltip>
      <Tooltip label="Adjust duration" detail="Opens the anchor editor at its duration.">
        <button
          type="button"
          class="anchor-action"
          aria-label={`Adjust duration for ${name}`}
          onClick={() => props.onAdjustDuration?.(id)}
        >
          <RowIcon name="blocks" />
        </button>
      </Tooltip>
    </div>
  );
}

function AnchorTimeControl(props: {
  id: string;
  name: string;
  state: AnchorTimeState;
  onSave?: (id: string) => void;
  onReset?: (id: string) => void;
}) {
  const { state } = props;
  if (!state.dirty && !state.saved && !state.error) return null;
  return (
    <div class="anchor-time" data-anchor-time={props.id}>
      <div class="anchor-time__row">
        {state.dirty && <span class="anchor-time__state anchor-time__state--today">Today only</span>}
        {!state.dirty && state.saved && (
          <span class="anchor-time__state anchor-time__state--saved">Saved</span>
        )}
        {state.dirty && state.configTime && (
          <span class="anchor-time__config">Configured {display12h(state.configTime)}</span>
        )}
      </div>
      {state.dirty && (
        <div class="anchor-time__actions">
          <button
            type="button"
            class="btn anchor-time__btn"
            disabled={state.saving}
            onClick={() => props.onSave?.(props.id)}
          >
            {state.saving ? "Saving…" : "Save time"}
          </button>
          <button
            type="button"
            class="btn anchor-time__btn"
            disabled={state.saving}
            onClick={() => props.onReset?.(props.id)}
          >
            Reset
          </button>
        </div>
      )}
      {state.dirty && (
        <div class="anchor-time__hint">
          Saves for every day this anchor runs
          {state.windowOpen ? " · sets the window's open time" : ""}
        </div>
      )}
      {state.error && (
        <div class="field-error anchor-time__error" role="alert">{state.error}</div>
      )}
    </div>
  );
}

export function AnchorsCard(props: AnchorsCardProps) {
  return (
    <section
      class="setup-section setup-section--anchors"
      data-settings-section="anchored"
      aria-labelledby="setup-sec-anchors"
    >
      <div class="setup-section__head">
        <h3 id="setup-sec-anchors">Anchors</h3>
        <p class="setup-section__hint">Click a block to edit it, or drag it along the strip to move it</p>
      </div>
      <div class="setup-section__body">
        <div class="anchor-chips">
          {props.counted.map((anchor) => (
            <div class="anchor-tile" key={anchor.id}>
              <div class="anchor-tile__head">
                <button
                  type="button"
                  class="anchor-chip"
                  onClick={() => props.onEdit(anchor.id)}
                  aria-label={`Edit ${anchor.name} ${display12h(anchor.time)} · ${anchor.blocks} blocks`}
                >
                  <span class="anchor-chip__name">{anchor.name}</span>
                  <span class="anchor-chip__meta">
                    {compactClock(anchor.time)} · {anchor.blocks}
                  </span>
                </button>
                <AnchorActions
                  id={anchor.id}
                  name={anchor.name}
                  skipped={anchor.skipped === true}
                  onToggleSkip={props.onToggleSkip}
                  onAdjustTime={props.onAdjustTime}
                  onAdjustDuration={props.onAdjustDuration}
                />
              </div>
              {anchor.timeState && (
                <AnchorTimeControl
                  id={anchor.id}
                  name={anchor.name}
                  state={anchor.timeState}
                  onSave={props.onSaveTime}
                  onReset={props.onResetTime}
                />
              )}
            </div>
          ))}
        </div>
        <div class="not-counted">
          <span class="not-counted__label">Not counted today:</span>
          {props.notCounted.length === 0 ? (
            <span class="not-counted__empty">none</span>
          ) : (
            props.notCounted.map((anchor) => (
              <span class="not-counted__item" key={anchor.id}>
                <button
                  type="button"
                  class="not-counted__chip"
                  onClick={() => props.onEdit(anchor.id)}
                >
                  {anchor.name} {compactClock(anchor.time)} · {anchor.reason}
                </button>
                <AnchorActions
                  id={anchor.id}
                  name={anchor.name}
                  skipped={anchor.skipped === true}
                  onToggleSkip={props.onToggleSkip}
                  onAdjustTime={props.onAdjustTime}
                  onAdjustDuration={props.onAdjustDuration}
                />
                {anchor.timeState && (
                  <AnchorTimeControl
                    id={anchor.id}
                    name={anchor.name}
                    state={anchor.timeState}
                    onSave={props.onSaveTime}
                    onReset={props.onResetTime}
                  />
                )}
              </span>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
