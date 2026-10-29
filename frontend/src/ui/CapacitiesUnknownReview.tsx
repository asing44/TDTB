/* CapacitiesUnknownReview — the unknown-candidate review surface for the
   Connections screen.

   Contract this surface keeps:
   - Candidates come from the typed ``capacities_intake`` diagnostic block on
     the current plan inputs (stable canonical identities + the server's own
     review-reason keys). The durable selection record is read separately so
     the acknowledgement flags and revision are authoritative.
   - Selection is by STABLE IDENTITY, never by name. Only a canonical
     ``capacities:{space}:{structure}:{object}`` identity is ever selectable
     or sent; a malformed identity is refused client-side.
   - Review reasons are WARN-ONLY for planning (owner decision 2026-10-29): a
     candidate that carries them stays visible and flagged, but it is
     selectable without a prior acknowledgement. The ``acknowledged`` flag is
     optional audit metadata, never a precondition.
   - A save with no loaded revision is refused ("unknown revision"), and a
     real 409 conflict keeps the local selection and reports both revisions.
   - "Okay to plan all" selects every listed unacknowledged review candidate
     in ONE selection request (same route, same body shape, same revision and
     409 handling as the per-row save), which promotes them into the plan. It
     never names a non-review row, a non-canonical identity, or an
     already-acknowledged row.
   - The intake diagnostic block is rendered truthfully (state, generation,
     coverage); an absent block is stated, never fabricated. */

import { useEffect, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { capacitiesSelectionsConflictOf } from "../adapters/api";
import { isCanonicalCapacitiesIdentity } from "../adapters/wire";
import type { CapacitiesIntakeCandidate, CapacitiesSelections } from "../model/types";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

interface DraftEntry {
  selected: boolean;
  acknowledged: boolean;
}

function initDraft(
  selections: CapacitiesSelections | null,
  candidates: CapacitiesIntakeCandidate[],
): Record<string, DraftEntry> {
  const records = new Map((selections?.selections ?? []).map((s) => [s.identity, s]));
  const draft: Record<string, DraftEntry> = {};
  for (const candidate of candidates) {
    const record = records.get(candidate.identity);
    draft[candidate.identity] = {
      selected: record ? true : candidate.selected,
      acknowledged: record ? record.acknowledged : false,
    };
  }
  return draft;
}

const INTAKE_STATE_TEXT: Record<string, string> = {
  not_configured: "No Capacities source is configured.",
  refresh_required: "A refresh is required before candidates can be reviewed.",
  unavailable: "The direct intake is unavailable; no Capacities rows are being served.",
  degraded: "The direct intake is degraded; rows are served with warnings.",
  ok: "The direct intake is healthy.",
};

export function CapacitiesUnknownReview({ active }: { active: boolean }) {
  const s = useAppState();
  const { controller } = useApp();
  const intake = s.inputs?.capacitiesIntake;
  const candidates = intake?.unassignedCandidates ?? [];
  const [selections, setSelections] = useState<CapacitiesSelections | null>(null);
  const [draft, setDraft] = useState<Record<string, DraftEntry>>({});
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ expectedRevision: number; currentRevision: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const [ackBusy, setAckBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = async () => {
    setPhase("loading");
    setError(null);
    try {
      const loaded = await controller.loadCapacitiesSelections();
      setSelections(loaded);
      setDraft(initDraft(loaded, candidates));
      setConflict(null);
      setPhase("ready");
    } catch (e) {
      setPhase("error");
      setError(messageOf(e));
    }
  };

  useEffect(() => {
    if (!active) return;
    void load();
  }, [active, controller]);

  if (!active) return null;

  const needsAck = (candidate: CapacitiesIntakeCandidate) => candidate.reviewReasons.length > 0;

  // Selecting a review-reason candidate needs no acknowledgement: the review
  // notice is advisory for planning, and the flag is audit metadata only.
  const toggleSelect = (candidate: CapacitiesIntakeCandidate) => {
    const current = draft[candidate.identity] ?? { selected: false, acknowledged: false };
    setDraft({ ...draft, [candidate.identity]: { ...current, selected: !current.selected } });
  };

  const toggleAck = (candidate: CapacitiesIntakeCandidate) => {
    const current = draft[candidate.identity] ?? { selected: false, acknowledged: false };
    setDraft({ ...draft, [candidate.identity]: { ...current, acknowledged: !current.acknowledged } });
  };

  const save = async () => {
    if (!selections) {
      setError("Selections are not loaded; reload before saving.");
      return;
    }
    const currentIds = new Set(selections.selections.map((entry) => entry.identity));
    const select: Array<{ identity: string; acknowledge: boolean }> = [];
    const deselect: string[] = [];
    for (const candidate of candidates) {
      if (!isCanonicalCapacitiesIdentity(candidate.identity)) continue;
      const entry = draft[candidate.identity];
      if (!entry) continue;
      if (entry.selected) {
        select.push({ identity: candidate.identity, acknowledge: entry.acknowledged });
      } else if (currentIds.has(candidate.identity)) {
        deselect.push(candidate.identity);
      }
    }
    if (select.length === 0 && deselect.length === 0) {
      setNotice("No selection changes to save.");
      return;
    }
    setSaving(true);
    setError(null);
    setNotice(null);
    setConflict(null);
    try {
      const { selections: saved, refreshError } = await controller.saveCapacitiesSelections({
        expectedRevision: selections.revision,
        select,
        deselect,
      });
      setSelections(saved);
      setDraft(initDraft(saved, candidates));
      setNotice(
        refreshError
          ? `Selections saved; planning refresh failed: ${refreshError}`
          : "Selections saved; plan inputs refreshed.",
      );
      setPhase("ready");
    } catch (e) {
      const asConflict = capacitiesSelectionsConflictOf(e);
      if (asConflict) {
        setConflict(asConflict);
      } else {
        setError(messageOf(e));
      }
    } finally {
      setSaving(false);
    }
  };

  // The one-click control works over exactly these rows: review-reason
  // candidates the draft still shows unacknowledged, restricted to the
  // canonical identities the route accepts. It SELECTS them (promoting them
  // into the plan) while stamping ``acknowledge: true`` as the audit flag.
  const unacknowledgedReviewRows = candidates.filter((candidate) => {
    if (!needsAck(candidate)) return false;
    if (!isCanonicalCapacitiesIdentity(candidate.identity)) return false;
    const entry = draft[candidate.identity];
    return entry !== undefined && !entry.acknowledged;
  });

  const okayToPlanAll = async () => {
    if (!selections) {
      setError("Selections are not loaded; reload before saving.");
      return;
    }
    if (unacknowledgedReviewRows.length === 0) return;
    const count = unacknowledgedReviewRows.length;
    setAckBusy(true);
    setError(null);
    setNotice(null);
    setConflict(null);
    try {
      const { selections: saved, refreshError } = await controller.saveCapacitiesSelections({
        expectedRevision: selections.revision,
        select: unacknowledgedReviewRows.map((candidate) => ({
          identity: candidate.identity,
          acknowledge: true,
        })),
        deselect: [],
      });
      setSelections(saved);
      setDraft(initDraft(saved, candidates));
      setNotice(
        refreshError
          ? `Added ${count} review rows to today's plan; planning refresh failed: ${refreshError}`
          : `Added ${count} review rows to today's plan; plan inputs refreshed.`,
      );
      setPhase("ready");
    } catch (e) {
      const asConflict = capacitiesSelectionsConflictOf(e);
      if (asConflict) {
        setConflict(asConflict);
      } else {
        setError(messageOf(e));
      }
    } finally {
      setAckBusy(false);
    }
  };

  const unconfigured = selections?.spaceId === null || (intake !== undefined && intake.state === "not_configured");

  return (
    <section
      class="setup-section capacities-settings__section"
      aria-labelledby="capacities-sec-unknown"
      data-settings-section="unknown"
    >
      <div class="setup-section__head">
        <h3 id="capacities-sec-unknown">Unknown candidate review</h3>
        {selections && (
          <span class="capacities-settings__revision">Selections revision {selections.revision}</span>
        )}
      </div>
      <div class="setup-section__body">
        <p class="capacities-settings__hint">
          These are Capacities objects the intake could not place on its own.
          Selecting one promotes it by stable identity; a candidate that carries
          review reasons stays flagged, and selecting it is enough to plan it.
        </p>

        {intake ? (
          <dl class="capacities-status__grid">
            <div class="capacities-status__cell">
              <dt>Intake</dt>
              <dd>{INTAKE_STATE_TEXT[intake.state] ?? intake.state}</dd>
            </div>
            <div class="capacities-status__cell">
              <dt>Generation</dt>
              <dd>{intake.generation ?? "—"}</dd>
            </div>
            <div class="capacities-status__cell">
              <dt>Coverage</dt>
              <dd>
                {intake.coverage.evaluated} / {intake.coverage.members} evaluated ·{" "}
                {intake.coverage.malformed} malformed · {intake.coverage.unreadable} unreadable
              </dd>
            </div>
          </dl>
        ) : (
          <p class="capacities-settings__empty" role="status">
            This build reports no direct-intake block, so there are no candidates to review.
          </p>
        )}

        {phase === "loading" && (
          <p class="capacities-settings__state" role="status">Loading selections…</p>
        )}
        {phase === "error" && (
          <section class="capacities-settings__error" role="alert">
            <strong>Selections could not be read</strong>
            <span>{error}</span>
            <button class="btn" onClick={() => void load()}>Reload selections</button>
          </section>
        )}

        {phase === "ready" && unconfigured && (
          <p class="capacities-settings__empty" role="status">
            No Capacities source is configured; there is nothing to select.
          </p>
        )}

        {phase === "ready" && !unconfigured && (
          <>
            {conflict && (
              <div class="capacities-settings__save-error" role="alert">
                <span>
                  Selections changed since they were read (stored revision {conflict.currentRevision},
                  expected {conflict.expectedRevision}). Your selection is preserved; reload to review
                  the newer record before saving.
                </span>
                <button class="btn" onClick={() => void load()}>Reload selections</button>
              </div>
            )}
            {error && !conflict && (
              <div class="capacities-settings__save-error" role="alert">
                <span>{error}</span>
                <button class="btn" onClick={() => void load()}>Reload selections</button>
              </div>
            )}
            {notice && <div class="capacities-rule__notice" role="status">{notice}</div>}

            {candidates.length === 0 ? (
              <p class="capacities-settings__empty">No unknown candidates are waiting for review.</p>
            ) : (
              <>
              {unacknowledgedReviewRows.length >= 2 && (
                <button
                  class="btn"
                  disabled={ackBusy || saving || selections === null}
                  aria-label={`Okay to plan all ${unacknowledgedReviewRows.length} review rows`}
                  onClick={() => void okayToPlanAll()}
                >
                  {ackBusy
                    ? "Planning…"
                    : `Okay to plan all (${unacknowledgedReviewRows.length})`}
                </button>
              )}
              <div class="capacities-object-list" role="group" aria-label="Unknown Capacities candidates">
                {candidates.map((candidate) => {
                  const entry = draft[candidate.identity] ?? { selected: false, acknowledged: false };
                  const canonical = isCanonicalCapacitiesIdentity(candidate.identity);
                  const requiresAck = needsAck(candidate);
                  return (
                    <div class="capacities-object-row" key={candidate.identity}>
                      <div class="capacities-object-row__identity">
                        <strong>{candidate.name || candidate.identity}</strong>
                        <code>{candidate.identity}</code>
                        {requiresAck && (
                          <small>Review reasons: {candidate.reviewReasons.join(", ")}</small>
                        )}
                        {!canonical && <small class="field-error">Not a canonical identity — cannot be selected.</small>}
                      </div>
                      <div class="capacities-unknown__controls">
                        <label class="capacities-object-row__toggle">
                          <input
                            type="checkbox"
                            checked={entry.acknowledged}
                            disabled={!requiresAck}
                            aria-label={`Acknowledge ${candidate.identity}`}
                            onChange={() => toggleAck(candidate)}
                          />
                          <span>Acknowledged</span>
                        </label>
                        <label class="capacities-object-row__toggle">
                          <input
                            type="checkbox"
                            checked={entry.selected}
                            disabled={!canonical}
                            aria-label={`Select ${candidate.identity}`}
                            onChange={() => toggleSelect(candidate)}
                          />
                          <span>{entry.selected ? "Selected" : "Not selected"}</span>
                        </label>
                      </div>
                    </div>
                  );
                })}
              </div>
              </>
            )}

            <div class="editor__actions setup__actions">
              <button
                class="btn btn--primary"
                disabled={saving || ackBusy || selections === null}
                onClick={() => void save()}
              >
                {saving ? "Saving…" : "Save selections"}
              </button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
