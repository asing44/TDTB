import { useEffect, useRef, useState } from "preact/hooks";
import { anchoredBlocks, anchoredOverrideOf, validateAnchoredOverride } from "../model/anchored";
import { display12h, formatBlockAmount } from "../model/time";
import { useApp, useAppState } from "./context";
import { useDialog } from "./useDialog";

export function AnchoredEditor() {
  const s = useAppState();
  const { controller, store } = useApp();
  const id = s.ui.editorAnchor;
  const block = s.inputs?.anchored.find((a) => a.id === id && a.kind !== "calendar");
  const initial = block ? anchoredOverrideOf(block, s.daySetup.anchored[block.id]) : null;
  const [start, setStart] = useState(initial?.time ?? block?.start ?? "");
  const [blocks, setBlocks] = useState(block && initial ? anchoredBlocks(block, initial) : 1);
  // U12: the Set up day screen edits/skips anchors through this one surface,
  // so the skip/on choice belongs here too.
  const [skip, setSkip] = useState(initial ? !initial.on || initial.skipToday : false);
  const [saving, setSaving] = useState(false);
  // The opener may ask for a specific field: the chip's Adjust time focuses the
  // Start input. Default focus stays with useDialog's first-focusable behaviour.
  const intent = s.ui.editorAnchorIntent;
  const startRef = useRef<HTMLInputElement>(null);
  const close = () =>
    store.dispatch({ type: "UI", patch: { editorAnchor: null, editorAnchorIntent: null } });
  const dialog = useDialog(close);
  // Declared after useDialog so it runs last and wins the mount focus.
  useEffect(() => {
    if (intent === "time") startRef.current?.focus();
    // Intent is read once on mount; the dialog is keyed by anchor id.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!id || !block || !initial || !s.inputs) return null;
  const override = { ...initial, time: start, blocks, on: !skip, skipToday: skip };
  const { errors, warnings } = validateAnchoredOverride(block, override, s.inputs.time);
  const apply = async () => {
    if (errors.length > 0) return;
    setSaving(true);
    await controller.saveAnchoredOverride(id, override);
    setSaving(false);
    close();
  };

  return (
    <>
      <div class="drawer-backdrop" onClick={close} />
      <div
        class="editor"
        role="dialog"
        aria-modal="true"
        aria-label={`Edit anchored block ${block.name}`}
        tabIndex={-1}
        ref={dialog.ref}
        onKeyDown={(e) => dialog.onKeyDown(e as unknown as KeyboardEvent)}
      >
        <h3>{block.name}</h3>
        <div class="field">
          <label for="anchor-editor-start">Start</label>
          <input
            id="anchor-editor-start"
            aria-label="Anchored start"
            type="time"
            step={1800}
            value={start}
            ref={startRef}
            onInput={(e) => setStart((e.target as HTMLInputElement).value)}
          />
        </div>
        <div class="field">
          <label>Duration</label>
          <div class="stepper" role="group" aria-label="Anchored duration" tabIndex={-1}>
            <button
              type="button"
              onClick={() => setBlocks((b) => Math.max(0, b - 1))}
              disabled={blocks <= 0}
              aria-label="Shorter anchored duration"
            >−</button>
            <span aria-live="polite">{blocks === 0 ? "Background · 0min" : formatBlockAmount(blocks)}</span>
            <button
              type="button"
              onClick={() => setBlocks((b) => b + 1)}
              aria-label="Longer anchored duration"
            >+</button>
          </div>
        </div>
        {block.kind === "window" && block.start && block.end && (
          <div class="editor__hint">
            Source window: {display12h(block.start)}–{display12h(block.end)}
          </div>
        )}
        <div class="field">
          <label>Today</label>
          <button
            type="button"
            class={`toggle ${!skip ? "toggle--on" : ""}`}
            aria-pressed={!skip}
            onClick={() => setSkip((value) => !value)}
          >
            {skip ? "Skipped" : "On"}
          </button>
        </div>
        {errors.map((error) => <div class="field-error" role="alert">{error}</div>)}
        {warnings.map((warning) => <div class="field-warning" role="status">{warning}</div>)}
        <div class="editor__hint">Today only — source config stays unchanged.</div>
        <div class="editor__actions">
          <button class="btn" onClick={close}>Cancel</button>
          <button class="btn btn--primary" onClick={apply} disabled={saving || errors.length > 0}>
            {saving ? "Saving…" : "Apply"}
          </button>
        </div>
      </div>
    </>
  );
}
