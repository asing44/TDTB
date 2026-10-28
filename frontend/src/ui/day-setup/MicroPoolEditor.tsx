/* MicroPoolEditor — the Set up day "Edit pool" drawer for the Live
   micro-adventure card. It edits the pool items themselves (idea, category,
   effort, active) and writes them to the tracked repo config, so the pool is
   never hand-edited in config/tdtb.json. It reuses the Habits drawer frame and
   footer conventions; focus, Tab wrap, Escape and focus return come from
   useDialog.

   Edits are local until Save, which sends the FULL pool. An existing item's id
   never changes (streak and history are keyed by it); a new item takes the next
   free maNN id. The server refuses an empty pool, so the last item cannot be
   removed. Rows never reorder. */

import { useEffect, useState } from "preact/hooks";

import type { MicroPoolItem } from "../../model/types";
import type { MicroPoolState } from "../../store/store";
import { useDialog } from "../useDialog";
import "./micro-pool-editor.css";

export const POOL_MAX_ITEMS = 200;
export const IDEA_MAX = 300;
export const FIELD_MAX = 64;

/* Same rule as the Habits drawer: a focused control must never go natively
   disabled (focus would drop to <body> and the dialog would stop seeing keys). */
const ariaOff = (on: boolean) => (on ? "true" : undefined);

/** Next free id continuing the repo's maNN pattern, skipping any collision. */
export function nextPoolId(items: MicroPoolItem[]): string {
  const taken = new Set(items.map((i) => i.id));
  let n = items.reduce((max, i) => {
    const m = /^ma(\d+)$/.exec(i.id);
    return m ? Math.max(max, Number(m[1])) : max;
  }, 0);
  for (;;) {
    n += 1;
    const id = `ma${String(n).padStart(2, "0")}`;
    if (!taken.has(id)) return id;
  }
}

const same = (a: MicroPoolItem[] | null, b: MicroPoolItem[] | null) =>
  JSON.stringify(a) === JSON.stringify(b);

export interface MicroPoolEditorProps {
  pool: MicroPoolState;
  /** Load (or reload) the config pool. */
  onLoad: () => void;
  /** Save the FULL pool. */
  onSave: (items: MicroPoolItem[]) => void;
  onClose: () => void;
}

export function MicroPoolEditor({ pool, onLoad, onSave, onClose }: MicroPoolEditorProps) {
  const dialog = useDialog(onClose);
  // Safety net, as in the Habits drawer: when a control is removed focus falls
  // to <body>, where the dialog's own onKeyDown never sees Escape.
  useEffect(() => {
    const onDocKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const el = dialog.ref.current;
      if (el && e.target instanceof Node && el.contains(e.target)) return;
      onClose();
    };
    document.addEventListener("keydown", onDocKey);
    return () => document.removeEventListener("keydown", onDocKey);
  }, [onClose]);

  const [draft, setDraft] = useState<MicroPoolItem[] | null>(pool.items);
  const [fieldError, setFieldError] = useState("");
  const [newIdea, setNewIdea] = useState("");
  const [newCategory, setNewCategory] = useState("");
  const [newEffort, setNewEffort] = useState("");
  const [addError, setAddError] = useState("");

  // A fresh load or a saved pool replaces the draft only while nothing is
  // unsaved, so a Retry never clobbers edits in progress.
  const dirty = !same(draft, pool.items);
  useEffect(() => {
    setDraft((current) => (current === null || same(current, pool.items) ? pool.items : current));
  }, [pool.items]);
  const rows = draft ?? [];
  const changed = draft !== null && dirty;
  const busy = pool.save === "saving";
  const loading = pool.load === "loading";
  const atLimit = rows.length >= POOL_MAX_ITEMS;

  function patch(id: string, p: Partial<MicroPoolItem>) {
    setFieldError("");
    setDraft(rows.map((r) => (r.id === id ? { ...r, ...p } : r)));
  }
  function remove(row: MicroPoolItem) {
    if (rows.length <= 1) return;
    setFieldError("");
    setDraft(rows.filter((r) => r.id !== row.id));
    dialog.ref.current?.focus();
  }
  function add() {
    const idea = newIdea.trim();
    if (!idea) { setAddError("Enter an idea first."); return; }
    if (atLimit) return;
    setAddError("");
    setDraft([
      ...rows,
      { id: nextPoolId(rows), idea, category: newCategory.trim(), effort: newEffort.trim(), active: true },
    ]);
    setNewIdea(""); setNewCategory(""); setNewEffort("");
  }
  function save() {
    if (busy || !changed || rows.length === 0) return;
    const blank = rows.findIndex((r) => !r.idea.trim());
    if (blank >= 0) {
      setFieldError(`Idea ${blank + 1} is blank. Write something or remove it.`);
      return;
    }
    setFieldError("");
    onSave(rows.map((r) => ({ ...r, idea: r.idea.trim(), category: r.category.trim(), effort: r.effort.trim() })));
  }
  function discard() {
    if (busy || !changed) return;
    setFieldError("");
    setDraft(pool.items);
  }

  return (
    <>
      <div class="drawer-backdrop" onClick={onClose} />
      <div class="habits-drawer pool-editor" role="dialog" aria-modal="true" aria-label="Micro-adventure ideas"
        tabIndex={-1} ref={dialog.ref} onKeyDown={(e) => dialog.onKeyDown(e as unknown as KeyboardEvent)}>
        <header class="habits-drawer__head">
          <div>
            <h2>Micro-adventure ideas</h2>
            <p class="habits-drawer__sub">
              Writes to config/tdtb.json in the repo, so edits apply every day, not just today.
            </p>
          </div>
          <button type="button" class="btn habits-drawer__close" aria-label="Close micro-adventure ideas" onClick={onClose}>✕</button>
        </header>
        <div class="habits-drawer__scroll">
          {pool.load === "failed" && (
            <div class="pool-editor__problem">
              <span class="field-error" role="alert">Could not load ideas: {pool.loadError}</span>
              <button type="button" class="btn" onClick={onLoad}>Retry</button>
            </div>
          )}
          <section aria-labelledby="pool-editor-list">
            <div class="habits-drawer__group-head">
              <h3 id="pool-editor-list">Ideas <span>{rows.length}</span></h3>
            </div>
            {draft !== null && rows.length === 0 ? (
              <p class="pool-editor__empty">No ideas yet. Add one below — the pool can't be saved empty.</p>
            ) : (
              <ul class="habits-drawer__list pool-editor__list">
                {rows.map((row, i) => (
                  <li key={row.id} class={`habits-drawer__row pool-editor__row${row.active ? "" : " pool-editor__row--off"}`}>
                    <label class="pool-editor__field pool-editor__field--idea">
                      <span>Idea</span>
                      <input type="text" aria-label={`Idea ${i + 1}`} value={row.idea} maxLength={IDEA_MAX}
                        onInput={(e) => patch(row.id, { idea: (e.target as HTMLInputElement).value })} />
                    </label>
                    <label class="pool-editor__field">
                      <span>Category</span>
                      <input type="text" aria-label={`Category ${i + 1}`} value={row.category} maxLength={FIELD_MAX}
                        onInput={(e) => patch(row.id, { category: (e.target as HTMLInputElement).value })} />
                    </label>
                    <label class="pool-editor__field">
                      <span>Effort</span>
                      <input type="text" aria-label={`Effort ${i + 1}`} value={row.effort} maxLength={FIELD_MAX}
                        onInput={(e) => patch(row.id, { effort: (e.target as HTMLInputElement).value })} />
                    </label>
                    <div class="pool-editor__row-actions">
                      <label class="pool-editor__switch">
                        <input type="checkbox" role="switch" checked={row.active}
                          aria-label={`In rotation: ${row.idea}`}
                          onChange={() => patch(row.id, { active: !row.active })} />
                        <span>{row.active ? "In rotation" : "Off"}</span>
                      </label>
                      <button type="button" class="btn" aria-label={`Remove ${row.idea}`}
                        aria-disabled={ariaOff(rows.length <= 1)} onClick={() => remove(row)}>Remove</button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            {rows.length === 1 && (
              <p class="field__hint pool-editor__hint">
                The pool can't be empty — keep at least one idea. Switch it off instead of removing it.
              </p>
            )}
          </section>
          <section class="pool-editor__add" aria-labelledby="pool-editor-add">
            <div class="habits-drawer__group-head"><h3 id="pool-editor-add">Add an idea</h3></div>
            <div class="pool-editor__add-fields">
              <label class="pool-editor__field pool-editor__field--idea">
                <span>Idea</span>
                <input type="text" aria-label="New idea" value={newIdea} maxLength={IDEA_MAX}
                  onInput={(e) => setNewIdea((e.target as HTMLInputElement).value)} />
              </label>
              <label class="pool-editor__field">
                <span>Category</span>
                <input type="text" aria-label="New category" value={newCategory} maxLength={FIELD_MAX}
                  onInput={(e) => setNewCategory((e.target as HTMLInputElement).value)} />
              </label>
              <label class="pool-editor__field">
                <span>Effort</span>
                <input type="text" aria-label="New effort" value={newEffort} maxLength={FIELD_MAX}
                  onInput={(e) => setNewEffort((e.target as HTMLInputElement).value)} />
              </label>
              <button type="button" class="btn" aria-disabled={ariaOff(atLimit)} onClick={add}>Add idea</button>
            </div>
            {addError && <span class="field-error" role="alert">{addError}</span>}
            {atLimit && <p class="field__hint pool-editor__hint">The pool is full ({POOL_MAX_ITEMS} ideas). Remove one to add another.</p>}
          </section>
          {fieldError && <span class="field-error" role="alert">{fieldError}</span>}
        </div>
        <footer class="habits-drawer__foot">
          <div class="habits-drawer__status" role="status">
            {loading ? "Loading ideas…"
              : pool.save === "saving" ? "Saving…"
              : pool.save === "saved" && !changed
                ? "✓ Saved to config/tdtb.json. Today's list updates after the next source refresh."
              : pool.save === "failed" ? (
                <>
                  <span class="habits-drawer__chip habits-drawer__chip--bad">Not saved</span>
                  {!pool.shadowed && (
                    <button type="button" class="btn" onClick={() => !busy && onSave(rows)}>Retry</button>
                  )}
                  <span class="field-error" role="alert">
                    {pool.shadowed
                      ? "The machine-local config.json overrides the micro-adventure pool, so this edit would have no effect. Nothing was written."
                      : pool.saveError}
                  </span>
                </>
              ) : ""}
          </div>
          <button type="button" class="btn" aria-disabled={ariaOff(busy || !changed)} onClick={discard}>
            Discard changes
          </button>
          <button type="button" class="btn btn--primary" aria-disabled={ariaOff(busy || !changed || rows.length === 0)}
            onClick={save}>
            Save to config
          </button>
        </footer>
      </div>
    </>
  );
}
