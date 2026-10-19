/* F4c1: independently testable source actions; deliberately not mounted yet.
   Assignment changes belong to the per-row assign toggle (KTD4), so this menu
   is exactly Hide always (a journaled batch-of-one with the shared results and
   undo surface) and Delete (individual-only through stagingAction, with inline
   consent). This component never infers source success or edits exclusion
   policies. */
import { useEffect, useId, useRef, useState } from "preact/hooks";
import { useApp } from "./context";
import { itemIdentity, isCanonicalCapacitiesIdentity } from "../adapters/wire";
import { taskActionDisabledReason, type AppState } from "../store/store";
import type { AssignedItem } from "../model/types";

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

export function SourceMenu({ item, s }: { item: AssignedItem; s: AppState }) {
  const { controller, store } = useApp();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const locked = useRef(false);
  const wrap = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const edge = useRef<"first" | "last">("first");
  const base = `source-menu-${useId()}`;
  const source = item.source === "todoist" ? "Todoist" : item.source === "capacities" ? "Capacities" : "Calendar";
  const reason = pending ? "Source action in progress" : disabledReason(s, item);

  function close(refocus = true) {
    setOpen(false);
    setConfirming(false);
    if (refocus) trigger.current?.focus();
  }

  function focusEdge() {
    const items = wrap.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]');
    if (items?.length) items[edge.current === "last" ? items.length - 1 : 0].focus();
  }

  useEffect(() => {
    if (confirming) cancel.current?.focus();
    else if (open) focusEdge();
  }, [open, confirming]);

  useEffect(() => {
    if (!open && !confirming) return;
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
  }, [open, confirming]);

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Escape" && (open || confirming)) {
      e.preventDefault();
      e.stopPropagation();
      // Confirmation is its own inline state, never an ancestor's Escape.
      close();
      return;
    }
    if (confirming) return;
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      e.stopPropagation();
      if (!open) {
        edge.current = e.key === "ArrowUp" || e.key === "End" ? "last" : "first";
        setOpen(true);
        return;
      }
      const items = Array.from(wrap.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []);
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
      <span>{label}</span><span class="source-menu__detail">{detail}</span>
    </button>;
  }

  return <div class="source-menu" ref={wrap} onKeyDown={onKeyDown}>
    <button type="button" class="source-menu__trigger" ref={trigger}
      aria-haspopup="menu" aria-expanded={open} aria-controls={`${base}-popover`}
      onClick={() => {
        if (open || confirming) close();
        else { edge.current = "first"; setOpen(true); }
      }}>Source ▾</button>
    {open && <div id={`${base}-popover`} class="source-menu__popover" role="menu" aria-label={`Source actions for ${item.name}`}>
      {menuItem("Hide always", "Hidden from planning every day", "hideAlways")}
      <hr class="source-menu__separator" role="separator" />
      {menuItem("Delete source…", `Permanently deletes in ${source}`, "delete")}
      <p class="source-menu__note">Immediate actions · no plan commit needed</p>
      {reason && <p class="source-menu__reason" id={`${base}-reason`}>{reason}</p>}
    </div>}
    {confirming && <div class="source-menu__confirm" role="group" aria-labelledby={`${base}-question`}>
      <span id={`${base}-question`}>Delete “{item.name}” from {source}?</span>
      <p class="source-menu__detail">This permanently deletes the source item. It cannot be undone from here.</p>
      <div class="source-menu__confirm-actions">
        <button type="button" class="source-menu__delete" disabled={!!reason}
          aria-describedby={reason ? `${base}-confirm-reason` : undefined}
          onClick={() => void apply("delete")}>Delete from {source}</button>
        <button type="button" class="source-menu__trigger" ref={cancel} onClick={() => close()}>Cancel</button>
      </div>
      {reason && <p class="source-menu__reason" id={`${base}-confirm-reason`}>{reason}</p>}
    </div>}
  </div>;
}
