# 07 — Drift detection between committed blocks and Calendar

**DRAFT, not published.** No GitHub issue exists for this draft.

## Title

Detect and display drift between committed blocks and current Calendar state

## Suggested label

`needs-triage`

## Why (observed evidence)

- After commit, TDTB cannot see operator edits in Calendar: calendar rows diff as CREATE or NOOP only.
- `app/shadow.py` ~962-975 `_match_calendar_event` matches a same-title event at any time as NOOP, so a re-commit treats moved owned blocks as unchanged.
- `/adjust` cannot change a block's time.

## Decision

Operator decision (settled this session): detect and show drift only.

## Scope

- Read-only comparison of committed blocks (runstate commit ledger) against current Calendar.
- Flag moved, missing, or added blocks on the plan screen.
- Change nothing and adopt nothing.

## Out of scope

- Any Todoist or Calendar write.
- Re-committing, repairing, or adopting drifted blocks.
- Changing `_match_calendar_event` NOOP matching itself.

## Acceptance (testable)

- A fixture where a committed owned block moved in Calendar is flagged as moved.
- A fixture where a committed block is missing from Calendar is flagged as missing.
- A fixture where an unowned Calendar block appeared is flagged as added.
- Running the comparison performs no Todoist or Calendar write.
- Fixture-only; zero billed calls and zero real source writes.

## Open questions

- Where the drift view lives (existing Plan screen section vs new surface). (inferred)
- Poll cadence for the comparison. (inferred)

## Safety notes

- Never-bump: read-only, so placement is untouched.
- No `POST /commit`.
- No billed calls; fixtures only; no live Calendar read in tests.
