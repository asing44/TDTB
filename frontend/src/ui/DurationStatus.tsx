/* DurationStatus — the unmistakable save state of a row's duration, derived
   only from data the store already holds:

     Unsaved     a staged draft (nothing committed; gone on Discard/reload)
     Today       a saved today-only override (session override; not tomorrow)
     Remembered  durationSource === "remembered" (durable, survives tomorrow)
     From source everything else: the Todoist/Capacities/tag/preset estimate

   The chip sits beside the duration readout in the collapsed header and in
   the expanded row; DurationSaveActions puts ✓ Save / ↺ Discard icon buttons
   in the collapsed header while a draft is staged, wired to the existing
   controller.saveDraft / discardDraft. Save announces a brief "Saved" tick. */

import { useEffect, useState } from "preact/hooks";
import { useApp } from "./context";
import { draftIsStaged, type AppState } from "../store/store";
import type { Controller } from "../store/controller";
import type { AssignedItem } from "../model/types";
import "./duration-ux.css";

export type DurationSaveState = "unsaved" | "today" | "remembered" | "source";

export function durationSaveState(s: AppState, item: AssignedItem): DurationSaveState {
  if (draftIsStaged(s, item.id)) return "unsaved";
  if (s.overrides[item.id]?.blocks != null) return "today";
  if (item.durationSource === "remembered") return "remembered";
  return "source";
}

const COPY: Record<DurationSaveState, { label: string; title: string }> = {
  unsaved: {
    label: "Unsaved",
    title:
      "Unsaved — a change you have staged but not saved. Save keeps it for today; Discard drops it. It is lost if you reload and does not carry to tomorrow.",
  },
  today: {
    label: "Today",
    title:
      "Saved for today only — this duration applies to today's plan and does not survive tomorrow. Use Remember for next time to keep it.",
  },
  remembered: {
    label: "Remembered",
    title:
      "Remembered — this duration is saved for every future day until you reset it.",
  },
  source: {
    label: "From source",
    title:
      "From source — the estimate comes from the task itself (or its tag, preset, or default). Nothing has been changed; it re-resolves each day.",
  },
};

/** Fired by saveDurationDraft so every mounted status chip for the row (the
    collapsed header and the expanded row) can flash the same confirmation. */
const SAVED_EVENT = "tdtb:duration-saved";

/** Save the staged draft through the existing controller path, then announce
    it. Behaviour is exactly controller.saveDraft(id). */
export function saveDurationDraft(controller: Controller, id: string): void {
  controller.saveDraft(id);
  window.dispatchEvent(new CustomEvent(SAVED_EVENT, { detail: { id } }));
}

const SAVED_MS = 1500;

export function DurationStatus({ s, item }: { s: AppState; item: AssignedItem }) {
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const on = (e: Event) => {
      if ((e as CustomEvent<{ id: string }>).detail?.id !== item.id) return;
      setSaved(true);
      clearTimeout(timer);
      timer = setTimeout(() => setSaved(false), SAVED_MS);
    };
    window.addEventListener(SAVED_EVENT, on);
    return () => {
      window.removeEventListener(SAVED_EVENT, on);
      clearTimeout(timer);
    };
  }, [item.id]);

  const state = durationSaveState(s, item);
  const copy = COPY[state];
  return (
    <span class="dux-status">
      {saved ? (
        <span class="qrow__saved-tick dux-saved" role="status">
          ✓ Saved
        </span>
      ) : (
        <span class={`dux-badge dux-badge--${state}`} title={copy.title} data-state={state}>
          <span class="dux-dot" aria-hidden="true" />
          <span class="dux-badge__text">{copy.label}</span>
        </span>
      )}
    </span>
  );
}

/** ✓ Save / ↺ Discard for the collapsed header; renders nothing unless a
    draft is staged. */
export function DurationSaveActions({ s, item }: { s: AppState; item: AssignedItem }) {
  const { controller } = useApp();
  if (!draftIsStaged(s, item.id)) return null;
  const busy = s.runtimeBusy;
  return (
    <span class="dux-acts">
      <button
        type="button"
        class="dux-icon dux-icon--save"
        aria-label="Save duration"
        title="Save duration — keeps the change for today"
        disabled={busy}
        onClick={() => saveDurationDraft(controller, item.id)}
      >
        <span aria-hidden="true">✓</span>
      </button>
      <button
        type="button"
        class="dux-icon"
        aria-label="Discard change"
        title="Discard change"
        disabled={busy}
        onClick={() => controller.discardDraft(item.id)}
      >
        <span aria-hidden="true">↺</span>
      </button>
    </span>
  );
}
