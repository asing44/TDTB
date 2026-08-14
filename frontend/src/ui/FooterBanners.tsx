/* FooterBanners — T12e (3a spec) → T12i: the runtime-action undo chip and
   runtime-action failures still render full-width above the dock — that
   feedback is transient, singular, and carries a button. The VALIDATION
   roll-up no longer gets footer rows at all: even collapsed to a count, the
   error bar + warnings bar were two full-width stripes covering the table
   they describe (Adam, 2026-07-27 21:11). Alerts now float as compact pills
   (⛔ N / ⚠ N) above the footer's right edge, each expanding a scrollable
   popover on demand. The dock status line still narrates blocking state, so
   a hidden pile can't silently swallow the reason a Send is refused. */

import { useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { alerts } from "../store/store";

const VERB_LABELS: Record<string, string> = {
  complete: "Completed",
  done: "Done",
  skip_today: "Skipped today",
  remove_from_today: "Removed from today",
  duration_edit: "Duration changed",
  move_resize: "Moved",
  delete_permanent: "Deleted permanently",
  delete: "Deleted",
  defer: "Deferred",
  assign: "Assigned",
  drop_from_plan: "Dropped from plan",
  unassign: "Unassigned",
};

function verbLabel(verb: string): string {
  return VERB_LABELS[verb] ?? verb;
}

export function FooterBanners() {
  const s = useAppState();
  const { controller } = useApp();
  const [openPanel, setOpenPanel] = useState<"error" | "rest" | null>(null);
  const a = s.lastRuntimeAction;

  const error =
    s.runtimeError ??
    (a && (a.status === "compensated" || a.status === "partial" || a.status === "undo_failed")
      ? `${verbLabel(a.verb)} of ${a.targetName} ${
          a.status === "compensated"
            ? "failed — every applied step was rolled back"
            : a.status === "partial"
              ? "partially failed — rollback incomplete, check the affected surfaces"
              : "undo failed on one or more steps — check the affected surfaces"
        }${a.error ? ` · ${a.error}` : ""}`
      : null);

  // Global alert roll-up (locked decision 6). The overassigned warning is
  // filtered: the rail's budget readout and the dock status already carry
  // that number.
  const list = alerts(s).filter((x) => !x.text.startsWith("Overassigned"));

  const blocking = list.filter((x) => x.level === "error");
  const rest = list.filter((x) => x.level !== "error");
  // A panel whose list emptied (re-validate cleared it) closes rather than
  // hanging around as an empty popover.
  const panelItems = openPanel === "error" ? blocking : openPanel === "rest" ? rest : [];
  const panelOpen = openPanel !== null && panelItems.length > 0;

  const icon = (level: string) =>
    level === "error" ? "⛔" : level === "info" ? "ℹ" : "⚠";

  const togglePanel = (which: "error" | "rest") =>
    setOpenPanel((p) => (p === which ? null : which));

  return (
    <>
      {list.length > 0 && (
        <div class="alert-pills" role="region" aria-label="Issues">
          {panelOpen && (
            <div
              class="alert-pills__panel"
              role="list"
              aria-label={openPanel === "error" ? "Blocking issues" : "Scheduling warnings"}
            >
              {panelItems.map((x, i) => (
                <div key={i} role="listitem" class={`alert-pills__item alert-pills__item--${x.level}`}>
                  <span aria-hidden="true">{icon(x.level)}</span>
                  <span>{x.text}</span>
                </div>
              ))}
            </div>
          )}
          <div class="alert-pills__row" aria-live="polite">
            {blocking.length > 0 && (
              <button
                class="alert-pill alert-pill--error"
                aria-expanded={openPanel === "error"}
                aria-label={`${blocking.length} blocking issue${blocking.length === 1 ? "" : "s"} — show details`}
                onClick={() => togglePanel("error")}
              >
                <span aria-hidden="true">⛔</span>
                <span>{blocking.length} blocking</span>
              </button>
            )}
            {rest.length > 0 && (
              <button
                class="alert-pill alert-pill--warning"
                aria-expanded={openPanel === "rest"}
                aria-label={`${rest.length} scheduling warning${rest.length === 1 ? "" : "s"} — show details`}
                onClick={() => togglePanel("rest")}
              >
                <span aria-hidden="true">⚠</span>
                <span>{rest.length} warning{rest.length === 1 ? "" : "s"}</span>
              </button>
            )}
          </div>
        </div>
      )}
      {a && a.status === "applied" && (
        <div class="footer-banner footer-banner--undo" role="status">
          <span>
            {verbLabel(a.verb)} · <strong>{a.targetName}</strong>
            {a.duplicate ? " (already applied)" : ""}
          </span>
          <span class="footer-banner__note">journaled</span>
          <button
            class="btn footer-banner__undo"
            aria-label={`Undo: ${verbLabel(a.verb)} ${a.targetName}`}
            disabled={s.runtimeBusy}
            onClick={() => void controller.undoRuntimeAction()}
          >
            Undo
          </button>
        </div>
      )}
      {a && a.status === "undone" && (
        <div class="footer-banner" role="status">
          Undone · {a.targetName}
        </div>
      )}
      {error && (
        <div class="footer-banner footer-banner--error" role="alert">
          <span>⛔ {error}</span>
        </div>
      )}
    </>
  );
}
