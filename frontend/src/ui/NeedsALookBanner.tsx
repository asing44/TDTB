/* NeedsALookBanner — ONE warn-only banner above the plan queue.

   Contract this surface keeps (owner decision 2026-10-29: review reasons are
   warn-only where the owner plans):
   - The rows come from the SAME data the unknown-candidate review surface
     reads: the typed ``capacities_intake.unassigned_candidates`` list plus the
     durable selection record (GET /capacities/selections). So "N items need a
     look" counts exactly what that surface counts: a candidate carrying server
     review reasons, with a canonical identity, that the stored record has not
     acknowledged.
   - Warn-only and non-blocking: nothing here gates planning, committing, or a
     plan row. "Okay to plan" / "Okay to plan all (N)" reuse the ONE batched
     selection call the review surface already uses (POST /capacities/selections
     with ``acknowledge: true`` and ``deselect: []``) through the controller,
     so there is no second write path and no second refresh path — the
     controller re-earns plan inputs after the save.
   - Hidden when nothing needs a look, and while the selection record cannot be
     read: without that record a count would be a guess, and the review surface
     already reports the read error itself.
   - There is no "Leave out" verb here: a candidate that was never selected has
     no selection record to withdraw, so leaving it out is already the default
     (it simply stays out of today's plan). */

import { useEffect, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { capacitiesSelectionsConflictOf } from "../adapters/api";
import { isCanonicalCapacitiesIdentity } from "../adapters/wire";
import type { CapacitiesIntakeCandidate, CapacitiesSelections } from "../model/types";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Titles listed inline before the rest fold into one "+M more" tail. */
export const NEEDS_A_LOOK_LIST_CAP = 5;

/** The rows that need a look: a canonical candidate carrying server review
    reasons that the stored selection record has not acknowledged. The review
    surface counts its own "Okay to plan all" control over the same set, so the
    two counts cannot drift. */
export function needsALookRows(
  candidates: CapacitiesIntakeCandidate[],
  acknowledged: ReadonlySet<string>,
): CapacitiesIntakeCandidate[] {
  return candidates.filter(
    (candidate) =>
      candidate.reviewReasons.length > 0 &&
      isCanonicalCapacitiesIdentity(candidate.identity) &&
      !acknowledged.has(candidate.identity),
  );
}

/** Identities the durable record already acknowledges (audit flag only). */
export function acknowledgedIdentities(
  selections: CapacitiesSelections | null,
): Set<string> {
  return new Set(
    (selections?.selections ?? [])
      .filter((record) => record.acknowledged)
      .map((record) => record.identity),
  );
}

/** A candidate that carries review reasons and could actually be selected. */
function reviewFlagged(candidates: CapacitiesIntakeCandidate[]) {
  return candidates.filter(
    (candidate) =>
      candidate.reviewReasons.length > 0 &&
      isCanonicalCapacitiesIdentity(candidate.identity),
  );
}

function rowLabel(candidate: CapacitiesIntakeCandidate): string {
  return candidate.name || candidate.identity;
}

export function NeedsALookBanner() {
  const s = useAppState();
  const { controller } = useApp();
  const candidates = s.inputs?.capacitiesIntake?.unassignedCandidates ?? [];
  const [selections, setSelections] = useState<CapacitiesSelections | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Only a flagged candidate can need a look, so an ordinary day (the common
  // case) never reads the selection record at all. The identity key re-reads
  // it when the flagged set itself changes (a refresh or a promotion).
  const flagged = reviewFlagged(candidates);
  const flaggedKey = flagged.map((candidate) => candidate.identity).join("\n");
  useEffect(() => {
    if (flaggedKey === "") {
      setSelections(null);
      return;
    }
    let cancelled = false;
    void controller
      .loadCapacitiesSelections()
      .then((loaded) => {
        if (!cancelled) setSelections(loaded);
      })
      .catch(() => {
        /* Unreadable record: stay hidden rather than show a guessed count;
           the review surface reports the read failure in full. */
        if (!cancelled) setSelections(null);
      });
    return () => {
      cancelled = true;
    };
  }, [controller, flaggedKey]);

  const pending = selections === null ? [] : needsALookRows(candidates, acknowledgedIdentities(selections));
  if (pending.length === 0) return null;

  const shown = pending.slice(0, NEEDS_A_LOOK_LIST_CAP);
  const rest = pending.length - shown.length;

  const okayRows = async (rows: CapacitiesIntakeCandidate[]) => {
    if (!selections || rows.length === 0) return;
    const count = rows.length;
    const noun = count === 1 ? "item" : "items";
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const { selections: saved, refreshError } = await controller.saveCapacitiesSelections({
        expectedRevision: selections.revision,
        select: rows.map((row) => ({ identity: row.identity, acknowledge: true })),
        deselect: [],
      });
      setSelections(saved);
      setNotice(
        refreshError
          ? `Added ${count} ${noun}; planning refresh failed: ${refreshError}`
          : `Added ${count} ${noun} to today's plan.`,
      );
    } catch (e) {
      const conflict = capacitiesSelectionsConflictOf(e);
      if (conflict) {
        setError(
          `Selections changed since they were read (stored revision ` +
            `${conflict.currentRevision}, expected ${conflict.expectedRevision}); ` +
            `nothing was changed and the record has been re-read.`,
        );
        // Re-read so a second click writes against the current revision.
        try {
          setSelections(await controller.loadCapacitiesSelections());
        } catch {
          setSelections(null);
        }
      } else {
        setError(messageOf(e));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div class="capacities-rule__notice capacities-look" role="status" data-needs-a-look>
      <p class="capacities-look__head">
        <strong>
          {pending.length} {pending.length === 1 ? "item needs" : "items need"} a look
        </strong>{" "}
        Capacities flagged them and nothing is blocked — planning and committing
        stay available. Okay to plan adds a row to today's plan.
      </p>
      <ul class="capacities-look__list">
        {shown.map((candidate) => (
          <li key={candidate.identity}>
            <span class="capacities-look__name">{rowLabel(candidate)}</span>
            <button
              class="btn"
              type="button"
              disabled={busy}
              aria-label={`Okay to plan ${rowLabel(candidate)}`}
              onClick={() => void okayRows([candidate])}
            >
              Okay to plan
            </button>
          </li>
        ))}
        {rest > 0 && <li class="capacities-look__more">+{rest} more</li>}
      </ul>
      <div class="capacities-look__actions">
        <button
          class="btn btn--primary"
          type="button"
          disabled={busy}
          aria-label={`Okay to plan all ${pending.length} ${pending.length === 1 ? "item" : "items"}`}
          onClick={() => void okayRows(pending)}
        >
          {busy ? "Adding…" : `Okay to plan all (${pending.length})`}
        </button>
      </div>
      {notice && (
        <p class="capacities-look__note" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p class="capacities-look__error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
