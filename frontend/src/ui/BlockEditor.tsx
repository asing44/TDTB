/* BlockEditor — exact start/duration/include controls with keyboard support
   (locked decision 8). Deterministic and free; edits mark the plan dirty and
   trigger revalidation via the controller.

   Duration-memory MVP (2026-08-17): the exact editor also owns the explicit
   durable actions. "Save duration" applies a STRICTLY validated value (whole
   minutes, >= 0, 5-minute grid — never rounded, truncated, or snapped) via
   ONE token-guarded non-billed mutation and claims success only after the
   response; "Reset duration" clears the remembered value through ONE mutation
   and applies the returned source fallback. Pending and error states are
   exposed with aria-busy and role="alert". Plain Apply keeps its existing
   session-only behavior (LD22 5-minute snapping included). */

import { useEffect, useRef, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { useDialog } from "./useDialog";
import { effectiveBlocks } from "../store/store";
import { isValidDurationMinutes } from "../store/controller";
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
  /** Exact minutes as typed — kept verbatim so the durable save can apply
      strict 5-minute validation instead of the Apply path's snapping. */
  const [rawMinutes, setRawMinutes] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const close = () =>
    store.dispatch({ type: "UI", patch: { editorItem: null, editorIntent: null } });
  const dialog = useDialog(close);

  // The mutation lifecycle is store-driven: pending disables the actions,
  // then success closes the editor and failure surfaces the server error.
  const mem = s.durationMemory[id ?? ""];
  const busy = mem?.pending ?? false;
  const wasPending = useRef(false);
  useEffect(() => {
    const st = s.durationMemory[id ?? ""];
    if (st?.pending) {
      wasPending.current = true;
      return;
    }
    if (wasPending.current) {
      wasPending.current = false;
      if (st?.error) setSaveError(st.error);
      else close();
    }
  }, [id, s.durationMemory[id ?? ""]?.pending, s.durationMemory[id ?? ""]?.error]);

  // Reopening the editor for another row resets the local save surface.
  useEffect(() => {
    setRawMinutes(null);
    setSaveError(null);
    wasPending.current = false;
  }, [id]);

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

  /** The value a durable save would persist: the exact typed minutes when the
      user typed them, else the stepper/snapped blocks in minutes. */
  const minutesNow = (): number => {
    if (rawMinutes != null && rawMinutes.trim() !== "") {
      const n = Number(rawMinutes);
      return Number.isFinite(n) ? n : Number.NaN;
    }
    return Math.round(blocks * 30);
  };

  const saveDuration = () => {
    const m = minutesNow();
    if (!isValidDurationMinutes(m)) {
      setSaveError(
        "Duration must be whole minutes in 5-minute steps (e.g. 45, 60, 90).",
      );
      return;
    }
    setSaveError(null);
    void controller.saveDurationMemory(id, m);
  };

  const resetDuration = () => {
    setSaveError(null);
    void controller.resetDurationMemory(id);
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
              onClick={() => {
                setRawMinutes(null);
                setBlocks((b) => Math.max(0, b - 0.5));
              }}
              disabled={blocks <= 0}
              aria-label="Shorter"
            >
              −
            </button>
            <span aria-live="polite">{blocksLabel(blocks)}</span>
            <button
              onClick={() => {
                setRawMinutes(null);
                setBlocks((b) => b + 0.5);
              }}
              aria-label="Longer"
            >
              +
            </button>
          </div>
          {/* LD22 amendment (2026-07-24): the exact editor accepts any
              5-minute multiple; steppers keep their 15-minute jumps. Typed
              values snap to the nearest 5 for the SESSION Apply path — the
              explicit Save path validates the typed value strictly and never
              snaps (duration-memory MVP). */}
          <input
            id="editor-minutes"
            type="number"
            min={0}
            step={5}
            value={rawMinutes ?? String(Math.round(blocks * 30))}
            aria-label="Exact minutes (5-minute steps)"
            onInput={(e) => {
              const raw = (e.target as HTMLInputElement).value;
              setRawMinutes(raw);
              const num = Number(raw);
              if (!Number.isFinite(num)) return;
              const m = Math.max(0, Math.round(num / 5) * 5);
              setBlocks(m / 30);
            }}
          />
        </div>
        <div class="editor__hint">
          {blocks === 0
            ? "All day — included, unscheduled, and uses no capacity."
            : "Today only — never changes the vault assignment or preset."}
        </div>
        {saveError && (
          <p class="editor__error" role="alert">
            {saveError}
          </p>
        )}
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
          <button
            class="btn"
            onClick={saveDuration}
            disabled={busy}
            aria-busy={busy}
          >
            Save duration
          </button>
          <button
            class="btn"
            onClick={resetDuration}
            disabled={busy}
            aria-busy={busy}
          >
            Reset duration
          </button>
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
