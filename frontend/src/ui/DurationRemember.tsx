/* DurationRemember — "Remember for next time" on the expanded row. A switch
   that is on when the duration is durable (durationSource === "remembered").
   Turning it on calls controller.saveDurationMemory with the duration shown;
   turning it off calls controller.resetDurationMemory (back to the source
   estimate). It calls the controller directly rather than BlockEditor's
   handlers, which are closures inside that component and not exported; the
   eligibility rules mirror BlockEditor (Capacities rows own their duration,
   a row needs a stable identity, minutes must be whole 5-minute steps). */

import { useApp } from "./context";
import { isValidDurationMinutes } from "../store/controller";
import { itemIdentity } from "../adapters/wire";
import type { AppState } from "../store/store";
import type { AssignedItem } from "../model/types";
import "./duration-ux.css";

export function rememberBlockedReason(
  item: AssignedItem,
  minutes: number,
  allDay: boolean,
): string | null {
  if (
    item.source === "capacities" ||
    (typeof item.path === "string" && item.path.startsWith("capacities://"))
  ) {
    return "Duration is owned by Capacities, so it can only be changed for today.";
  }
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
  /** The duration shown (the draft's), in 30-minute blocks. */
  blocks: number;
  allDay: boolean;
  idBase: string;
}) {
  const { controller } = useApp();
  const remembered = item.durationSource === "remembered";
  const mem = s.durationMemory[item.id];
  const pending = mem?.pending ?? false;
  const minutes = Math.round(blocks * 30);
  // Turning it off is always allowed (reset needs no value); turning it on is
  // gated on the same rules as the exact editor's Save duration.
  const blocked = remembered ? null : rememberBlockedReason(item, minutes, allDay);
  const off = s.runtimeBusy || pending || blocked !== null;
  const title =
    blocked ??
    (remembered
      ? "Remembered — applies every day. Turn off to return to the source estimate."
      : `Saves ${minutes} minutes for every future day, not just today.`);
  const errId = `dux-mem-err-${idBase}`;
  return (
    <>
      <label class={`dux-mem${off ? " dux-mem--off" : ""}`} title={title}>
        <input
          type="checkbox"
          role="switch"
          aria-checked={remembered}
          aria-busy={pending}
          aria-describedby={mem?.error ? errId : undefined}
          checked={remembered}
          disabled={off}
          onChange={() =>
            void (remembered
              ? controller.resetDurationMemory(item.id)
              : controller.saveDurationMemory(item.id, minutes))
          }
        />{" "}
        Remember for next time
      </label>
      {mem?.error && (
        <span class="trow__why" id={errId} role="alert">
          {mem.error}
        </span>
      )}
    </>
  );
}
