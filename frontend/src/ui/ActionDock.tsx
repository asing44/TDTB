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
import { budgetTotal, localSelected, trimForState } from "../store/allocatorView";
import { buildDayPrompt } from "../store/exportPrompt";

async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator.clipboard?.writeText === "function") {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Some browsers expose the async API but reject it outside a trusted
    // gesture or secure context. Try the local legacy path before failing.
  }

  let ta: HTMLTextAreaElement | null = null;
  try {
    ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    return typeof document.execCommand === "function" && document.execCommand("copy");
  } catch {
    return false;
  } finally {
    ta?.remove();
  }
}

/* Manual fallback: copy today's exact state as a self-contained scheduling
   prompt for an external LLM. Never disabled — it exists precisely for the
   states where the cockpit itself is blocked (degraded sources, spent
   ledger). Clipboard write only; no network, no billed call. */
function CopyPromptButton() {
  const s = useAppState();
  const [copyState, setCopyState] = useState<"idle" | "copied" | "unavailable">("idle");
  const [fallbackText, setFallbackText] = useState("");
  const copy = async () => {
    const text = buildDayPrompt(s);
    if (await copyText(text)) {
      setFallbackText("");
      setCopyState("copied");
      setTimeout(() => setCopyState("idle"), 2000);
      return;
    }
    setFallbackText(text);
    setCopyState("unavailable");
  };
  return (
    <>
      <button class="btn" onClick={() => void copy()} aria-label="Copy plan prompt for an external LLM">
        {copyState === "copied"
          ? "Copied ✓"
          : copyState === "unavailable"
            ? "Copy unavailable"
            : "Copy prompt"}
        <span class="btn__sub">paste into any LLM · fallback</span>
      </button>
      {copyState === "unavailable" && (
        <div class="copy-prompt-fallback" role="alert">
          <span>Clipboard unavailable — select the prompt below</span>
          <textarea
            aria-label="Prompt text to copy manually"
            value={fallbackText}
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
  const openSetup = () => store.dispatch({ type: "UI", patch: { setupOpen: true } });
  const openApproval = () =>
    store.dispatch({ type: "UI", patch: { approvalOpen: true } });

  // T12e (3a spec): the block count being committed lives in the primary
  // label and the status line, so the number is never off-screen when the
  // billed button is reachable.
  const spend = localSelected(s);
  const budget = budgetTotal(s);
  const trim = trimForState(s);
  // FEEDBACK-18: the entry point names the live count of writes the preview
  // will produce (shadow is current in preview state, so this is exact).
  const activeWrites = s.shadow
    ? s.shadow.entries.filter((e) => e.classification !== "no-op").length
    : 0;

  const statusText: Record<string, string> = {
    setup: "Confirm your day frame, blocks, and captures to begin.",
    sequence:
      trim.drop.length > 0
        ? `Accept the trim and you sequence ${trim.after} of ${budget} blk.`
        : `Setup confirmed. Sequence ${spend} of ${budget} blk when ready.`,
    sequencing: "Sequencing — one billed judgment call in flight…",
    review: defectsPending
      ? `Sequence staged with ${defects.length} acceptable defect${defects.length === 1 ? "" : "s"} — fix, or accept as-is to proceed.`
      : "Sequence staged and valid. Preview the exact writes next.",
    fix: `${s.validation?.hardErrors.length ?? 0} blocking issue${(s.validation?.hardErrors.length ?? 0) === 1 ? "" : "s"} — place or edit rows to fix, or resequence.`,
    preview: "Shadow preview is current. Review and approve in the drawer.",
    committing: "Committing to Todoist, Calendar, and vault…",
    verified: "Committed and verified. Have a good day.",
    partial: "Commit did not complete cleanly — review surfaces in the drawer.",
    "budget-manual": "Billed budget spent — manual layout stays available.",
  };

  return (
    <footer class="dock" aria-label="Actions">
      <span class="dock__status" role="status">
        {statusText[state]}
      </span>

      <CopyPromptButton />

      {/* Day setup reaches the frame, allotment, anchored blocks and the Live
          micro-adventure — all of it editable at any phase, so it belongs in
          the dock outright rather than behind the edit-day toggle and only
          during setup. It stays primary while setup is unconfirmed, since
          that is the one state where it is the next action. FEEDBACK-08:
          the unconfirmed state is called out in the button's own sub-label,
          not only in the status line. */}
      <button
        class={`btn ${state === "setup" ? "btn--primary" : ""}`}
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
            <button class="btn" onClick={() => void controller.revalidate()}>
              Revalidate
              <span class="btn__sub">deterministic · free</span>
            </button>
          )}
          <button
            class="btn btn--primary"
            onClick={() => void controller.autoSequence()}
            disabled={!canAutoSequence(s)}
            title={
              canAutoSequence(s)
                ? "One billed judgment call"
                : "Requires confirmed setup and billed budget"
            }
          >
            Auto sequence {spend} blk
            <span class="btn__sub">
              1 billed call · {s.ledger?.remaining ?? 0} left today
            </span>
          </button>
        </>
      )}

      {state === "review" && (
        <>
          <button
            class="btn"
            onClick={() => void controller.autoSequence()}
            disabled={!canAutoSequence(s)}
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
            disabled={!canShadow(s)}
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
    </footer>
  );
}
