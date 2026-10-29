/* CapacitiesPlanPreview — the strip at the top of "What shows up in my plan?":
   how many Capacities items are in today's plan, how many wait for setup, how
   many a tag leaves out, and (folded) each item with its reason. Read-only;
   the data comes from planPreview's hook, shared with the item-type list. */

import type { PlanData } from "./planPreview";

const REASON_TEXT = {
  shown: "shown",
  waiting: "waiting for setup",
  "left out by tag": "left out by tag",
} as const;

export function CapacitiesPlanPreview({ data }: { data: PlanData }) {
  if (data.phase === "loading") {
    return null;
  }
  if (data.phase === "error") {
    return (
      <p class="settings-hub__muted">
        Today's plan could not be previewed right now.
      </p>
    );
  }
  return (
    <section class="plan-preview" aria-labelledby="plan-preview-title">
      <h4 id="plan-preview-title" class="settings-hub__sub">
        In today's plan
      </h4>
      <p class="plan-preview__strip">
        <b>{data.shown} shown in today's plan</b>
        <span class="settings-hub__muted">
          {" · "}
          {data.waiting} waiting · {data.leftOut} left out by tag
        </span>
      </p>
      <details class="settings-hub__details">
        <summary>See each item ({data.rows.length})</summary>
        {data.rows.length === 0 ? (
          <p class="settings-hub__muted">Nothing in the current read from Capacities yet.</p>
        ) : (
          <ul class="settings-hub__rows">
            {data.rows.map((row, i) => (
              <li key={`${row.title}:${i}`}>
                <span>{row.title}</span>
                <span class="settings-hub__muted">{REASON_TEXT[row.reason]}</span>
              </li>
            ))}
          </ul>
        )}
      </details>
    </section>
  );
}
