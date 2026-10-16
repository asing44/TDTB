# 03 — Overflow parking must skip anchored spans

**DRAFT, not published.** No GitHub issue exists for this draft.

## Title

Overflow parking must respect anchored spans and never stack rows on anchors

## Suggested label

`needs-triage`

## Why (observed evidence)

- Overflow parking (`app/sequence.py`, `OVERFLOW_PARK_TIME` `17:00`) packs parked rows consecutively, is exempt from hard overlap with anchored blocks, Mint walls, and other re-seated rows, and emits `overflow_reseated`/`overflow_overlap` warnings.
- 2026-10-16 commit record showed stacked rows: System Improvements 17:30 with Sudsing 17:30; WALL-E-OS Capacities Buildout 18:00 with Foods Dinner 18:00-19:00; Backpack 17:00 with Quick Tasks 17:00.
- No warning was recorded in the state dir that day (warnings may not persist).
- This changes previously approved behavior: overflow exempt from anchor overlap (commit `4add629` era). This drafting reverses that earlier decision on the operator's current word.

## Decision

Operator decision (settled this session): anchors are NOT shareable. When the day is overcommitted, overflow rows fill free gaps and skip anchor spans; whatever still does not fit goes past end of day with a visible warning.

## Scope

- Make overflow parking skip anchor spans and pack only free gaps.
- Place non-fitting rows past end of day and emit a visible warning.
- Persist or surface the warning so a stacked day cannot pass silently.

## Out of scope

- Changing which rows are anchors.
- Changing Mint wall handling for non-overflow rows (see 06).
- Changing model-side placement of non-overflow rows.

## Acceptance (testable)

- First acceptance item: a failing test reproducing the 2026-10-16 stacking from that day's shape — anchors Sudsing, Foods Dinner, Night Routine; Mint Afternoon 15:00-17:00 walls; Trinoor Afternoon 13:30-17:00.
- No overflow row shares a start time with an anchor row in the reproduced shape.
- Every item is still placed after the change (never-bump).
- Non-fitting rows land past end of day with a visible warning present.
- Diagnosis is recorded first: whether the 2026-10-16 stacking was overflow re-seating or model placement.
- Fixture-only; zero billed calls and zero real source writes.

## Open questions

- None beyond the diagnosis above.

## Safety notes

- Never-bump: every item still placed.
- No `POST /commit`.
- No billed calls; fixtures only.
