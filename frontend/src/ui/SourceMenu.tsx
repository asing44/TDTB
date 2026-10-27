/* F4c1: independently testable source actions; deliberately not mounted yet.
   Assignment changes belong to the per-row assign toggle (KTD4), so this menu
   is exactly Hide always (a journaled batch-of-one with the shared results and
   undo surface) and Delete (individual-only through stagingAction, behind an
   inline two-step confirm rendered inside the popover). The trigger is a 32px
   kebab icon button; the popover is position:fixed and placed by
   popoverPlacement so it can never clip off the viewport. This component never infers source success or edits exclusion
   policies. */
import { useEffect, useId, useLayoutEffect, useRef, useState } from "preact/hooks";
import { useApp } from "./context";
import { itemIdentity, isCanonicalCapacitiesIdentity } from "../adapters/wire";
import { taskActionDisabledReason, type AppState } from "../store/store";
import type { AssignedItem } from "../model/types";
import { computePopoverPlacement } from "./popoverPlacement";
import "./source-menu.css";

function disabledReason(s: AppState, item: AssignedItem): string | null {
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

export function SourceMenu({ item, s }: { item: AssignedItem; s: AppState }) {
  const { controller, store } = useApp();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const locked = useRef(false);
  const wrap = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const popover = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const edge = useRef<"first" | "last">("first");
  const base = `source-menu-${useId()}`;
  const source = item.source === "todoist" ? "Todoist" : item.source === "capacities" ? "Capacities" : "Calendar";
  const reason = pending ? "Source action in progress" : disabledReason(s, item);
  const shown = open || confirming;

  function close(refocus = true) {
    setOpen(false);
    setConfirming(false);
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
    if (!shown) return;
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [shown, confirming, reason]);

  useEffect(() => {
    if (confirming) cancel.current?.focus();
    else if (open) focusEdge();
  }, [open, confirming]);

  useEffect(() => {
    if (!shown) return;
    const outside = (e: Event) => {
      if (!wrap.current?.contains(e.target as Node)) close(false);
    };
    document.addEventListener("mousedown", outside);
    document.addEventListener("touchstart", outside);
    document.addEventListener("click", outside);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("touchstart", outside);
      document.removeEventListener("click", outside);
    };
  }, [shown]);

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Escape" && shown) {
      e.preventDefault();
      e.stopPropagation();
      // Confirmation is its own inline state, never an ancestor's Escape.
      close();
      return;
    }
    if (confirming) {
      if (e.key === "Tab") {
        // Two-button trap: Tab/Shift+Tab swaps Cancel and the destructive
        // button; Escape is the way out.
        const btns = Array.from(popover.current?.querySelectorAll<HTMLButtonElement>(".source-menu__confirm-actions button") ?? [])
          .filter((b) => !b.disabled);
        if (btns.length > 1) {
          e.preventDefault();
          const i = btns.indexOf(document.activeElement as HTMLButtonElement);
          btns[(i + (e.shiftKey ? -1 : 1) + btns.length) % btns.length].focus();
        }
      }
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

  async function apply(action: "hideAlways" | "delete") {
    const live = store.getState();
    const current = live.inputs?.assigned.find((row) => row.id === item.id);
    if (locked.current || !current || disabledReason(live, current)) return;
    if (action === "delete" && !confirming) return;
    locked.current = true;
    setPending(true);
    close();
    try {
      if (action === "delete") await controller.stagingAction("delete", item.id);
      else await controller.runRowBatch(item.id, action);
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
        if (action === "delete") { setOpen(false); setConfirming(true); }
        else void apply(action);
      }}>
      <ItemIcon kind={action} />
      <span class="source-menu__text">
        <span class="source-menu__label">{label}</span>
        <span class="source-menu__detail">{detail}</span>
      </span>
    </button>;
  }

  return <div class="source-menu" ref={wrap} onKeyDown={onKeyDown}>
    <button type="button" class="source-menu__trigger" ref={trigger}
      aria-label="Source actions" title="Source actions"
      aria-haspopup="menu" aria-expanded={shown} aria-controls={`${base}-popover`}
      onClick={() => {
        if (shown) close();
        else { edge.current = "first"; setOpen(true); }
      }}><KebabIcon /></button>
    {shown && <div id={`${base}-popover`} class="source-menu__popover" ref={popover}>
      {open && <div class="source-menu__menu" role="menu" aria-label={`Source actions for ${item.name}`}>
        {menuItem("Hide always", "Hidden from planning every day", "hideAlways")}
        <hr class="source-menu__separator" role="separator" />
        {menuItem(`Delete in ${source}…`, `Permanently deletes it in ${source}`, "delete")}
        {reason && <p class="source-menu__reason" id={`${base}-reason`}>{reason}</p>}
      </div>}
      {confirming && <div class="source-menu__confirm" role="group" aria-labelledby={`${base}-question`}>
        <span class="source-menu__question" id={`${base}-question`}>Delete “{item.name}” from {source}?</span>
        <p class="source-menu__detail">This permanently deletes the source item. It cannot be undone from here.</p>
        <div class="source-menu__confirm-actions">
          <button type="button" class="source-menu__cancel" ref={cancel} onClick={() => close()}>Cancel</button>
          <button type="button" class="source-menu__delete" disabled={!!reason}
            aria-describedby={reason ? `${base}-confirm-reason` : undefined}
            onClick={() => void apply("delete")}>Yes, delete permanently</button>
        </div>
        {reason && <p class="source-menu__reason" id={`${base}-confirm-reason`}>{reason}</p>}
      </div>}
    </div>}
  </div>;
}
