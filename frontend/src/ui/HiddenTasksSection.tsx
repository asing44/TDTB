/* HiddenTasksSection — "Tasks I hid": the Todoist tasks hidden from planning
   every day, each with Show again. A controlled list: the owning panel keeps
   the draft and saves it with the tag exclusions (one full replacement). */

import type { TodoistExclusionEntry } from "../model/types";

/** Short label for a hidden task's added_at timestamp (e.g. "Mar 4, 2026"). */
function shortAddedDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(
    date,
  );
}

export function HiddenTasksSection({
  entries,
  onShowAgain,
}: {
  entries: readonly TodoistExclusionEntry[];
  onShowAgain: (id: string) => void;
}) {
  return (
    <section
      class="setup-section tag-exclusion-settings__section"
      aria-labelledby="hidden-tasks-title"
    >
      <div class="setup-section__head">
        <h3 id="hidden-tasks-title">Tasks I hid</h3>
        <span class="tag-exclusion-settings__revision">
          {entries.length === 0 ? "None" : `${entries.length} hidden`}
        </span>
      </div>
      <div class="setup-section__body">
        {entries.length === 0 ? (
          <p class="tag-exclusion-settings__empty">
            Nothing hidden. Use ⋯ &gt; Always exclude on a task to hide it every day.
          </p>
        ) : (
          <>
            <ul class="settings-hub__rows">
              {entries.map((entry) => (
                <li key={entry.id}>
                  <span>
                    <strong>{entry.name}</strong>{" "}
                    <span class="settings-hub__muted">{shortAddedDate(entry.addedAt)}</span>
                  </span>
                  <button
                    type="button"
                    class="btn btn--sm"
                    aria-label={`Show again ${entry.name}`}
                    onClick={() => onShowAgain(entry.id)}
                  >
                    Show again
                  </button>
                </li>
              ))}
            </ul>
            <p class="settings-hub__muted">Press Save tag exclusions to apply.</p>
          </>
        )}
      </div>
    </section>
  );
}
