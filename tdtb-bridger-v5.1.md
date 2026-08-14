---
name: tdtb-bridger
description: "Sequences today's already-staged Todoist tasks into time blocks and writes back via Todoist's native scheduling. Runs AFTER Coda staging + Make.com PUSH have set initial times — this skill is the re-sequencer, not the scheduler. Pulls today's timed Todoist tasks + Google Calendar events, ranks deterministically, renders an HTML day-timeline plan artifact, and requires a single inline confirm before committing writes. Trigger on phrases like \"prep my TDTB\", \"plan my day\", \"sequence my blocks\", \"daily planning\", \"TDTB run\", or whenever Adam wants today's blocks re-sequenced after a Coda push. Trigger on \"orphan check\" for the read-only Tana Attention scan alone (no writes, no confirm)."
---

# TDTB Bridger — Cowork

## Core Model

**Todoist tasks are the blocks.** BusyCal renders timed Todoist tasks as
calendar events. Daily planning = assigning times to Todoist tasks via
`due_string`.

Tana provides *context* (parent effort, orphan awareness) but is NOT the
primary source of schedulable items. Only reach into Tana when the orphan
scan is explicitly invoked.

## Pipeline Position

This skill orchestrates the TDTB v5.1 pipeline at two points (steps 0 and 2):

0. **Kickoff (this skill, `kickoff TDTB cowork`)** — start-of-day 7-day
   week brief. **Compact inline markdown only.** Read-only.
1. **TDTB Shortcut (Apple Shortcut)** — collects planning inputs, stages
   tasks in Coda (caps enforced: `deepCap=4` for Type=Efforts,
   `mixedCap=3` for Type=Tasks), then triggers Make.com PUSH (scenario
   4486190). PUSH writes `due_string` + duration to Todoist — default
   time today 2:00 PM (or `start pull` if present).
2. **Sequence (this skill, `prep my TDTB cowork`)** — alignment check,
   then re-times pushed tasks intelligently around fixed calendar events.
   Produces an **HTML day-timeline visualization** for review before
   committing writes. **Replaces the manual BusyCal rearrangement step.**
3. **BusyCal** — renders timed Todoist tasks as calendar blocks.

When the sequencer is invoked, today's Todoist tasks have already been
staged and pushed via the Shortcut. They have a duration and a default
time. The sequencer's job is to replace the default time with a sequenced
time that respects fixed commitments and priority order.

**Over-cap surfacing:** Coda's caps SHOULD prevent over-staging, but the
button-disable enforcement is partial (some Stage actions bypass the
disabled-if formula). If today's pushed tasks include more `Type=Efforts`
than `deepCap` or more `Type=Tasks` than `mixedCap`, the bridger surfaces
this in the plan as `Deep: 6/4 ⚠ over` and bumps the surplus by
lowest priority before sequencing.

## Duration Labels (Block Length)

A task is a **timed candidate** if it has either:
- One of the emoji duration labels below, OR
- A native Todoist `duration` field (any value)

Use the emoji label's block length when present. When only the native
`duration` field is present, use that value directly as the block length.

| Label | Block length |
|---|---|
| 🚀10min | 10 min |
| 🍅30min | 30 min |
| 🏃‍♂️60min | 60 min |
| 🐢90min | 90 min |
| 🪨120min | 120 min |

Tasks with **neither** an emoji duration label **nor** a native `duration`
field are **all-day**. Do not assign a start time. Surface them in the
"Also Today" section of the plan.

## Priority

Use Todoist's native priority (P1 highest → P4 lowest, per convention
where P4 is the "assigned today" working tier). Rank within each tier by:

1. Overdue > due today
2. Has 🟢 In progress / started-today signals (if present)
3. Tiebreak: alphabetical by task name (deterministic; no silent randomization)

---

## Routing

This skill exposes three distinct commands. When invoked, route based on the
user's trigger phrase:

| Trigger contains | Run command |
|---|---|
| `kickoff` / `morning` / `start` / `week brief` | `kickoff TDTB cowork` |
| `prep` / `plan` / `sequence` / `TDTB run` / `daily planning` | `prep my TDTB cowork` |
| `orphan` | `orphan check cowork` |

**Plain invocation (`/tdtb-bridger` with no trigger phrase):** ask the user
which command they want before proceeding. Do NOT default to a command —
the three serve different points in the pipeline and running the wrong one
wastes time. Format: present the three options with one-line descriptions
and wait for the user's choice.

---

## Commands

### `kickoff TDTB cowork` (aliases: `start my TDTB cowork`, `morning TDTB cowork`, `TDTB week brief`)

Start-of-day orientation. **Read-only — no writes, no confirm.** Produces a
compact inline markdown brief for the next 7 days plus today's shape.
Runs as **step 0** of the TDTB pipeline, before Coda staging.

#### Phase 0 — Tool check

- Google Calendar: `list_calendars`, `list_events`
- Todoist: `find-tasks-by-date` (or equivalent date-range filter)

`tool_search` for any missing. Halt if Calendar MCP unavailable.

#### Phase 1 — Gather

1. **Pin current time.** Same anchor-time discipline as `prep` — user-stated
   time first, live clock second, halt and ask if neither.
2. **Pull GCal events for today + next 6 days** across ALL calendar IDs
   (`list_calendars` → per-ID `list_events`). Capture `title`, `start`,
   `end`, `all_day`, `calendar` source. Same per-ID-query rule as `prep`.
3. **Pull Todoist tasks** where due is in `[today, today+6 days]` OR
   overdue. Capture `name`, `task_id`, `priority`, `due`, `duration`,
   `project`.
4. **Identify not-yet-staged tasks**: due is all-day (no time component)
   OR due is in future without a time. These go in the "due-soon-not-staged"
   carryover bucket.

#### Phase 2 — Synthesize

5. **Per-day summary** for each of the 7 days:
   - Event count, total booked minutes
   - Headline event(s) — the most distinctive 1–2 commitments
   - Heavy/light flag: heavy if booked > 3h, light if booked < 1h
6. **Week themes**:
   - Cluster patterns ("heavy Mon–Tue, light Wed–Fri")
   - Identify the cleanest deep-work day (longest uninterrupted block in
     the week)
   - Note all-day-locked days (travel, offsite, etc.)
7. **Today's day shape**: identify free windows between fixed events.
   Flag short fragments (<60 min) as Mixed-only. Flag the longest window
   as Deep-eligible.
8. **Carryover**:
   - Overdue Todoist tasks (any priority)
   - Tasks due today not yet pushed/staged
   - Tasks due in next 7 days that need staging attention this week
9. **Suggested today** (descriptive, not prescriptive):
   - "Long midday window = ideal for one Deep block"
   - "Short pre-meeting fragment — don't squeeze work in"
   - Reference the cap state if known (skip if not).

#### Phase 3 — Emit

10. Emit a compact inline markdown summary — **no HTML artifact, no file write**:
    - **Week-at-a-glance table**: 7 rows. Columns: Day | Load (🔴/🟡/🟢) | Headline
    - **Today's shape**: 1–2 sentence narrative (free windows, deep-work
      eligibility, any hard constraints)
    - **Carryover**: overdue tasks + due-today-not-staged, compact bullet list
    - **Suggested focus**: 1–2 bullets
11. Return immediately after emitting — no further phases.

#### Behavioral rules

- Read-only. Never write to Todoist, Calendar, or Coda.
- No inline confirm — emit and return.
- Don't classify carryover items by Type (Deep/Mixed) — that's a staging
  concern. Just surface what's overdue and what's coming.
- Halt rather than ship a partial brief if any calendar's `list_events`
  errors. Calendar gaps are misleading.

---

### `prep my TDTB cowork` (aliases: `cowork TDTB run`, `plan my day cowork`, `sequence my blocks cowork`)

#### Phase 0 — Tool check

Verify these tools are loaded. `tool_search` for any missing:

- Todoist: `find-tasks-by-date`, `update-tasks`
- Google Calendar: `list_events`
- Tana (only if orphan scan is invoked): `tana:read_node`

`list_events` is NOT in the default loaded schema. Always front-load it.
If any required tool is unavailable after `tool_search`: halt, dump tool
availability to the run log, do not proceed.

#### Phase 1 — Gather

1. **Pin current time.** Every "available window" calculation anchors
   here. Record in run log under `anchor_time`.
   **CRITICAL:** `anchor_time` must be the actual clock time at the moment
   of invocation — NOT inferred from session metadata, context window
   headers, or the system date string. The correct source priority is:
   (a) user-stated time in the invoking message (e.g. "current time is 1:39"),
   (b) a live clock tool if available. If neither is present, **halt and ask**
   before proceeding. Scheduling tasks in the past is a hard failure mode.
2. **Pull Todoist tasks due today + overdue** across all projects. Capture
   per task: `name`, `task_id`, `priority`, `labels` (especially duration
   emoji labels), `duration` (native field), `due_string`, `due` (full
   object including `datetime`), `project`, `checked` state.
3. **Alignment check.** Immediately after pulling tasks, classify each by
   PUSH state:
   - **Pushed** — `due.datetime` is set to any time today (PUSH has run)
   - **Default-timed** — `due.datetime` is today at 2:00 PM UTC / 10:00 AM ET
     (PUSH ran but no custom `start pull` time was set — will be re-timed
     by sequencer)
   - **Unpushed** — `due.datetime` is null / all-day (PUSH has not run for
     this task, or task is intentionally all-day)
   Emit one compact alignment line:
   `PUSH state: N pushed (N at default 2PM) · N unpushed`
   **Halt condition:** if N pushed = 0 AND today's task list is non-empty,
   warn: "No PUSH-timed tasks found — has the Shortcut run? Confirm to
   proceed or halt." Wait for user response before continuing.
   This check replaces the manual Coda alignment dashboard review.
4. **Pull today's Google Calendar events** across ALL connected calendars
   (default/primary, work/Trinoor import, personal import, shared). Query
   each calendar ID separately — do not rely on the default calendar alone.
   Capture: `title`, `start`, `end`, `all_day` flag, `calendar` source.
   Fixed commitments = anything with a real time block.
5. **(Conditional) Tana Attention scan** — only if the orphan scan is
   explicitly invoked. Read the Attention search node on today's daily
   page.

#### Phase 2 — Synthesize

6. **Partition Todoist tasks:**
   - **Locked** (has `🚙Humming` label) → fixed commitments. Add to
     `fixedEvents` using their existing due time + duration (emoji label
     or native field). Immovable.
   - **Timed candidates** (has emoji duration label OR native `duration`
     field, not locked) → `timedTasks`. Block length = emoji label value
     if present, else native `duration` value.
   - **All-day** (no emoji duration label AND no native `duration` field,
     not locked) → `allDayTasks`.
7. **Compute available windows:** (current time → end of work day) minus
   fixed calendar events and locked blocks. End-of-day default: 5:45 PM
   unless a later bound is explicit in today's calendar.
8. **Rank timed candidates** per Priority section above.
9. **Pre-fit sequence** deterministically:
   - High-priority / started items → earliest viable slot
   - Heavy blocks (🐢/🪨 or native duration ≥ 90 min) → largest uninterrupted windows
   - Back-to-back, no mandatory buffer
   - Task won't fit → bump. Never truncate, never split, never auto-reschedule
     to tomorrow.

#### Phase 3 — Visualize & Confirm

Apply `artifact-style-guide` for typography, color tokens, and spacing.

Build an HTML artifact — the plan visualization:

- **Header bar**: date, anchor time, window summary (`Nh available · N
  scheduled · N bumped`)
- **Day timeline**: horizontal lane spanning anchor_time → EOD
  - Fixed calendar events: colored blocks labeled with title + time range.
    Color per calendar source: 🟡 Mint/Trinoor `#fbd75b`, 🍯 Shared
    `#f9c83a`, 🙋‍♂️ Personal `#7986cb`, 🔒 Locked `#ef9a9a`, fallback `#a4bdfc`
  - Scheduled tasks: solid blocks labeled with rank number, task name,
    duration, priority badge (P1–P4). Use a distinct task color (e.g.
    `#80cbc4`)
  - Available (unscheduled) windows: light-background zone with "free Nh
    Mm" label
  - Time axis: tick marks every 30 min, hour labels on the hour
- **Bumped section** (below timeline): bulleted list, task name + bump
  reason
- **Also Today section** (below bumped): all-day tasks, no action needed
- **Cap status line**: `Deep N/4 · Mixed N/3` (⚠ flag if over)

Write artifact to `[outputs]/tdtb-plan-YYYY-MM-DD.html`.

After emitting the artifact, present the inline confirm prompt:

> Commit [N] writes to Todoist? (yes / no / edit)
> - `yes` → proceed to Phase 4
> - `no` → halt, log `decision: declined`, no writes
> - `edit` → halt, plan artifact preserved for manual adjustment before re-invoke

Do NOT proceed to Phase 4 without an explicit `yes`.

#### Phase 4 — Write

For each scheduled task in the approved plan, update via Todoist MCP:

```
update-tasks(
  task_id: "...",
  due_string: "today at H:MMam/pm"
)
```

**CRITICAL:** Use `due_string`, NOT `due_datetime`. `due_datetime` silently
no-ops via the current MCP wrapper — responses return success but the time
does not change.

Write scope:
- All-day tasks: no writes.
- Calendar events: no writes (this skill never creates calendar events;
  BusyCal renders the Todoist times).
- Bumped tasks: no writes.
- Locked blocks: no writes (they already have the correct time).

**Per-write verification.** After each `update-tasks` call, check the
response's `due` value against the intended time. If mismatch, log under
`errors.write_verification_failed` and continue the batch. Do not silently
retry.

**Completion summary.** After the batch:

```
✓ [N] scheduled · [N] skipped · [N] bumped · [N] verification failures
Run log: [artifact path]
```

---

### `orphan check cowork`

Read-only, no confirm required.

1. Read the Tana Attention search node via `tana:read_node`.
2. Pull today's Todoist set (same as Phase 1 step 2).
3. Emit a markdown list of items flagged by Tana that are not represented
   in Todoist. Write to run artifact. No scheduling. No writes.

---

## Autonomy Boundaries

**Inline confirm** (pause, prompt user at desk, resume on response):

- Alignment check: N pushed = 0 and task list is non-empty (possible PUSH
  failure — confirm intent before proceeding).
- Committing the batch of Todoist `due_string` writes (Phase 3 → Phase 4 gate).

**Halt** (unresumable; user re-invokes after fixing the condition):

- Todoist MCP unavailable or auth failure
- Google Calendar MCP unavailable (breaks window computation)
- `> 15` timed candidates surface — unusual input signal; dump plan to
  artifact and halt rather than commit an overloaded day
- Unrecognized or malformed duration label on a candidate
- Zero available windows (every timed candidate would bump)
- Schema mismatch on a Todoist response (unexpected shape, missing fields)
- Tana Attention node unreachable during orphan scan

## Decision Defaults

When multiple valid interpretations exist, apply in this order:

1. **Ordering within a priority tier**: Priority tiebreak chain (overdue →
   started → alphabetical). Deterministic.
2. **Task won't fit today's windows**: bump. Never truncate, split, or
   auto-reschedule.
3. **End-of-day ambiguity**: 5:45 PM. Extend only if a calendar commitment
   after 5:45 PM is explicit in today's events.
4. **Exact window-fit**: fill it. No buffer default.
5. **Task has native `duration` but no emoji label**: use native duration
   as block length. Treat as a timed candidate, not all-day.
6. **Locked block overlaps a calendar event**: both render as fixed. Trust
   the user's prior configuration. Do not reconcile.
7. **Heavy-block contention for a large window**: higher priority wins →
   overdue → alphabetical.

Record every non-trivial default applied under `decisions` in the run log.

## Completion Criteria

A run is complete when ALL hold:

- Every schedulable timed candidate is either (a) written with a new
  `due_string` and verified, (b) bumped with a logged reason, or (c)
  skipped with a logged reason.
- Run log artifact is written to `[outputs]/tdtb-YYYY-MM-DD.md`.
- The Phase 4 completion summary is emitted.
- Zero silent failures — every `update-tasks` call has a logged
  verification entry.

If the user responds `no` or `edit` to the inline confirm, the run is also
complete — with `decision: declined` or `decision: edit_requested` logged
and no writes attempted.

## Run Log Schema

Write a markdown artifact to `[outputs]/tdtb-YYYY-MM-DD.md`:

```
# TDTB Run — [date] (anchor: [HH:MM AM/PM])

## Inputs
- todoist_candidates_total: [N]
- locked_count: [N]
- timed_count: [N]
- all_day_count: [N]
- calendar_events: [N]
- available_minutes: [N]

## Alignment
- pushed: [N] (default_2pm: [N])
- unpushed: [N]

## Plan Artifact
[link or path to tdtb-plan-YYYY-MM-DD.html]

## Decision
- user_confirm: [yes | no | edit_requested]

## Writes Executed
| task_id | name | old_due | new_due | verification |
|---|---|---|---|---|
| ... | ... | ... | ... | ok | mismatch | skipped |

## Decisions Applied (non-trivial defaults)
- [decision_key]: [chosen value] — [rule applied]

## Items Skipped / Bumped
| task_id | name | reason |
|---|---|---|
| ... | ... | ... |

## Errors
| category | message | resolution |
|---|---|---|
| ... | ... | ... |

## Risks Surfaced for Review
- [note]
```

## Error Handling Policy

| Category | Example | Policy |
|---|---|---|
| Transient | Todoist rate limit, network timeout | Retry 1x after 1s backoff. If second attempt fails, log the task under `errors.transient_after_retry` and continue the batch. |
| Data | Malformed task shape, missing project | Skip task, log under `items_skipped`, continue. |
| Access | Todoist MCP unavailable, auth invalid, Calendar MCP unavailable | Halt. Dump MCP state to run log. |
| Structural | Unexpected response shape, schema mismatch on labels, unknown duration label | Halt. Dump full context (request + response) to run log. |
| Write verification | Response `due` doesn't match intended time | Log under `errors.write_verification_failed`. Do not silently retry. Continue batch. |

---

## Schema Reference

### Todoist

| Entity | ID |
|---|---|
| Inbox project | `6M92PWG3HHJgQvfp` |

Priority tiers, duration labels, and priority projects pulled dynamically
from Todoist — don't hardcode.

### Tana (context only)

| Entity | ID |
|---|---|
| Workspace | `pr3I62E4Gd1w` |
| `#efforts` tag | `lS0GKXZ7JxTa` |
| `#todo` tag | `g8XxDrKf-ERe` |

Attention node ID varies per day — read from today's daily page.

### Google Calendar

| Calendar | Purpose |
|---|---|
| 🟡 Mint / Trinoor | Work meetings (imported) |
| 🍯 A + M Busy Bees! | Shared with Megan |
| 🙋‍♂️ Personal | Personal events |

Query all calendars via `list_calendars` then `list_events` per calendar ID.
Do not rely on the default calendar alone — work meetings live on an import
calendar that is missed when only the primary is queried.

No `⬜ Blocks` writes. That pipeline is retired — Todoist times render
directly in BusyCal.

### Time conversion reference

| Time | Minutes from midnight |
|---|---|
| 6:00 AM | 360 |
| 7:00 AM | 420 |
| 8:00 AM | 480 |
| 8:30 AM | 510 |
| 9:00 AM | 540 |
| 10:00 AM | 600 |
| 12:00 PM | 720 |
| 1:00 PM | 780 |
| 3:00 PM | 900 |
| 5:00 PM | 1020 |
| 5:45 PM | 1065 |
| 6:00 PM | 1080 |

Formula: `hours * 60 + minutes` (24hr clock).

---

## Behavioral Rules

- Todoist is the source of truth for schedulable blocks.
- **Scope of writes: `due_string` only.** This skill never modifies task
  names, descriptions, priorities, labels, projects, or any other field.
- Never complete, delete, or reprioritize Todoist tasks.
- Never create or modify calendar events.
- **Locked blocks** (`🚙Humming` label) are fixed commitments. NOT in
  `timedTasks`, NOT rearranged, NOT written. Partition into `fixedEvents`
  during Phase 2 using existing due time + duration (emoji label or native
  field).
- Never filter a surfaced task based on pattern recognition ("this is an
  interval, skip it"). All P1–P4 tasks due today are candidates unless
  locked. The deterministic ranking handles sequencing.
- A task is all-day only if it has **neither** an emoji duration label
  **nor** a native `duration` field. A native `duration` field alone is
  sufficient to make a task a timed candidate.
- **The Phase 3 HTML artifact IS the plan.** Present the artifact first,
  then the inline confirm. Never skip the artifact and go straight to
  confirm.
- The single inline-confirm gate is the approval model. No per-task
  confirms. No silent commits.
- **Kickoff is markdown-only.** The kickoff command never writes files or
  produces HTML. Inline markdown, then return.

---

## Glossary

Interpret these when displaying or reasoning about tasks — do not rename
the tasks themselves.

| Term | Meaning |
|---|---|
| Sudsing | Showering |
| Putz | Auto-imported Pomodoro time block from the "Session" app on the Putz schedule. Represents unfocused / puttering time — not a targeted work block. Treat as low-priority filler. |

**Adding entries (requires-preconfiguration):** glossary updates are made
by editing this file directly. The chat variant's mid-run
"glossary: term means X" capture pattern does not apply in cowork —
there's no live dialog channel to accept the directive.

---

## Corrections

| Date | Correction | Source |
|---|---|---|
| 2026-05-02 | v5.1 — Kickoff Phase 3 demoted from heavy HTML artifact (7-day week grid, colored calendar blocks, file write) to compact inline markdown only (week table + today shape + carryover + suggested focus). No file write. Kickoff was producing a heavy output that belonged in the prep stage. | Skill redesign |
| 2026-05-02 | v5.1 — Prep Phase 3 upgraded from markdown plan summary to HTML day-timeline visualization artifact. Shows fixed events, scheduled blocks (with rank + priority badge), available windows, bumped tasks, and all-day items as a rendered day view. Replaces the missing visualization that was dropped during cowork conversion. Inline confirm follows the artifact. | Skill redesign |
| 2026-05-02 | v5.1 — Alignment check added to prep Phase 1 (step 3). After pulling Todoist tasks, classify each by PUSH state (pushed / default-timed / unpushed) and emit a compact alignment line. If N pushed = 0 and task list is non-empty, inline-confirm before proceeding. Replaces the manual Coda alignment dashboard review. | Skill redesign |
| 2026-04-29 | Added `kickoff TDTB cowork` — start-of-day 7-day week brief. Replaces the Apple Shortcut "check your calendar" popup with an interpreted week view + today's day shape + carryover. Read-only, no confirm. | Phase 4 redesign |
| 2026-04-29 | TDTB v5.0 redesign — bridger repositioned as post-PUSH sequencer (not scheduler). Coda enforces block budget caps upstream (`deepCap=4`, `mixedCap=3`); bridger surfaces over-cap state and bumps surplus by ascending priority. Replaces manual BusyCal rearrangement step. | Phase 3 redesign |
| 2026-04-28 | anchor_time must come from (a) user-stated time in the invoking message or (b) a live clock tool — never inferred from session metadata or context headers. Using a stale session-start time caused all 9 tasks to be scheduled in the past on the first pass of the run. If neither source is available, halt and ask before Phase 1. | Live run |
| 2026-04-27 | Native Todoist `duration` field qualifies a task as a timed candidate — equivalent to an emoji duration label. Tasks with a native `duration` but no emoji label were incorrectly classified as all-day and dumped to the "Also Today" section instead of being scheduled. | Live run |
| 2026-04-27 | Phase 1 must query ALL calendar IDs (via `list_calendars` then per-ID `list_events`), not just the default/primary calendar. Work meetings on the Trinoor import calendar were missed when only the primary calendar was queried. | Live run |
| 2026-04-23 | Cowork variant: Phase 3 emits a markdown plan summary + single inline-confirm gate. Widget + sendPrompt flow dropped. `> 15` candidates is a halt, not a confirm. | Cowork conversion |
| 2026-04-21 | Phase 3 outputs a visual planner widget (.jsx artifact), not a text plan. User approves via the widget's Approve button, which fires sendPrompt(). | Skill redesign (chat variant only) |
| 2026-04-17 | Todoist tasks are the primary source for TDTB blocks, not Tana. BusyCal renders timed Todoist tasks as calendar events. No ⬜ Blocks calendar writes needed. | Live run |
| 2026-04-17 | Tasks without a duration label (🚀/🍅/🏃‍♂️/🐢/🪨) stay all-day. Do not assign times. | Live run |
| 2026-04-17 | Use `due_string: "today at H:MMam/pm"` for Todoist scheduling. `due_datetime` silently no-ops via the MCP wrapper — API returns success but the time doesn't change. | Live run — 7 tasks failed silently before switching to due_string |
| 2026-04-17 | Front-load `tool_search` for Google Calendar `list_events` before Phase 1. Not in the default loaded schema. | Live run |
| 2026-04-17 | Do not filter candidates by type pattern (e.g. "Summits is an interval, skip"). Surface all P1–P4 due-today Todoist tasks; let the ranking sequence. | Live run |
| 2026-04-15 | `supertag:tana_query` returns node metadata but field values come back empty. All field reads require individual `tana:read_node` calls. | Prior dry run |
| 2026-04-15 | Orphan scan via structured MCP queries is not viable. Use the Attention search node read — Tana computes date logic natively. | Prior dry run |
