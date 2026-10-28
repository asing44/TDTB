/* DurationRemember — "Remember" on the expanded row: a toggle button next to
   the duration (aria-pressed), not a checkbox. Pressed means the duration is
   durable (durationSource === "remembered") and ALWAYS overrides the source
   (Todoist label / Capacities duration) on every day until forgotten. Pressing
   it on calls controller.saveDurationMemory with the duration shown; pressing
   it off calls controller.resetDurationMemory (back to the source estimate).
   A backend refusal shows the row's existing error chip. Capacities rows are
   eligible: the backend accepts their canonical identity. */

import { useApp } from "./context";
import { isValidDurationMinutes } from "../store/controller";
import { itemIdentity } from "../adapters/wire";
import type { AppState } from "../store/store";
import type { AssignedItem } from "../model/types";
import "./duration-ux.css";

export const REMEMBER_TITLE_OFF =
  "Always use this duration, even if the source changes. Click again to forget.";

export function rememberBlockedReason(
  item: AssignedItem,
  minutes: number,
  allDay: boolean,
): string | null {
  if (!(item.identity ?? itemIdentity(item))) {
    return "This item has no stable identity, so its duration can't be remembered.";
  }
  if (allDay) return "All-day rows have no duration to remember.";
  if (!isValidDurationMinutes(minutes) || minutes <= 0) {
    return "Remembered durations use whole 5-minute steps (e.g. 25m, 45m).";
  }
  return null;
}

export function DurationRemember({
  item,
  s,
  blocks,
  allDay,
  idBase,
}: {
  item: AssignedItem;
  s: AppState;
  /** The duration shown, in 30-minute blocks. */
  blocks: number;
  allDay: boolean;
  idBase: string;
}) {
  const { controller } = useApp();
  const remembered = item.durationSource === "remembered";
  const mem = s.durationMemory[item.id];
  const pending = mem?.pending ?? false;
  const minutes = Math.round(blocks * 30);
  // Forgetting is always allowed (reset needs no value); remembering is gated
  // on the same rules as the exact editor's Save duration.
  const blocked = remembered ? null : rememberBlockedReason(item, minutes, allDay);
  const off = s.runtimeBusy || pending || blocked !== null;
  const title = blocked ?? REMEMBER_TITLE_OFF;
  const errId = `dux-mem-err-${idBase}`;
  return (
    <>
      <button
        type="button"
        class={`dux-mem${remembered ? " dux-mem--on" : ""}${off ? " dux-mem--off" : ""}`}
        title={title}
        aria-pressed={remembered}
        aria-busy={pending}
        aria-describedby={mem?.error ? errId : undefined}
        disabled={off}
        onClick={() =>
          void (remembered
            ? controller.resetDurationMemory(item.id)
            : controller.saveDurationMemory(item.id, minutes))
        }
      >
        <span class="dux-mem__mark" aria-hidden="true">
          {remembered ? "●" : "○"}
        </span>
        Remember
      </button>
      {mem?.error && (
        <span class="trow__why" id={errId} role="alert">
          {mem.error}
        </span>
      )}
    </>
  );
}
