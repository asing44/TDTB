/* Source actions on the collapsed task row. Assignment changes belong to the
   per-row assign toggle (KTD4), so the menu is exactly Hide always (a
   journaled batch-of-one with the shared results and undo surface) and Delete
   (individual-only through stagingAction). The trigger is a 32px kebab icon
   button in the row's control cluster; the menu popover is position:fixed and
   placed by popoverPlacement so it can never clip off the viewport.

   Delete's second step is NOT a popover: choosing it asks the row
   (onRequestDelete) to swap its content for SourceDeleteConfirm, a row-height
   panel with the question, the consequence, Cancel and a destructive button.
   Nothing floats, so nothing is clipped or covers a neighbouring row. This
   module never infers source success or edits exclusion policies. */
import { useEffect, useId, useLayoutEffect, useRef, useState } from "preact/hooks";
import type { Ref } from "preact";
import { useApp } from "./context";
import { itemIdentity, isCanonicalCapacitiesIdentity } from "../adapters/wire";
import { taskActionDisabledReason, type AppState } from "../store/store";
import type { AssignedItem } from "../model/types";
import { computePopoverPlacement } from "./popoverPlacement";
import "./source-menu.css";

export function disabledReason(s: AppState, item: AssignedItem): string | null {
  if (s.runtimeBusy || s.batch.phase === "applying") {
    return "Source action in progress";
  }
  const gate = taskActionDisabledReason(s, "complete");
  if (gate) return gate;
  if (item.source !== "todoist" && item.source !== "capacities") {
    return "Calendar source actions are not available here";
  }
  // Match the controller's identity resolution, without admitting name/path
  // fallbacks as arbitrary upstream mutation targets.
  const identity = item.identity ?? itemIdentity(item);
  if (item.source === "todoist"
    ? !identity?.startsWith("todoist:") || identity.length <= "todoist:".length
    : !isCanonicalCapacitiesIdentity(identity)) {
    return "This item has no stable source identity";
  }
  if (!s.inputs?.assigned.some((row) => row.id === item.id)) {
    return "This item is no longer assigned";
  }
  return null;
}

function KebabIcon() {
  return <svg class="source-menu__icon" viewBox="0 0 16 16" width="16" height="16"
    fill="currentColor" aria-hidden="true" focusable="false">
    <circle cx="8" cy="3.25" r="1.35" /><circle cx="8" cy="8" r="1.35" /><circle cx="8" cy="12.75" r="1.35" />
  </svg>;
}

function ItemIcon({ kind }: { kind: "hideAlways" | "delete" }) {
  return <svg class="source-menu__item-icon" viewBox="0 0 16 16" width="18" height="18" fill="none"
    stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
    aria-hidden="true" focusable="false">
    {kind === "hideAlways"
      ? <><path d="M1.5 8s2.3-4.5 6.5-4.5S14.5 8 14.5 8s-2.3 4.5-6.5 4.5S1.5 8 1.5 8z" /><path d="M2.5 13.5l11-11" /></>
      : <><path d="M2.5 4.5h11M6 4.5V3h4v1.5M4 4.5l.7 8.5h6.6l.7-8.5" /><path d="M6.8 7v3.8M9.2 7v3.8" /></>}
  </svg>;
}

function sourceName(item: AssignedItem): string {
  return item.source === "todoist" ? "Todoist" : item.source === "capacities" ? "Capacities" : "Calendar";
}

/** Row-side state for Delete's inline confirm: the row swaps its content while
    `deleting`, and backing out returns focus to the kebab once it has rendered
    again. Shared by the plan row and the Not in plan drawer row. */
export function useRowDelete() {
  const [deleting, setDeleting] = useState(false);
  const kebab = useRef<HTMLButtonElement | null>(null);
  const refocus = useRef(false);
  useEffect(() => {
    if (deleting || !refocus.current) return;
    refocus.current = false;
    kebab.current?.focus();
  }, [deleting]);
  const closeConfirm = (back: boolean) => {
    refocus.current = back;
    setDeleting(false);
  };
  return { deleting, setDeleting, kebab, closeConfirm };
}

export function SourceMenu({ item, s, onRequestDelete, triggerRef }: {
  item: AssignedItem;
  s: AppState;
  /** Delete was chosen: the row swaps in SourceDeleteConfirm. Nothing is sent. */
  onRequestDelete: () => void;
  /** Lets the row put focus back on the trigger when the confirm closes. */
  triggerRef?: Ref<HTMLButtonElement>;
}) {
  const { controller, store } = useApp();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const locked = useRef(false);
  const wrap = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const popover = useRef<HTMLDivElement>(null);
  const edge = useRef<"first" | "last">("first");
  const base = `source-menu-${useId()}`;
  const source = sourceName(item);
  const reason = pending ? "Source action in progress" : disabledReason(s, item);

  function close(refocus = true) {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  }

  function focusEdge() {
    const items = wrap.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]');
    if (items?.length) items[edge.current === "last" ? items.length - 1 : 0].focus();
  }

  // Anchor to the trigger, right-aligned, preferring downward; the panel is
  // position:fixed so the row's overflow can never clip it.
  function place() {
    const el = popover.current;
    const t = trigger.current;
    if (!el || !t) return;
    el.style.maxHeight = "";
    const p = computePopoverPlacement(
      t.getBoundingClientRect(),
      { width: window.innerWidth, height: window.innerHeight },
      { width: el.offsetWidth, height: el.scrollHeight },
      "down",
      "end",
    );
    el.style.top = `${p.top}px`;
    el.style.left = `${p.left}px`;
    el.style.maxHeight = `${p.maxHeight}px`;
    el.dataset.placement = p.placement;
  }

  useLayoutEffect(() => {
    if (!open) return;
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, reason]);

  useEffect(() => {
    if (open) focusEdge();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const outside = (e: Event) => {
      // composedPath is fixed at dispatch: the clicked menu item may already be
      // unmounted (re-render between listeners), so target.contains() would
      // misread an inside click as outside and cancel the confirm step.
      const inside = e.composedPath().includes(wrap.current as EventTarget);
      if (!inside) close(false);
    };
    document.addEventListener("mousedown", outside);
    document.addEventListener("touchstart", outside);
    document.addEventListener("click", outside);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("touchstart", outside);
      document.removeEventListener("click", outside);
    };
  }, [open]);

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Escape" && open) {
      e.preventDefault();
      e.stopPropagation();
      // The menu closes before any ancestor sees Escape.
      close();
      return;
    }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      e.stopPropagation();
      if (!open) {
        edge.current = e.key === "ArrowUp" || e.key === "End" ? "last" : "first";
        setOpen(true);
        return;
      }
      const items = Array.from(wrap.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []);
      if (!items.length) return;
      const i = items.indexOf(document.activeElement as HTMLButtonElement);
      const n = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1
        : e.key === "ArrowDown" ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
      items[n]?.focus();
    } else if (e.key === "Tab" && open) {
      close(false);
    }
  }

  async function hideAlways() {
    const live = store.getState();
    const current = live.inputs?.assigned.find((row) => row.id === item.id);
    if (locked.current || !current || disabledReason(live, current)) return;
    locked.current = true;
    setPending(true);
    close();
    try {
      await controller.runRowBatch(item.id, "hideAlways");
    } finally {
      locked.current = false;
      setPending(false);
    }
  }

  function menuItem(label: string, detail: string, action: "hideAlways" | "delete") {
    return <button type="button" role="menuitem" tabIndex={-1}
      class={`source-menu__item${action === "delete" ? " source-menu__item--danger" : ""}`}
      aria-label={label} aria-disabled={reason ? "true" : undefined}
      aria-describedby={reason ? `${base}-reason` : undefined}
      disabled={!!reason}
      onClick={() => {
        if (reason) return;
        if (action === "delete") { setOpen(false); onRequestDelete(); }
        else void hideAlways();
      }}>
      <ItemIcon kind={action} />
      <span class="source-menu__text">
        <span class="source-menu__label">{label}</span>
        <span class="source-menu__detail">{detail}</span>
      </span>
    </button>;
  }

  return <div class="source-menu" ref={wrap} onKeyDown={onKeyDown}>
    <button type="button" class="source-menu__trigger"
      ref={(el) => {
        trigger.current = el;
        if (typeof triggerRef === "function") triggerRef(el);
        else if (triggerRef) (triggerRef as { current: HTMLButtonElement | null }).current = el;
      }}
      aria-label="Source actions" title="Source actions"
      aria-haspopup="menu" aria-expanded={open} aria-controls={`${base}-popover`}
      onClick={() => {
        if (open) close();
        else { edge.current = "first"; setOpen(true); }
      }}><KebabIcon /></button>
    {open && <div id={`${base}-popover`} class="source-menu__popover" ref={popover}>
      <div class="source-menu__menu" role="menu" aria-label={`Source actions for ${item.name}`}>
        {menuItem("Hide always", "Hidden from planning every day", "hideAlways")}
        <hr class="source-menu__separator" role="separator" />
        {menuItem(`Delete in ${source}…`, `Permanently deletes it in ${source}`, "delete")}
        {reason && <p class="source-menu__reason" id={`${base}-reason`}>{reason}</p>}
      </div>
    </div>}
  </div>;
}

/** Delete's confirm step, rendered by the row in place of its content. Opening
    sends nothing; only the destructive button calls the existing
    controller.stagingAction("delete", id). Focus lands on Cancel, Tab swaps
    between the two buttons, and Escape or Cancel closes with no call (the row
    refocuses the kebab). Keys never reach the row's own shortcuts. */
export function SourceDeleteConfirm({ item, s, onClose }: {
  item: AssignedItem;
  s: AppState;
  /** `refocus` is true when the operator backed out and the kebab should regain focus. */
  onClose: (refocus: boolean) => void;
}) {
  const { controller, store } = useApp();
  const locked = useRef(false);
  const cancel = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const base = `source-confirm-${useId()}`;
  const source = sourceName(item);
  const reason = disabledReason(s, item);

  useEffect(() => { cancel.current?.focus(); }, []);

  function onKeyDown(e: KeyboardEvent) {
    // Confirmation is its own inline state: nothing here is an ancestor's key.
    e.stopPropagation();
    if (e.key === "Escape") {
      e.preventDefault();
      onClose(true);
    } else if (e.key === "Tab") {
      // Two-button trap: Tab/Shift+Tab swaps Cancel and the destructive
      // button; Escape is the way out.
      const btns = Array.from(panel.current?.querySelectorAll<HTMLButtonElement>(".source-confirm__actions button") ?? [])
        .filter((b) => !b.disabled);
      if (btns.length > 1) {
        e.preventDefault();
        const i = btns.indexOf(document.activeElement as HTMLButtonElement);
        btns[(i + (e.shiftKey ? -1 : 1) + btns.length) % btns.length].focus();
      }
    }
  }

  async function confirmDelete() {
    const live = store.getState();
    const current = live.inputs?.assigned.find((row) => row.id === item.id);
    if (locked.current || !current || disabledReason(live, current)) return;
    locked.current = true;
    onClose(false);
    await controller.stagingAction("delete", item.id);
  }

  return <div class="source-confirm" ref={panel} role="group" aria-labelledby={`${base}-question`}
    onKeyDown={onKeyDown}>
    <div class="source-confirm__text">
      <span class="source-confirm__question" id={`${base}-question`}>Delete “{item.name}” from {source}?</span>
      <p class="source-confirm__detail">This permanently deletes the source item. It cannot be undone from here.</p>
      {reason && <p class="source-confirm__reason" id={`${base}-reason`}>{reason}</p>}
    </div>
    <div class="source-confirm__actions">
      <button type="button" class="source-confirm__cancel" ref={cancel} onClick={() => onClose(true)}>Cancel</button>
      <button type="button" class="source-confirm__delete" disabled={!!reason}
        aria-describedby={reason ? `${base}-reason` : undefined}
        onClick={() => void confirmDelete()}>Delete</button>
    </div>
  </div>;
}
