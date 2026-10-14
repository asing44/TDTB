/* ActionDock — sticky state-driven dock (locked decision 1). One explicit
   billed Auto sequence action; exact placement/editor controls are the
   deterministic recovery path. Live commit never appears here — it lives
   behind the approval drawer's second gate. */

import { useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import {
  acceptableDefects,
  canAutoSequence,
  canShadow,
  defectsResolved,
  dockState,
} from "../store/store";
import { budgetTotal, localSelected } from "../store/allocatorView";
import { buildDayPrompt } from "../store/exportPrompt";
import { formatBlockAmount } from "../model/time";

/* R20b (restored): the Plan screen keeps a pure-local "Copy prompt" fallback
   that serializes today's plan for an external LLM. It is NOT the opt-in
   prompt-task export, which remains Commit-only, and it never writes to
   Todoist, Calendar, or the ledger. */
function CopyPromptButton() {
  const s = useAppState();
  const [status, setStatus] = useState<"idle" | "copied" | "unavailable">("idle");
  const [fallbackPrompt, setFallbackPrompt] = useState("");

  const copy = async () => {
    const text = buildDayPrompt(s);
    try {
      if (typeof navigator.clipboard?.writeText === "function") {
        await navigator.clipboard.writeText(text);
        setFallbackPrompt("");
        setStatus("copied");
        setTimeout(() => setStatus("idle"), 2000);
        return;
      }
    } catch {
      // Rejected or unavailable clipboard falls through to the readable
      // fallback below — never an unhandled rejection, never a success lie.
    }
    setFallbackPrompt(text);
    setStatus("unavailable");
  };

  return (
    <>
      <button
        class="btn"
        onClick={() => void copy()}
        aria-label="Copy plan prompt for an external LLM"
        title="local plan context · export stays Commit-only"
      >
        Copy prompt
        <span class="btn__sub">local plan context · export stays Commit-only</span>
      </button>
      {status === "copied" && (
        <span class="copy-prompt-status" role="status">
          Copied to clipboard
        </span>
      )}
      {status === "unavailable" && (
        <div class="copy-prompt-fallback" role="alert">
          <span>Clipboard unavailable — select the prompt below</span>
          <textarea
            aria-label="Prompt text to copy manually"
            value={fallbackPrompt}
            readOnly
            onFocus={(event) => event.currentTarget.select()}
            onClick={(event) => event.currentTarget.select()}
          />
        </div>
      )}
    </>
  );
}
export function ActionDock() {
  const s = useAppState();
  const { controller, store } = useApp();
  if (!s.inputs) return null;

  const state = dockState(s);
  const defects = acceptableDefects(s);
  const defectsPending = state === "review" && !defectsResolved(s);
  const openSetup = () => store.dispatch({ type: "UI", patch: { settingsPanel: "day" } });
  const openApproval = () =>
    store.dispatch({ type: "UI", patch: { approvalOpen: true } });

  // T12e (3a spec): the block count being committed lives in the primary
  // label and the status line, so the number is never off-screen when the
  // billed button is reachable.
  const spend = localSelected(s);
  const budget = budgetTotal(s);
  const over = Math.max(0, spend - budget);
  // FEEDBACK-18: the entry point names the live count of writes the preview
  // will produce (shadow is current in preview state, so this is exact).
  const activeWrites = s.shadow
    ? s.shadow.entries.filter((e) => e.classification !== "no-op").length
    : 0;
  // A degraded source read is not a usable planning snapshot. Keep the
  // clipboard fallback available, but do not let a billed sequence or a
  // write-oriented preview become the accidental recovery path.
  const sourcesHealthy = s.inputs.sourceHealth === "ok";

  const statusText: Record<string, string> = {
    setup: "Confirm your day frame, blocks, and captures to begin.",
    /* 2026-10-06 cockpit feedback item 2: report the day's actual numbers. The
       old line offered a hypothetical accepted trim ("Accept the trim and you
       sequence …"), describing a thing the operator never does — they keep a
       row, or they exclude / complete / delete it. */
    sequence:
      over > 0
        ? `${formatBlockAmount(spend)} selected of ${formatBlockAmount(budget)} capacity - ${formatBlockAmount(over)} over.`
        : `Setup confirmed. Sequence ${formatBlockAmount(spend)} of ${formatBlockAmount(budget)} when ready.`,
    sequencing: "Sequencing — one billed judgment call in flight…",
    review: defectsPending
      ? `Sequence staged with ${defects.length} acceptable defect${defects.length === 1 ? "" : "s"} — fix, or accept as-is to proceed.`
      : "Sequence staged and valid. Preview the exact writes next.",
    fix: `${s.validation?.hardErrors.length ?? 0} blocking issue${(s.validation?.hardErrors.length ?? 0) === 1 ? "" : "s"} — place or edit rows to fix, or resequence.`,
    preview: "Shadow preview is current. Review and approve in the drawer.",
    committing: "Committing to Todoist and Calendar…",
    verified: "Committed and verified. Have a good day.",
    partial: "Commit did not complete cleanly — review surfaces in the drawer.",
    "budget-manual": "Billed budget spent — manual layout stays available.",
  };

  return (
    <div class="dock">
      <span class="dock__status" role="status">
        {statusText[state]}
      </span>

      {!sourcesHealthy && (
        <div class="dock__health" role="alert">
          Sources {s.inputs.sourceHealth} — sequencing and commit are blocked until the read is healthy.
        </div>
      )}

      <div class="dock__controls">
        <CopyPromptButton />
        <span class="dock__prompt-note" role="note">
          Copy prompt shares this plan locally; opt-in prompt tasks still export on Commit only.
        </span>

      {/* Day setup reaches the frame, allotment, anchored blocks and the Live
          micro-adventure — all of it editable at any phase, so it belongs in
          the dock outright rather than behind the edit-day toggle and only
          during setup. It stays primary while setup is unconfirmed, since
          that is the one state where it is the next action. FEEDBACK-08:
          the unconfirmed state is called out in the button's own sub-label,
          not only in the status line. */}
      <button
        class={`btn dock__setup ${state === "setup" ? "btn--primary" : ""}`}
        onClick={openSetup}
        aria-label="Open day setup"
      >
        {state === "setup" ? "Confirm day setup" : "Day setup"}
        {state === "setup" && <span class="btn__sub">setup not confirmed</span>}
      </button>

      {/* FEEDBACK-08 (A06): the Results/SOW surface is the approval drawer —
          the statement of work for today's writes. It gets a persistent
          named entry in review (preview not yet built), preview, and
          verified instead of three differently-named controls ("Preview
          commit" stays as the build action; partial keeps the T21 shout). */}
      {(state === "review" || state === "preview" || state === "verified") && (
        <button
          class={`btn ${state === "preview" ? "btn--primary" : ""}`}
          onClick={openApproval}
          aria-label="Results — review exact writes and commit result"
        >
          Results
          {state === "review" && <span class="btn__sub">preview not built yet</span>}
          {state === "preview" && (
            <span class="btn__sub">
              {activeWrites} write{activeWrites === 1 ? "" : "s"} ready
            </span>
          )}
          {state === "verified" && <span class="btn__sub">statement of work</span>}
        </button>
      )}

      {(state === "sequence" || state === "fix" || state === "budget-manual") && (
        <>
          {s.sequence && s.seqPhase === "dirty" && (
            <button
              class="btn"
              onClick={() => void controller.revalidate()}
              title="deterministic · free"
            >
              Revalidate
              <span class="btn__sub">deterministic · free</span>
            </button>
          )}
          <button
            class="btn btn--primary"
            onClick={() => void controller.autoSequence()}
            disabled={!canAutoSequence(s) || !sourcesHealthy}
            title={
              canAutoSequence(s) && sourcesHealthy
                ? "One billed judgment call"
                : !sourcesHealthy
                  ? "Sources must be healthy before a billed sequence"
                  : "Requires confirmed setup and billed budget"
            }
          >
            Sequence & commit {formatBlockAmount(spend)}
            <span class="btn__sub">1 billed call</span>
          </button>
        </>
      )}

      {state === "review" && (
        <>
          <button
            class="btn"
            onClick={() => void controller.autoSequence()}
            disabled={!canAutoSequence(s) || !sourcesHealthy}
          >
            Resequence
            <span class="btn__sub">1 billed · {s.ledger?.remaining ?? 0} left</span>
          </button>
          {defectsPending && (
            <button
              class="btn"
              onClick={() => store.dispatch({ type: "ACCEPT_DEFECTS" })}
              title="Acknowledge the listed defects and unblock preview/commit — today only; any edit or source drift revokes it"
            >
              Accept as-is
              <span class="btn__sub">
                {defects.length} defect{defects.length === 1 ? "" : "s"} · a working plan beats a perfect one
              </span>
            </button>
          )}
          <button
            class="btn btn--primary"
            onClick={() => {
              openApproval();
              void controller.shadowPreview();
            }}
            disabled={!canShadow(s) || !sourcesHealthy}
          >
            Preview commit
            <span class="btn__sub">shadow — writes nothing</span>
          </button>
        </>
      )}

      {/* partial keeps the T21 shout: the dock names the failure count, not a
          neutral results link. */}

      {state === "partial" && (
        <button class="btn btn--danger" onClick={openApproval}>
          Commit incomplete — view failures
          <span class="btn__sub">
            {s.commitReport?.verifyFailures.length ?? 0} verification failure
            {(s.commitReport?.verifyFailures.length ?? 0) === 1 ? "" : "s"}
          </span>
        </button>
      )}

      {(state === "sequencing" || state === "committing") && (
        <button class="btn" disabled>
          Working…
        </button>
      )}
      </div>
    </div>
  );
}
