# 04 — Show a note on rows that share time

**DRAFT, not published.** No GitHub issue exists for this draft.

## Title

Show a visible overlap note on rows that share time with another row

## Suggested label

`needs-triage`

## Why (observed evidence)

- On 2026-10-16 rows shared time with other rows and nothing on the row said so; the stacking was only visible in the commit record.
- Overlap can arise from explicit grants: `overlap_grants` in `app/placement_rules.py`, including model-emitted `reason` text and derived constraints.
- Anchors are never shareable (see 03), so a note must reflect explicit grants only.

## Decision

Operator decision (settled this session): show the note. It applies to explicit grants only, plus Mint per-row grants from 06. It does not apply to anchors, which never share.

## Scope

- Add a visible per-row note naming the other row and why the overlap was granted.
- Source it from the explicit grant, not from inferred time collision alone.
- Cover both server-side grants and the Mint per-row grant from 06.

## Out of scope

- Granting or denying overlaps (grants are set elsewhere).
- Changing anchor overlaps (03 handles that by removing them).
- Any change to what the commit gate accepts.

## Acceptance (testable)

- A row with an explicit overlap grant renders a note naming the other row and the grant reason.
- A row that merely abuts another row renders no note.
- Fixture-only; zero billed calls and zero real source writes.

## Open questions

- Investigation step: whether `overlap_grants` reasons (model-emitted `reason`, `placement_rules.py` derived constraints) already reach the UI. Currently unknown.

## Safety notes

- Never-bump: every item stays placed.
- No `POST /commit`.
- No billed calls; fixtures only.
