# 05 — Per-row "Keep all-day" action in Plan tasks

**DRAFT, not published.** No GitHub issue exists for this draft.

## Title

Plan tasks: add a per-row "Keep all-day" action for date-only tasks

## Suggested label

`ready-for-agent`

## Why (observed evidence)

- On 2026-10-16 `call Mom on birthday` (Todoist date-only due, priority 1) was sequenced as a 10-minute block 14:45-14:55, and the operator manually made it all-day.
- Today no rule keeps date-only tasks all-day: `app/external_sources.py` `_due_time` returns None for date-only, and they become movable 30-minute-default blocks.
- All-day currently happens only when `blocks == 0`, which commits a date-only `schedule-all-day` Todoist due (`app/shadow.py` ~727-737).

## Decision

Operator decision (settled this session): a per-row `Keep all-day` action in Plan tasks. Not a rule, not a label.

## Scope

- Add a `Keep all-day` action on a row in Plan tasks.
- Route the row through the existing `blocks == 0` all-day path.

## Out of scope

- Any automatic date-only rule or label-based behavior.
- Changing default duration for date-only tasks generally.
- Changing Todoist due semantics beyond the existing date-only write path.

## Acceptance (testable)

- Invoking the action sets that row to the existing `blocks == 0` all-day path.
- Commit writes a date-only due and creates no new timed block.
- The row is visible as all-day on the plan screen after the action.
- Fixture-only; zero billed calls and zero real source writes (no real Todoist write).

## Open questions

- None.

## Safety notes

- Never-bump: every item stays placed.
- No `POST /commit`.
- No billed calls; fixtures only.
