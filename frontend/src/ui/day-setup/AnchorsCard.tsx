/* U12 Anchors card. Counted anchors render as chips that open the existing
   anchored editor; anchors outside the day frame or skipped today drop into a
   dashed "Not counted today" row with the reason. */

import { display12h } from "../../model/time";

export interface AnchorChip {
  id: string;
  name: string;
  time: string;
  blocks: number;
}

export interface NotCountedChip {
  id: string;
  name: string;
  time: string;
  reason: string;
}

export interface AnchorsCardProps {
  counted: AnchorChip[];
  notCounted: NotCountedChip[];
  onEdit: (id: string) => void;
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
        <p class="setup-section__hint">Click a block on the strip to change time or length</p>
      </div>
      <div class="setup-section__body">
        <div class="anchor-chips">
          {props.counted.map((anchor) => (
            <button
              type="button"
              class="anchor-chip"
              onClick={() => props.onEdit(anchor.id)}
              aria-label={`Edit ${anchor.name} ${display12h(anchor.time)} · ${anchor.blocks} blocks`}
              key={anchor.id}
            >
              <span class="anchor-chip__name">{anchor.name}</span>
              <span class="anchor-chip__meta">
                {display12h(anchor.time)} · {anchor.blocks}
              </span>
            </button>
          ))}
        </div>
        <div class="not-counted">
          <span class="not-counted__label">Not counted today:</span>
          {props.notCounted.length === 0 ? (
            <span class="not-counted__empty">none</span>
          ) : (
            props.notCounted.map((anchor) => (
              <button
                type="button"
                class="not-counted__chip"
                onClick={() => props.onEdit(anchor.id)}
                key={anchor.id}
              >
                {anchor.name} {display12h(anchor.time)} · {anchor.reason}
              </button>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
