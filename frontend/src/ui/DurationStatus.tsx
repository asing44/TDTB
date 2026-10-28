/* DurationStatus — the honest source of a row's duration, derived only from
   data the store already holds:

     Today       a local today-only override (applied at once; not tomorrow)
     Remembered  durationSource === "remembered" (durable, survives tomorrow)
     From source everything else: the Todoist/Capacities/tag/preset estimate

   Local edits apply the moment they are made (no staged/Unsaved state), so
   there is nothing to save. DurationUndo is the single-step reverse of the
   row's last local edit, wired to controller.undoRowEdit. */

import { useApp } from "./context";
import type { AppState } from "../store/store";
import type { AssignedItem } from "../model/types";
import { UndoIcon } from "./RowIcons";
import "./duration-ux.css";

export type DurationSaveState = "today" | "remembered" | "source";

export function durationSaveState(s: AppState, item: AssignedItem): DurationSaveState {
  if (item.durationSource === "remembered") return "remembered";
  if (s.overrides[item.id]?.blocks != null) return "today";
  return "source";
}

const COPY: Record<DurationSaveState, { label: string; title: string }> = {
  today: {
    label: "Today",
    title:
      "Today only — this change applies to today's plan right away and does not carry to tomorrow. Use Remember to keep it.",
  },
  remembered: {
    label: "Remembered",
    title:
      "Remembered — this duration always overrides the source on every day until you forget it.",
  },
  source: {
    label: "From source",
    title:
      "From source — the estimate comes from the task itself (or its tag, preset, or default). Nothing has been changed; it re-resolves each day.",
  },
};

export function DurationStatus({ s, item }: { s: AppState; item: AssignedItem }) {
  const state = durationSaveState(s, item);
  const copy = COPY[state];
  return (
    <span class="dux-status">
      <span class={`dux-badge dux-badge--${state}`} title={copy.title} data-state={state}>
        <span class="dux-dot" aria-hidden="true" />
        <span class="dux-badge__text">{copy.label}</span>
      </span>
    </span>
  );
}

/** ↺ Undo for the last local edit on the row; renders nothing until there is
    one. Single step: the edit before it is not kept. */
export function DurationUndo({ s, item }: { s: AppState; item: AssignedItem }) {
  const { controller } = useApp();
  if (!s.rowUndo[item.id]) return null;
  return (
    <span class="dux-acts">
      <button
        type="button"
        class="dux-icon dux-undo"
        aria-label={`Undo last change to ${item.name}`}
        title="Undo the last change to this row"
        disabled={s.runtimeBusy}
        onClick={() => controller.undoRowEdit(item.id)}
      >
        <UndoIcon />
        <span class="dux-undo__text">Undo</span>
      </button>
    </span>
  );
}
