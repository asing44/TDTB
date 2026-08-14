import { useState } from "preact/hooks";
import { buildExecutionModel, type ExecutionMoment } from "../model/execution";
import { display12h } from "../model/time";
import { canRuntimeAct } from "../store/store";
import { useApp, useAppState } from "./context";

/** T20: direct one-tap runtime verbs on a committed plan item — no modal
    chains. Delete is two taps on the SAME control (arm -> confirm), never a
    dialog. Mounted only when a live commit exists (the server resolves the
    item against today's committed manifest). */
function EntryActions({ entryId }: { entryId: string }) {
  const s = useAppState();
  const { controller } = useApp();
  const [armed, setArmed] = useState(false);
  if (!canRuntimeAct(s)) return null;
  return (
    <div class="runtime-actions" role="group" aria-label={`Actions for ${entryId}`}>
      <button
        class="btn btn--ghost runtime-actions__btn"
        aria-label={`Complete ${entryId}`}
        title="Complete — updates the source, undoable"
        onClick={() => void controller.runtimeAction("complete", entryId)}
      >
        ✓
      </button>
      <button
        class="btn btn--ghost runtime-actions__btn"
        aria-label={`Remove ${entryId} from today`}
        title="Remove from today — source and assignment stay"
        onClick={() => void controller.runtimeAction("remove_from_today", entryId)}
      >
        –
      </button>
      <button
        class={`btn btn--ghost runtime-actions__btn ${armed ? "runtime-actions__btn--armed" : ""}`}
        aria-label={
          armed
            ? `Confirm permanent delete of ${entryId}`
            : `Delete ${entryId} permanently`
        }
        title="Permanent delete — source is deleted (trash/Todoist), undoable once"
        onClick={() => {
          if (armed) {
            setArmed(false);
            void controller.runtimeAction("delete_permanent", entryId);
          } else {
            setArmed(true);
          }
        }}
        onBlur={() => setArmed(false)}
      >
        {armed ? "Confirm delete" : "🗑"}
      </button>
    </div>
  );
}

function MomentCard({ label, moment }: { label: string; moment: ExecutionMoment | null }) {
  if (!moment) {
    return (
      <article class="execution-card execution-card--quiet">
        <h3>{label}</h3>
        <div class="execution-card__name">Clear</div>
        <div class="execution-card__meta">No scheduled block</div>
      </article>
    );
  }
  return (
    <article
      class={`execution-card ${moment.allowedOverlap ? "execution-card--cluster" : ""}`}
      aria-label={moment.allowedOverlap ? "Allowed overlap cluster" : undefined}
    >
      <h3>{label}</h3>
      <div class="execution-card__time">
        {display12h(moment.start)} – {display12h(moment.end)}
      </div>
      {moment.entries.map((entry, index) => (
        <div class="execution-card__entry" key={entry.id}>
          <div class="execution-card__name">
            {index === 0 || !moment.allowedOverlap ? entry.name : `↳ ${entry.name}`}
          </div>
          <div class="execution-card__meta">
            {entry.kind === "calendar" ? "Calendar · immutable" : entry.kind}
          </div>
          {entry.kind === "work" && <EntryActions entryId={entry.id} />}
        </div>
      ))}
      {moment.allowedOverlap && (
        <div class="execution-card__grant">Allowed overlap · {moment.overlapReason}</div>
      )}
    </article>
  );
}

export function ExecutionView() {
  const s = useAppState();
  if (!s.inputs) return null;
  // T12e (brief problem 5): NOW/NEXT was dead space every pre-sequence
  // morning ("Clear · No scheduled block"). The surface only means something
  // once a live commit exists — the T20 runtime verbs need it then; before
  // then the rail + table are the whole page. Banners moved to the footer.
  if (!canRuntimeAct(s)) return null;
  const model = buildExecutionModel({
    inputs: s.inputs,
    sequence: s.sequence,
    overlapGrants: s.overlapGrants,
    planningConfigFingerprint:
      s.planningConfigFingerprint ?? s.inputs.planningConfigFingerprint,
  });

  return (
    <section class="execution" aria-label="Now and next">
      <div class="execution__head">
        <h2>Today</h2>
      </div>
      <div class="execution__cards">
        <MomentCard label="Now" moment={model.now} />
        <MomentCard label="Next" moment={model.next} />
      </div>
      {model.zones.length > 0 && (
        <div class="execution__zones" aria-label="Template zones">
          {model.zones.map((zone) => (
            <span class="execution-zone" key={`${zone.name}:${zone.start}:${zone.end}`}>
              {zone.name} · {display12h(zone.start)}–{display12h(zone.end)}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
