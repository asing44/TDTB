/* Planning 2a anchor popover — the 300px card that opens under a selected
   anchor block. Starts moves today's start through the existing override path
   (never the config); Window is the anchor's own window and is read-only;
   Count today is the existing skip/on override; Edit items opens the shared
   anchored editor; Reset to default clears today's start; and Save as default
   is the existing config persist, offered only once today's start differs.

   Placement is the shared viewport-fitting math (popoverPlacement.ts): the
   panel is position:fixed under the block, clamped inside the viewport, and
   re-placed on resize/scroll. Escape or a click outside (but not on a block)
   closes it and returns focus to the originating block. A cleared or partial
   Starts value is held locally and never persisted. */

import { useEffect, useRef, useState } from "preact/hooks";
import { display12h, isValidHHMM } from "../../model/time";
import { computePopoverPlacement } from "../popoverPlacement";

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

export interface AnchorPopoverAnchor {
  id: string;
  name: string;
  /** Today's start, 24h HH:MM. */
  start: string;
  /** The anchor's window end, 24h HH:MM (read-only). */
  end: string;
  blocks: number;
  /** Counting today (on and not skipped). */
  counted: boolean;
  timeState: AnchorTimeState;
}

export interface AnchorPopoverProps {
  anchor: AnchorPopoverAnchor;
  /** Today-only start move (the strip's drag/arrow path). */
  onMoveTime: (id: string, time: string) => void;
  /** The existing skip-today override. */
  onToggleCount: (id: string) => void;
  /** Opens the shared anchored editor. */
  onEdit: (id: string) => void;
  /** Clears today's start override. */
  onResetDefault: (id: string) => void;
  /** Persists today's start to the config (existing saveAnchoredStart path). */
  onSaveDefault: (id: string) => void;
  onClose: () => void;
}

/** The mockup's popover width. */
const POPOVER_WIDTH = 300;

export function AnchorPopover(props: AnchorPopoverProps) {
  const { anchor } = props;
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ top: 0, left: 0, maxHeight: 0 });
  // The Starts field is held locally: a cleared or partially typed time must
  // never flow into today's override (and so into the server as time:"").
  const [startValue, setStartValue] = useState(anchor.start);
  const [startInvalid, setStartInvalid] = useState(false);
  // The last time actually pushed to the move handler, so committing on both
  // change and blur cannot send it twice.
  const lastSent = useRef(anchor.start);
  // The latest close handler, so the document listeners subscribe once.
  const closeRef = useRef(props.onClose);
  closeRef.current = props.onClose;
  // The originating block id, so Escape / an outside click can hand focus back
  // to the block the popover opened from.
  const anchorIdRef = useRef(anchor.id);
  anchorIdRef.current = anchor.id;
  const closePopover = () => {
    closeRef.current();
    document
      .querySelector<HTMLElement>(`[data-anchor-id="${anchorIdRef.current}"]`)
      ?.focus();
  };

  // Re-sync the field when the anchor or its persisted start changes (Reset,
  // a config save, or a strip move), so the input never drifts from the value.
  useEffect(() => {
    setStartValue(anchor.start);
    setStartInvalid(false);
    lastSent.current = anchor.start;
  }, [anchor.id, anchor.start]);

  /** Push a valid HH:MM to the move handler once; refuse anything else. */
  const commitStart = (raw: string) => {
    if (!isValidHHMM(raw)) {
      setStartInvalid(true);
      return;
    }
    setStartInvalid(false);
    if (raw === lastSent.current) return;
    lastSent.current = raw;
    props.onMoveTime(anchor.id, raw);
  };

  // Place under the block with the shared viewport math; re-place on resize.
  useEffect(() => {
    const place = () => {
      const rect = document
        .querySelector(`[data-anchor-id="${anchor.id}"]`)
        ?.getBoundingClientRect();
      const height = ref.current?.getBoundingClientRect().height ?? 0;
      const placed = computePopoverPlacement(
        rect ?? { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 },
        { width: window.innerWidth, height: window.innerHeight },
        { width: POPOVER_WIDTH, height },
        // A dropdown under the block, like the mockup; the math lifts it above
        // only when the panel cannot fit below.
        "down",
      );
      setPos({ top: placed.top, left: placed.left, maxHeight: placed.maxHeight });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [anchor.id, anchor.start, anchor.counted]);

  // Focus the popover so Escape and the labelled dialog work immediately.
  useEffect(() => {
    ref.current?.focus();
  }, [anchor.id]);

  // Escape and an outside click close. A press on another block is the strip's
  // own selection, so it is not treated as an outside click.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePopover();
    };
    const onDown = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && ref.current?.contains(target)) return;
      if (target?.closest?.("[data-anchor-id]")) return;
      closePopover();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, []);

  const uid = anchor.id.replace(/[^a-zA-Z0-9_-]/g, "-");
  const state = anchor.timeState;

  return (
    <div
      class="anchor-popover"
      role="dialog"
      aria-label={anchor.name}
      tabIndex={-1}
      ref={ref}
      style={{
        position: "fixed",
        top: `${pos.top}px`,
        left: `${pos.left}px`,
        width: `${POPOVER_WIDTH}px`,
        maxHeight: pos.maxHeight > 0 ? `${pos.maxHeight}px` : undefined,
      }}
    >
      <div class="anchor-popover__head">
        <span class="anchor-popover__name">
          {anchor.name}
        </span>
        <span class="anchor-popover__count">
          {anchor.blocks} {anchor.blocks === 1 ? "item" : "items"}
        </span>
      </div>

      <div class="anchor-popover__fields">
        <div class="anchor-popover__field">
          <label for={`anchor-popover-start-${uid}`}>Starts</label>
          <input
            id={`anchor-popover-start-${uid}`}
            class="anchor-popover__time"
            type="time"
            step={900}
            value={startValue}
            aria-invalid={startInvalid || undefined}
            aria-describedby={startInvalid ? `anchor-popover-start-error-${uid}` : undefined}
            onInput={(e) => {
              const value = (e.target as HTMLInputElement).value;
              setStartValue(value);
              setStartInvalid(!isValidHHMM(value));
            }}
            onChange={(e) => commitStart((e.target as HTMLInputElement).value)}
            onBlur={(e) => commitStart((e.target as HTMLInputElement).value)}
          />
          {startInvalid && (
            <span
              class="field-error"
              id={`anchor-popover-start-error-${uid}`}
              role="alert"
            >
              Start time is required.
            </span>
          )}
          {state.dirty && (
            <button
              type="button"
              class="anchor-popover__save-default"
              disabled={state.saving}
              onClick={() => props.onSaveDefault(anchor.id)}
            >
              {state.saving ? "Saving…" : "Save as default"}
            </button>
          )}
        </div>
        <div class="anchor-popover__field">
          <span>Window</span>
          <span class="anchor-popover__window">Until {display12h(anchor.end)}</span>
        </div>
      </div>

      {state.error && (
        <span class="field-error" role="alert">{state.error}</span>
      )}
      {!state.dirty && state.saved && <span class="anchor-popover__saved">Saved</span>}

      <div class="anchor-popover__row">
        <span>Count today</span>
        <button
          type="button"
          role="switch"
          aria-checked={anchor.counted}
          aria-label="Count today"
          class="anchor-popover__switch"
          onClick={() => props.onToggleCount(anchor.id)}
        />
      </div>

      <div class="anchor-popover__footer">
        <button
          type="button"
          class="anchor-popover__link"
          onClick={() => props.onEdit(anchor.id)}
        >
          Edit items
        </button>
        <span aria-hidden="true">·</span>
        <button
          type="button"
          class="anchor-popover__link anchor-popover__link--muted"
          onClick={() => props.onResetDefault(anchor.id)}
        >
          Reset to default
        </button>
      </div>
    </div>
  );
}
