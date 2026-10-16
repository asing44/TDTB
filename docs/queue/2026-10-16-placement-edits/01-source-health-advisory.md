# 01 — Generation-freshness warnings should be advisory, not global health

**DRAFT, not published.** No GitHub issue exists for this draft.

## Title

Source health: treat generation-freshness warnings as advisory at the served row level

## Suggested label

`needs-triage`

## Why (observed evidence)

- `frontend/src/adapters/wire.ts` (~1987) `sourceHealthOf` re-derives health from warning text and whitelists only calendar-reminder omissions and `skipped Capacities object ... not plannable`.
- The Capacities warning `Capacities structure <id> is not in the published generation; needs Refresh.` (`app/capacities_intake.py` `_type_warnings` ~215) is not whitelisted, so it degrades ALL sources.
- Degradation sets `sourceHealthBlocked` (`frontend/src/store/store.ts` ~1335), which locks sequence/commit (decision 17); the pill reads `Sources degraded · commit blocked` (`frontend/src/ui/Header.tsx` ~120).
- Todoist and Calendar dots turn amber only because `sourceDot` derives from that one global health (`frontend/src/ui/connections/ConnectionsScreen.tsx` ~498-510).
- Evidence 2026-10-16: refresh outcome `staleConfiguration` (config changed mid-run) discarded a complete 236-read result, so Purchase/Print stayed out of generation 22.

## Decision

Operator decision (settled this session): generation-freshness warnings are treated as advisory where the rows are served; per-source dots are scoped to their own source; the commit gate stays fail-closed for real read failures.

## Scope

- Classify generation-freshness warnings as advisory at the point where served rows are evaluated.
- Scope each source dot to its own source's health rather than the one global health value.
- Keep `sourceHealthBlocked` fail-closed for real read failures.

## Out of scope

- Any change to the commit gate's handling of genuine read failures.
- Re-running or repairing the 2026-10-16 refresh.
- The `staleConfiguration` discard behavior itself (cited here as evidence only).

## Acceptance (testable)

- A fixture with only the generation-freshness warning does not set `sourceHealthBlocked` and does not lock sequence/commit.
- Todoist and Calendar dots stay green while only the Capacities source carries a generation-freshness warning.
- A fixture with a real read failure still sets `sourceHealthBlocked` and still locks commit.
- Fixture-only; zero billed calls and zero real source writes.

## Open questions

- The missing-type warning means newly mapped types are absent from served rows, so advisory classification must still surface a visible per-source note. Confirm that the note is per-source and where it appears. (inferred)

## Safety notes

- Never-bump: every item stays placed.
- No `POST /commit`.
- No billed calls; fixtures only.
