/* CapacitiesStructureList — the Capacities item types in the source mapping,
   each with a health dot and one plain status line. Choosing a row moves focus
   to that type's rule card in the editor below, when it is on screen. */

import type { PlanData } from "./planPreview";

const DOT_CLASS = {
  ok: "",
  "needs-setup": " plan-types__dot--warn",
  off: " plan-types__dot--off",
} as const;

export function CapacitiesStructureList({ data }: { data: PlanData }) {
  if (data.phase !== "ready") return null;
  const focusRule = (structureId: string) => {
    const card = Array.from(
      document.querySelectorAll<HTMLElement>(".capacities-rule[data-structure]"),
    ).find((el) => el.dataset.structure === structureId);
    if (!card) return;
    card.scrollIntoView?.({ block: "nearest" });
    card.focus();
  };
  return (
    <section class="plan-types" aria-label="Item types">
      {data.notYetRefreshed > 0 && (
        <p class="settings-hub__muted" role="status">
          {data.notYetRefreshed} mapped types are not yet in the latest refresh. Press Refresh now
          in Is my data up to date?
        </p>
      )}
      {data.structures.length === 0 ? (
        <p class="settings-hub__muted">No item types in the source mapping.</p>
      ) : (
        <ul class="settings-hub__rows plan-types__list">
          {data.structures.map((st) => (
            <li key={st.structureId}>
              <button
                type="button"
                class="settings-hub__link plan-types__row"
                onClick={() => focusRule(st.structureId)}
              >
                <span class={`plan-types__dot${DOT_CLASS[st.health]}`} aria-hidden="true" />
                {st.label}
              </button>
              <span class="settings-hub__muted">{st.line}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
