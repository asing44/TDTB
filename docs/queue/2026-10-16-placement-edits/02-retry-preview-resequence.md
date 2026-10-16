# 02 — Deterministic refusals need resequence, not an inert Retry

**DRAFT, not published.** No GitHub issue exists for this draft.

## Title

Approval drawer: offer resequence/regenerate instead of an inert Retry on deterministic commit refusals

## Suggested label

`needs-triage`

## Why (observed evidence)

- The Preview writes drawer shows a red panel with `Retry preview` (`frontend/src/ui/ApprovalDrawer.tsx` ~196-207; store `SHADOW_FAIL` in `frontend/src/store/store.ts` ~754) for deterministic 422 commit refusals.
- Retrying resends the same sequence, so it can never succeed.
- Example 2026-10-16: `commit refused: forged fixed-lane timing/backdrop: Live: client backdrop on non-zone row — regenerate before committing`.
- That specific refusal was fixed on main at `77b2c39` by widening the backdrop allow-set to `overlap_allowed` anchored rows; the class of deterministic refusals remains.

## Decision

Operator decision (settled this session): on deterministic refusals, offer a resequence/regenerate affordance instead of the inert retry; transient transport failures keep Retry.

## Scope

- Distinguish deterministic 422 commit refusals from transient transport failures in the drawer.
- On a deterministic refusal, show a regenerate/resequence affordance replacing Retry.
- Keep Retry for transient transport failures.

## Out of scope

- Changing commit-gate validation or the refusal messages themselves.
- Auto-repairing or auto-resequencing without an operator action.

## Acceptance (testable)

- A deterministic 422 refusal renders the regenerate/resequence affordance, not Retry.
- A transport failure renders Retry and no regenerate affordance.
- The affordance fires nothing until the operator explicitly invokes it.
- Fixture-only; zero billed calls and zero real source writes.

## Open questions

- `/sequence` is billed against the daily ledger, so the affordance must not auto-fire a billed call; it must be an explicit operator action that shows it is billed. Confirm the billed label and any confirmation step. (inferred)

## Safety notes

- Never-bump: every item stays placed.
- No `POST /commit`.
- No billed calls; never auto-fire `/sequence`; fixtures only.
