/* exportPrompt.ts — manual-fallback prompt export.

   When the cockpit can't finish the plan itself (degraded calendar read,
   spent ledger, source failure), the user can copy a self-contained prompt
   describing today's exact state and paste it into any LLM that has
   calendar/Todoist access. Pure serialization of current state — no network,
   no billed call, never blocked by validation or source health. */

import type { AppState } from "./store";
import type { AssignedItem } from "../model/types";
import { effectiveAnchoredBlocks, queueState } from "./store";
import { budgetTotal, localSelected } from "./allocatorView";
import {
  blocksLabel,
  display12h,
  addMinutes,
  formatBlockAmount,
  formatDurationMinutes,
} from "../model/time";

function line(parts: Array<string | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/** Native Todoist wall time carried by the source (`scheduledStart`). */
function isNativeTimedTodoist(item: AssignedItem): boolean {
  return (
    item.source === "todoist" &&
    typeof item.scheduledStart === "string" &&
    /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(item.scheduledStart)
  );
}

/** Stable source identity token. Mirrors the canonical identity the app keys
    rows on (todoist id / vault path / capacities identity): a display name
    alone is never an identity, and an identity-less row is marked so an
    external scheduler cannot mistake it for something writable. */
function sourceIdentity(item: AssignedItem): string {
  if (item.source === "todoist") {
    return item.todoistId
      ? `(todoist · id ${item.todoistId})`
      : "(todoist · unidentified)";
  }
  if (item.source === "capacities") {
    const identity = item.capacitiesIdentity ?? item.identity ?? null;
    return identity
      ? `(capacities · ${identity})`
      : "(capacities · unidentified)";
  }
  return item.path ? `(vault · ${item.path})` : "(vault · unidentified)";
}

/** Recurrence identity + the time-adjustment permission that actually
    applies. Recurring timed rows are pattern-owned commitments (T27): their
    wall time is locked, never ordinary work to reschedule. Non-recurring
    native times are protected unless the user explicitly opted in
    (`timeAdjustmentOptIns`; absent/false = protected). */
function timePermission(s: AppState, item: AssignedItem): string | null {
  if (item.isRecurring) {
    return isNativeTimedTodoist(item)
      ? `· recurring · native ${display12h(item.scheduledStart ?? null)} · time locked`
      : "· recurring · no native time";
  }
  if (!isNativeTimedTodoist(item)) return null;
  return s.timeAdjustmentOptIns[item.id] === true
    ? `· native ${display12h(item.scheduledStart ?? null)} · adjustable`
    : `· native ${display12h(item.scheduledStart ?? null)} · protected`;
}

/** Existing relationship context the scheduler should not discard: source
    labels/tags and the Obsidian parent relationship when the row carries
    them. */
function relationshipContext(item: AssignedItem): string[] {
  const out: string[] = [];
  if (item.labels?.length) out.push(`· labels ${item.labels.join(", ")}`);
  if (item.tags?.length) out.push(`· tags ${item.tags.join(", ")}`);
  if (item.relatesTo) out.push(`· relates to ${item.relatesTo}`);
  return out;
}

/** The live capacity ledger. Local edits change the local selection, so the
    server's `selected`/`remaining` arithmetic would go stale; only
    `budgetTotal` (the free+selected invariant) and `localSelected` cross —
    the same numbers the rail and the allocation meter render. */
function capacitySection(s: AppState): string[] {
  const cap = s.capacity;
  if (!cap) {
    return [
      "## Capacity",
      "- Capacity read unavailable — task room and overage are unknown. Do " +
        "not invent a capacity limit; ask me if the plan looks overfull.",
      "",
    ];
  }
  const budget = budgetTotal(s);
  const selected = localSelected(s);
  const remaining = budget - selected;
  const components = [
    `fixed ${formatBlockAmount(cap.fixed)}`,
    `anchored ${formatBlockAmount(cap.anchored)}`,
    `habits ${formatBlockAmount(cap.habits)}`,
    `mint ${formatBlockAmount(cap.mint)}`,
    `buffer ${formatBlockAmount(cap.buffer)}`,
  ].join(" · ");
  return [
    "## Capacity — my planner's live numbers (local edits included)",
    `- Day capacity: ${formatBlockAmount(cap.total)} (${components})`,
    `- Reserved before tasks: ${formatBlockAmount(cap.total - cap.availableForSelection)}`,
    `- Task room (budget for chosen tasks): ${formatBlockAmount(budget)}`,
    `- Chosen tasks (my local selection): ${formatBlockAmount(selected)}`,
    remaining >= 0
      ? `- Remaining: ${formatBlockAmount(remaining)} left`
      : `- Over by: ${formatBlockAmount(Math.abs(remaining))} — reduce durations or exclude items`,
    "",
  ];
}

export function buildDayPrompt(s: AppState): string {
  const inputs = s.inputs;
  if (!inputs) return "";
  const out: string[] = [];

  out.push(`# Schedule my day — ${inputs.validDate}`);
  out.push("");
  out.push(
    "My planner exported this because it couldn't finish the plan itself. " +
      "Act as my day scheduler using the state below.",
  );
  out.push("");

  out.push("## Frame");
  out.push(
    `- Plan window: ${display12h(inputs.time.anchor)} – ${display12h(inputs.time.effectiveEod)}` +
      ` (buffering: ${s.daySetup.buffering})`,
  );
  out.push(`- Time now: ${display12h(inputs.time.now)}`);
  const semantics = inputs.daySemantics;
  out.push(
    `- Day preset: ${semantics?.selectedPreset?.name ?? "none selected"} ` +
      `(${semantics?.resolutionSource || "default"}) · effective work ` +
      `allotment ${formatDurationMinutes(semantics?.effectiveAllotmentMinutes ?? 0)} ` +
      "— context only: it is already reflected in the capacity below, do not " +
      "add it again",
  );
  out.push("");

  // FEEDBACK-04: quarantined rows are excluded from planning on the server —
  // the fallback must not hand them to an external scheduler as fixed.
  // FEEDBACK-28 (retry): effectiveAnchoredBlocks already gates calendar
  // skips to EXPLICIT current-run intent (saveAnchoredOverride). A persisted
  // skip from a previous run therefore lands in `fixed` as a real
  // "(calendar event)" commitment — visible and planned around. Only a
  // current-run skip reaches the "skipped today" branch below. Skipped config
  // anchored blocks stay omitted: they are planning scaffolding, not real
  // commitments.
  const fixed = effectiveAnchoredBlocks(s).filter(
    (a) => a.on && !a.skipToday && a.capacityClass !== "quarantined",
  );
  const skippedCalendar = effectiveAnchoredBlocks(s).filter(
    (a) =>
      a.kind === "calendar" &&
      a.on &&
      a.skipToday &&
      a.capacityClass !== "quarantined",
  );
  out.push("## Fixed commitments — do not move these");
  if (fixed.length === 0 && skippedCalendar.length === 0) {
    out.push("- (none known — see warnings)");
  }
  for (const a of fixed) {
    const window =
      a.kind === "window" && a.start && a.end && s.daySetup.anchored[a.id]?.time == null
        ? `anytime ${display12h(a.start)}–${display12h(a.end)}`
        : `${display12h(a.start)}`;
    out.push(
      line([
        `- ${a.name}:`,
        window,
        `· ${a.durationMin}min`,
        a.kind === "calendar" ? "(calendar event)" : null,
      ]),
    );
  }
  for (const a of skippedCalendar) {
    out.push(
      line([
        `- ${a.name}:`,
        `${display12h(a.start)}`,
        `· ${a.durationMin}min`,
        "(calendar event · skipped today — not planned around)",
      ]),
    );
  }
  out.push("");

  out.push(...capacitySection(s));

  const placed: string[] = [];
  const toPlace: string[] = [];
  const allDay: string[] = [];
  const excluded: string[] = [];
  for (const item of inputs.assigned) {
    const blocks = s.overrides[item.id]?.blocks ?? item.blocks;
    const state = queueState(s, item.id);
    const meta = line([
      `${item.name} —`,
      blocksLabel(blocks),
      sourceIdentity(item),
      timePermission(s, item),
      item.deadline ? `· due ${item.deadline}` : null,
      item.urgency ? `· ${item.urgency}` : null,
      ...relationshipContext(item),
    ]);
    if (state === "excluded") excluded.push(`- ${item.name}`);
    else if (state === "background") allDay.push(`- ${meta}`);
    else {
      const row = s.sequence?.find((r) => r.id === item.id && r.kind === "work");
      // Placement precedence is unchanged: staged row, then manual placement,
      // then — only for a time the source locks (recurring, or a protected
      // native time) — the row's own native wall time. An opted-in native
      // time is freely placeable and must not inherit its pin.
      const nativeLocked =
        isNativeTimedTodoist(item) &&
        (item.isRecurring || s.timeAdjustmentOptIns[item.id] !== true)
          ? item.scheduledStart
          : null;
      const start = row?.start ?? s.placements[item.id] ?? nativeLocked;
      if (start) {
        placed.push(`- ${display12h(start)}–${display12h(addMinutes(start, blocks * 30))} ${meta}`);
      } else toPlace.push(`- ${meta}`);
    }
  }

  out.push(`## Tasks to place (${toPlace.length})`);
  out.push(...(toPlace.length ? toPlace : ["- (none)"]));
  out.push("");
  if (placed.length) {
    out.push("## Already placed — keep unless they conflict");
    out.push(
      `- Sequence status: ${
        s.sequence
          ? s.seqPhase === "valid"
            ? "current (validated) — keep these placements"
            : "dirty — local edits since the last valid sequence; keep these placements and their order, but treat start times as advisory until resequenced"
          : "manual placements only (no staged sequence)"
      }`,
    );
    out.push(...placed);
    out.push("");
  }
  if (allDay.length) {
    out.push("## All-day — no time slot, just keep visible");
    out.push(...allDay);
    out.push("");
  }
  if (excluded.length) {
    out.push("## Excluded today — ignore");
    out.push(...excluded);
    out.push("");
  }

  const captures = s.daySetup.captures;
  if (captures.intention || captures.forMeegy || captures.stoic) {
    out.push("## Captures");
    if (captures.intention) out.push(`- Intention: ${captures.intention}`);
    if (captures.forMeegy) out.push(`- For Meegy: ${captures.forMeegy}`);
    if (captures.stoic) out.push(`- Stoic: ${captures.stoic}`);
    out.push("");
  }

  if (inputs.sourceWarnings.length) {
    out.push("## Source warnings — data below may be incomplete");
    out.push(...inputs.sourceWarnings.map((w) => `- ${w}`));
    out.push("");
  }

  out.push("## Instructions");
  out.push(
    "1. If you have calendar access, check my real calendar for today first — " +
      "especially if a warning above says busy blocks are missing.",
  );
  out.push(
    "2. Propose a timed plan for the tasks inside the plan window, around the " +
      "fixed commitments, using the durations given (30-minute alignment " +
      "preferred, 15-minute steps fine). Respect the plan's own capacity: if " +
      "the plan runs over, reduce durations or propose exclusions instead of " +
      "adding time.",
  );
  out.push("3. Show me the plan and wait for my approval before writing anything.");
  out.push(
    "4. On approval, write the plan to Todoist as timed blocks " +
      "(due date + time + duration):",
  );
  out.push(
    "   - Tasks marked `todoist · id …` already exist — UPDATE that task by " +
      "its id. Never create a duplicate.",
  );
  out.push(
    "   - Tasks marked `recurring` are existing recurring commitments, not " +
      "work to place. A shown native time is locked (`· time locked`): do " +
      "not move it. If I explicitly approve moving one, reschedule with a " +
      "full datetime rather than rewriting the due string, so the " +
      "recurrence survives.",
  );
  out.push(
    "   - Tasks marked `protected` keep their shown native time unless I " +
      "explicitly approve a move; tasks marked `adjustable` may be moved as " +
      "part of the plan.",
  );
  out.push(
    "   - Vault-sourced tasks (`vault · path`) have no Todoist row yet — " +
      "create them in my PHEP project, not the Inbox.",
  );
  out.push(
    "   - Capacities-sourced tasks (`capacities · …`) have no write route " +
      "from this prompt — include them in the plan only; do not create or " +
      "update them anywhere.",
  );
  out.push(
    "   - Rows marked `unidentified` have no stable source identity — plan " +
      "around them, but do not write them anywhere.",
  );
  out.push(
    // 2026-07-27: this step used to ask for "one event per placed work
    // block" too, which the app's own manifest never does (work rows are
    // Step A Todoist writes; only zones/template blocks/anchored lifestyle
    // blocks reach the calendar). An external scheduler following it wrote
    // eight work blocks onto ⬜ Blocks that then had to be hand-deleted.
    // The fallback must mirror the real write contract, not invent a wider one.
    "5. Also on approval, publish ONLY the fixed commitments listed above to " +
      "my \"⬜ Blocks\" calendar (at their shown or agreed times) — except " +
      "rows marked \"(calendar event)\", which already exist, and rows marked " +
      "\"skipped today\", which are NOT planned around and must not be " +
      "published. Placed work blocks do NOT get calendar events: their timed " +
      "Todoist entries from step 4 are the schedule. This mirrors what my " +
      "planner itself commits.",
  );
  out.push(
    "6. Write only to the \"⬜ Blocks\" calendar — never modify events on any " +
      "other calendar.",
  );

  return out.join("\n");
}
