/* ConfirmUnassign — U4: the row's source-unassign control.

   The Todoist row used to carry a "Skip today" button. It was never a skip:
   `runRowBatch(id, "unassign")` clears the task's date in Todoist (or advances
   a recurring task's due occurrence without completing it), so the row leaves
   today's plan *upstream*. The label now says what happens and the control has
   two steps:

   1. "Unassign in Todoist…" / "Skip occurrence in Todoist…" only OPENS the
      inline confirm. Zero adapter calls: nothing is read, staged, or sent.
   2. The confirm panel restates exactly what changes in Todoist, and only its
      own "Yes, unassign in Todoist" button calls the controller. The existing
      batch-of-one path is used unchanged (`runRowBatch(id, "unassign")`).

   Escape, Cancel, a click outside, or focus leaving the control all close the
   panel with zero calls. Capacities and calendar rows never mount this
   component: their slot stays reserved (an empty `.trow__slot`) because a
   Capacities write is out of scope and the assign switch already owns their
   date-scoped drop. */

import { useLayoutEffect, useRef, useState } from "preact/hooks";
import { useApp } from "./context";
import { taskActionDisabledReason, type AppState } from "../store/store";
import type { AssignedItem } from "../model/types";
import "./confirm-unassign.css";

/** The trigger's words carry the real source, so the row never implies a
    local-only skip. The verb distinguishes a recurring row, the way the batch
    bar's "Skip occurrence" label already does; the wire verb is `unassign`
    either way. */
export const UNASSIGN_TRIGGER_LABEL = "Unassign in Todoist…";
export const SKIP_OCCURRENCE_TRIGGER_LABEL = "Skip occurrence in Todoist…";

/** The second click. Named for the source change, never a bare "OK". */
export const UNASSIGN_CONFIRM_LABEL = "Yes, unassign in Todoist";
export const SKIP_OCCURRENCE_CONFIRM_LABEL = "Yes, skip occurrence in Todoist";

/** What the verb does upstream, in the source's own terms (IMP-05 action
    table in app/runtime_actions.py: unassign clears a non-recurring task's
    date, a recurring task's due occurrence advances without completing). */
export const UNASSIGN_EFFECT =
  "Clears this task's due date in Todoist, so Todoist no longer holds it for today. The task itself stays in Todoist.";
export const SKIP_OCCURRENCE_EFFECT =
  "Advances this task's due occurrence in Todoist without completing it, so Todoist no longer holds it for today. The recurrence stays.";

/** Item ids are human names; DOM ids may not be. */
function safeId(id: string): string {
  return id.replace(/[^A-Za-z0-9_-]+/g, "-");
}

export function SourceUnassign({ item, s }: { item: AssignedItem; s: AppState }) {
  const { controller } = useApp();
  const [open, setOpen] = useState(false);
  const [sent, setSent] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = safeId(item.id);
  const recurring = item.isRecurring === true;
  // Same gate the row's other source verbs read: Day Setup confirmed and no
  // batch already applying. The reason is written out, not only a title.
  const why = taskActionDisabledReason(s, "skip");
  const off = s.runtimeBusy || why !== null || sent;
  const triggerLabel = recurring
    ? SKIP_OCCURRENCE_TRIGGER_LABEL
    : UNASSIGN_TRIGGER_LABEL;
  const confirmLabel = recurring
    ? SKIP_OCCURRENCE_CONFIRM_LABEL
    : UNASSIGN_CONFIRM_LABEL;
  const whyId = `why-unassign-${id}`;
  const panelId = `unassign-${id}-confirm`;
  const questionId = `unassign-${id}-question`;

  function close(refocus = true): void {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  }

  // Open: the panel takes focus so the explanation is read before the
  // destructive button is reached (Tab lands on it, Enter alone never can).
  // Focus leaving the control — to another row, the page, or nowhere —
  // cancels the confirm. `focusout` bubbles, so a listener on each half of the
  // control covers the trigger and every button in the panel.
  useLayoutEffect(() => {
    if (!open) return;
    const parts: HTMLElement[] = [];
    if (panel.current) parts.push(panel.current);
    if (trigger.current) parts.push(trigger.current);
    panel.current?.focus();
    const onFocusOut = (e: Event) => {
      const next = (e as FocusEvent).relatedTarget as Node | null;
      if (
        next &&
        parts.some((el) => el === next || el.contains(next))
      ) {
        return;
      }
      setOpen(false);
    };
    for (const el of parts) el.addEventListener("focusout", onFocusOut);
    return () => {
      for (const el of parts) el.removeEventListener("focusout", onFocusOut);
    };
  }, [open]);

  function onKeyDown(e: KeyboardEvent): void {
    if (!open || e.key !== "Escape") return;
    // The confirm is its own state: its Escape never reaches the row's keys.
    e.preventDefault();
    e.stopPropagation();
    close();
  }

  async function confirm(): Promise<void> {
    if (off) return;
    setSent(true);
    close(false);
    try {
      // Unchanged controller path: one row, the journaled unassign batch.
      await controller.runRowBatch(item.id, "unassign");
    } finally {
      setSent(false);
    }
  }

  return (
    <>
      <button
        type="button"
        ref={trigger}
        class="trow__btn unassign__trigger"
        aria-label={triggerLabel}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-describedby={why ? whyId : undefined}
        disabled={off}
        title={why ?? triggerLabel}
        onClick={() => {
          if (off) return;
          if (open) close();
          else setOpen(true);
        }}
        onKeyDown={onKeyDown}
      >
        {triggerLabel}
      </button>
      {why && (
        <span class="sr-only" id={whyId}>
          {why}
        </span>
      )}
      {open && (
        <div
          class="unassign__panel"
          id={panelId}
          ref={panel}
          role="group"
          aria-labelledby={questionId}
          tabIndex={-1}
          onKeyDown={onKeyDown}
        >
          <p class="unassign__q" id={questionId}>
            {recurring
              ? `Skip this occurrence of “${item.name}” in Todoist?`
              : `Unassign “${item.name}” in Todoist?`}
          </p>
          <p class="unassign__what">
            {recurring ? SKIP_OCCURRENCE_EFFECT : UNASSIGN_EFFECT}
          </p>
          <p class="unassign__note">Nothing is saved until you confirm.</p>
          <div class="unassign__acts">
            <button
              type="button"
              class="unassign__yes"
              disabled={off}
              onClick={() => void confirm()}
            >
              {confirmLabel}
            </button>
            <button type="button" class="unassign__cancel" onClick={() => close()}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </>
  );
}
