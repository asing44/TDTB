/* CommitSummary — the Commit step's three summary cards (Todoist / Calendar /
   Morning prompts). Counts are derived only from data the app already holds:
   the shadow preview's entries and the day's prompt captures. Presentation
   only; nothing here writes or arms a commit. */

import type { Captures, PromptDisabled, ShadowDiff } from "../model/types";
import "./commit-r3.css";

export interface CommitCounts {
  todoistUpdates: number;
  calendarEvents: number;
  /** Where the calendar writes land: capacities ids are not BusyCal. */
  calendarDest: string;
  /** Prompts that will export to the Todoist Inbox, in display order. */
  promptsExported: string[];
  /** Prompts with no text or switched off — nothing is exported for them. */
  promptsLeft: number;
}

/** Active (non no-op) writes per surface plus the prompt tally. A prompt
    exports when it has text and, for the two toggleable ones, is not switched
    off — the same include/skip rule the day-setup captures use. */
export function commitCounts(
  shadow: ShadowDiff | null,
  captures: Captures,
  disabled: PromptDisabled | undefined,
): CommitCounts {
  const active = (system: "todoist" | "calendar") =>
    (shadow?.entries ?? []).filter(
      (e) => e.system === system && e.classification !== "no-op",
    ).length;
  const calendarRows = (shadow?.entries ?? []).filter(
    (e) => e.system === "calendar" && e.classification !== "no-op",
  );
  const calendarDest =
    calendarRows.length > 0 && calendarRows.every((e) => e.idOrPath.startsWith("capacities:"))
      ? "Capacities"
      : "BusyCal Blocks";
  const prompts: Array<[string, boolean]> = [
    ["Reminder", captures.intention.trim() !== ""],
    ["For Meegy", captures.forMeegy.trim() !== "" && !disabled?.forMeegy],
    ["Stoic", captures.stoic.trim() !== "" && !disabled?.stoic],
  ];
  const promptsExported = prompts.filter(([, on]) => on).map(([name]) => name);
  return {
    todoistUpdates: active("todoist"),
    calendarEvents: active("calendar"),
    calendarDest,
    promptsExported,
    promptsLeft: prompts.length - promptsExported.length,
  };
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

export function CommitSummary({ counts }: { counts: CommitCounts }) {
  const p = counts.promptsExported;
  return (
    <div class="commit-cards" aria-label="What will be written">
      <section class="commit-card" aria-label="Todoist">
        <div class="commit-card__label">Todoist</div>
        <div class="commit-card__count">
          {counts.todoistUpdates} {plural(counts.todoistUpdates, "update", "updates")}
        </div>
        <div class="commit-card__note">schedule times on existing tasks</div>
      </section>
      <section class="commit-card" aria-label="Calendar">
        <div class="commit-card__label">Calendar · {counts.calendarDest}</div>
        <div class="commit-card__count">
          {counts.calendarEvents} new {plural(counts.calendarEvents, "event", "events")}
        </div>
        <div class="commit-card__note">one per placed task block</div>
      </section>
      <section class="commit-card" aria-label="Morning prompts">
        <div class="commit-card__label">Morning prompts</div>
        <div class="commit-card__count">
          {p.length} {plural(p.length, "prompt", "prompts")}
        </div>
        <div class="commit-card__note">
          {p.length > 0 ? `${p.join(", ")} → Todoist Inbox` : "none to export"}
          {counts.promptsLeft > 0 ? ` · ${counts.promptsLeft} left impromptu` : ""}
        </div>
      </section>
    </div>
  );
}
