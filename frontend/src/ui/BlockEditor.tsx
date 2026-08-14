/* BlockEditor — exact start/duration/include controls with keyboard support
   (locked decision 8). Deterministic and free; edits mark the plan dirty and
   trigger revalidation via the controller. */

import { useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { useDialog } from "./useDialog";
import { effectiveBlocks } from "../store/store";
import { blocksLabel } from "../model/time";

export function BlockEditor() {
  const s = useAppState();
  const { controller, store } = useApp();
  const id = s.ui.editorItem;
  const item = s.inputs?.assigned.find((i) => i.id === id);
  const row = s.sequence?.find((r) => r.id === id && r.kind === "work");
  const pinned = s.pendingPinnedRows.some((r) => r.id === id);
  const [start, setStart] = useState(row?.start ?? s.inputs?.time.anchor ?? "09:00");
  const [blocks, setBlocks] = useState(id ? effectiveBlocks(s, id) : 1);
  const close = () =>
    store.dispatch({ type: "UI", patch: { editorItem: null, editorIntent: null } });
  const dialog = useDialog(close);

  if (!id || !item) return null;

  // T12e (brief problem 7): the modal adapts to its caller. ✎ asked for
  // duration — never re-ask for (or touch) the start its caller didn't
  // mention. ⤵ asked for placement — start is the field that matters.
  // null intent = legacy both-fields behaviour (timeline block edit).
  const intent = s.ui.editorIntent;
  const showStart = intent !== "duration" && !item.isRecurring;

  const apply = () => {
    const included = s.overrides[id]?.included ?? true;
    if (blocks !== effectiveBlocks(s, id)) {
      controller.setOverride(id, included, blocks);
    }
    if (blocks === 0 || intent === "duration") {
      close();
      return;
    }
    // T25: recurring rows are duration-shapeable only — their wall time is
    // pinned by the recurrence pattern, never moved from here.
    if (item?.isRecurring) {
      close();
      return;
    }
    if (row) {
      controller.moveRow(id, start);
    } else {
      controller.placeRow(id, start);
    }
    close();
  };

  return (
    <>
      <div class="drawer-backdrop" onClick={close} />
      <div
        class="editor"
        role="dialog"
        aria-modal="true"
        aria-label={`Edit ${item.name}`}
        tabIndex={-1}
        ref={dialog.ref}
        onKeyDown={(e) => {
          dialog.onKeyDown(e as unknown as KeyboardEvent);
          if (e.key === "Enter" && (e.target as HTMLElement).tagName !== "BUTTON") apply();
        }}
      >
        <h3>{item.name}</h3>
        {showStart && (
          <div class="field">
            <label for="editor-start">Start</label>
            <input
              id="editor-start"
              type="time"
              step={900}
              value={start}
              disabled={blocks === 0}
              autofocus={intent === "place"}
              onInput={(e) => setStart((e.target as HTMLInputElement).value)}
            />
          </div>
        )}
        <div class="field">
          <label for="editor-minutes">Duration</label>
          <div class="stepper">
            <button
              onClick={() => setBlocks((b) => Math.max(0, b - 0.5))}
              disabled={blocks <= 0}
              aria-label="Shorter"
            >
              −
            </button>
            <span aria-live="polite">{blocksLabel(blocks)}</span>
            <button onClick={() => setBlocks((b) => b + 0.5)} aria-label="Longer">
              +
            </button>
          </div>
          {/* LD22 amendment (2026-07-24): the exact editor accepts any
              5-minute multiple; steppers keep their 15-minute jumps. Typed
              values snap to the nearest 5. */}
          <input
            id="editor-minutes"
            type="number"
            min={0}
            step={5}
            value={Math.round(blocks * 30)}
            aria-label="Exact minutes (5-minute steps)"
            onInput={(e) => {
              const raw = Number((e.target as HTMLInputElement).value);
              if (!Number.isFinite(raw)) return;
              const m = Math.max(0, Math.round(raw / 5) * 5);
              setBlocks(m / 30);
            }}
          />
        </div>
        <div class="editor__hint">
          {blocks === 0
            ? "All day — included, unscheduled, and uses no capacity."
            : "Today only — never changes the vault assignment or preset."}
        </div>
        <div class="editor__actions">
          {row && pinned && (
            <button
              class="btn"
              onClick={() => {
                controller.resetPlacement(id);
                close();
              }}
            >
              Reset placement
            </button>
          )}
          {row && !pinned && (
            <button
              class="btn"
              onClick={() => {
                controller.unplaceRow(id);
                close();
              }}
            >
              Unplace
            </button>
          )}
          <button class="btn" onClick={close}>
            Cancel
          </button>
          <button class="btn btn--primary" onClick={apply}>
            Apply
          </button>
        </div>
      </div>
    </>
  );
}
