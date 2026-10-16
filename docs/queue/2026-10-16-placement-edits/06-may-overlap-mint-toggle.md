# 06 — Per-row "May overlap Mint" toggle

**DRAFT, not published.** No GitHub issue exists for this draft.

## Title

Plan tasks: add a per-row "May overlap Mint" toggle with server-side validation

## Suggested label

`needs-triage`

## Why (observed evidence)

- Mint Afternoon sessions (15:00-17:00 on 2026-10-16) are hard walls committed as TDTB calendar events, so assigned work landed at 17:00 or later.
- The operator hand-placed tasks in or against the Mint hours to work around that.

## Decision

Operator decision (settled this session): only certain tasks may overlap Mint; chosen by a per-row toggle in Plan tasks (`May overlap Mint`), not by label, size, or rule; the overlap must be shown explicitly (see 04); Mint remains a hard wall for every row without the toggle.

## Scope

- Add a per-row `May overlap Mint` toggle in Plan tasks.
- Validate the grant server-side so it is an explicit grant, not a client-only bypass.
- Surface the granted overlap as the visible note from 04.

## Out of scope

- Weakening the commit gate's never-forge checks. (inferred as the constraint)
- Label-, size-, or rule-based Mint overlap policies.
- Changing how Mint sessions themselves are created or committed.

## Acceptance (testable)

- A row with the toggle may be placed inside Mint hours and renders the overlap note from 04.
- A row without the toggle is never placed inside Mint hours; Mint stays a hard wall.
- The grant is validated server-side and does not bypass never-forge checks.
- Dependency: 04 (overlap note on rows) lands first.
- Fixture-only; zero billed calls and zero real source writes.

## Open questions

- Server-side representation of the per-row grant is not settled in this session (grant schema and where it is persisted). (inferred)

## Safety notes

- Never-bump: every item stays placed.
- No `POST /commit`.
- No billed calls; fixtures only.
- Never-forge checks stay intact.
