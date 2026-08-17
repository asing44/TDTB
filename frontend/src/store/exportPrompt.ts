/* exportPrompt.ts — manual-fallback prompt export.

   When the cockpit can't finish the plan itself (degraded calendar read,
   spent ledger, source failure), the user can copy a self-contained prompt
   describing today's exact state and paste it into any LLM that has
   calendar/Todoist access. Pure serialization of current state — no network,
   no billed call, never blocked by validation or source health. */

import type { AppState } from "./store";
import { effectiveAnchoredBlocks, queueState } from "./store";
import { blocksLabel, display12h, addMinutes } from "../model/time";

function line(parts: Array<string | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
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
      item.todoistId ? `(todoist · id ${item.todoistId})` : `(${item.source})`,
      item.deadline ? `· due ${item.deadline}` : null,
      item.urgency ? `· ${item.urgency}` : null,
    ]);
    if (state === "excluded") excluded.push(`- ${item.name}`);
    else if (state === "background") allDay.push(`- ${meta}`);
    else {
      const row = s.sequence?.find((r) => r.id === item.id && r.kind === "work");
      const start = row?.start ?? s.placements[item.id];
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
      "preferred, 15-minute steps fine).",
  );
  out.push("3. Show me the plan and wait for my approval before writing anything.");
  out.push(
    "4. On approval, write the plan to Todoist as timed blocks " +
      "(due date + time + duration):",
  );
  out.push(
    "   - Tasks marked `todoist · id …` already exist — UPDATE that task by " +
      "its id. Never create a duplicate. If it's recurring, reschedule with " +
      "a full datetime rather than rewriting the due string, so the " +
      "recurrence survives.",
  );
  out.push(
    "   - Vault-sourced tasks (no todoist id) have no Todoist row yet — " +
      "create them in my PHEP project, not the Inbox.",
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
