# 08 — Update capacities-assigned rule to assignedOn

**DRAFT, not published.** No GitHub issue exists for this draft.

## Title

Fix capacities-assigned rule: reference assignedOn instead of the non-existent assigned property

## Suggested label

`needs-triage`

## Why (observed evidence)

- The machine-local `~/.config/tdtb/producer-rules.json` rule `capacities-assigned` references property `assigned`, which does not exist on current Project objects (live field `assignedOn`), so it matched zero objects in the 2026-10-16 refresh.
- `skills/tdtb-refresh/producer-rules.json` is the canonical starter template; the machine-local file is operator state.
- Record-level `assigned_on_property` semantics landed at `c31b775`: date on or before today means assigned.

## Decision

Operator decision (settled this session): update the rule to `assignedOn`. The op and values are still to be decided (truthy vs date on or before `$today`, matching the record-level `assigned_on_property` semantics). The starter-file change is the repo part; the machine-local edit is a separate operator step, not a repo edit.

## Scope (repo part)

- Update the `capacities-assigned` rule in `skills/tdtb-refresh/producer-rules.json` to reference `assignedOn` with the op/values agreed when decided.

## Operator step (not a repo edit)

- Apply the matching change to `~/.config/tdtb/producer-rules.json` as an operator-approved change.

## Out of scope

- Editing the machine-local file from this repo task.
- Any other producer rule.
- Changing `assigned_on_property` record-level semantics.

## Acceptance (testable)

- Starter rule references `assignedOn`; no rule references the non-existent `assigned` property.
- Rule validation or a fixture load confirms the rule parses and matches as intended under the agreed op/values.
- Fixture-only; zero billed calls and zero real source writes.

## Open questions

- Op and values: truthy vs date on or before `$today`. (open, operator to decide)

## Safety notes

- Never-bump: every item stays placed.
- No `POST /commit`.
- No billed calls; fixtures only.
