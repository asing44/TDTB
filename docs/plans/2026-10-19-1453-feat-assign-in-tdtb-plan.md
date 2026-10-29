---
title: Assign and Unassign in TDTB - Plan
type: feat
date: 2026-10-19
topic: assign-in-tdtb
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-brainstorm
execution: code
---

# Assign and Unassign in TDTB - Plan

## Goal Capsule

- **Objective:** The operator reviews the items already sourced for today in one place and decides in TDTB which are assigned to the plan, without writing anything to Capacities.
- **Product authority:** The operator's answers in the 2026-10-19 conversation, marked `session-settled` below.
- **Open blockers:** None blocking planning; see Outstanding Questions.
- **Not active scope:** sourcing new tasks from Todoist or Capacities, and any Capacities write-back.

## Product Contract

### Summary

The Assigned and Unassigned toggle becomes a second review step over the items already sourced for today. Assigned items are placed on the calendar at commit. Unassigned items are held back. Todoist unassign also advances the task's due date, as the Source menu does today. Capacities items are only held back, never written.

### Problem Frame

Today the toggle separates assigned rows from the quick pool (`frontend/src/ui/Queue.tsx`), and the Source menu carries a separate "Unassign in source" action (`frontend/src/ui/SourceMenu.tsx`). Capacities rows cannot be written back and are materialized as Todoist tasks in the PHEP project at commit. The operator wants one place to decide what is in the plan, with the Capacities write-back left out of it.

### Key Decisions

- **The list is only items already sourced for today; the toggle does not pull tasks in from Todoist or Capacities.** (session-settled: user-directed — chosen over an all-open-tasks pool or filtered pools.) Governs R1.
- **Unassigning a Todoist item advances its due date; unassigning a Capacities item writes nothing.** (session-settled: user-directed — chosen over TDTB-only hold-back for both.) Governs R3, R4.

### Requirements

**Assignment review**

- R1. The Plan tasks list contains exactly the items already sourced for today, each marked Assigned or Unassigned.
- R2. Toggling an item to Assigned includes it in today's calendar placement and commit; toggling to Unassigned excludes it.
- R3. Unassigning a Todoist item advances its due date through the existing journaled path and is undoable.
- R4. Unassigning a Capacities item only holds it back from this commit: it is not placed on the calendar, not copied into the PHEP Todoist project, and nothing is written to Capacities.

**Controls**

- R5. The toggle becomes the single control for assignment, and the Source menu's "Unassign in source" is removed or reduced to avoid two controls with the same effect.

### Acceptance Examples

- AE1. **Covers R3.** Given a Todoist item due today, when the operator sets it Unassigned, then its due date advances, it leaves the commit, and Undo restores it.
- AE2. **Covers R4.** Given a Capacities item, when the operator sets it Unassigned, then no Capacities write occurs and the item is not placed on the calendar or copied to PHEP.

### Scope Boundaries

- Not adding Capacities write-back of any kind.
- Not changing how items are sourced into today's list.

### Outstanding Questions

**Deferred to Planning**

- What Assigned means for a Todoist item that was previously unassigned: does re-assigning set its due date to today, or only include it in the commit?
- How R5 resolves the overlap with the existing "Skip today" and the Source menu's actions.
- Whether an Unassigned Capacities item survives a reload and a day rollover, and where it is stored.

### Sources / Research

- `frontend/src/ui/Queue.tsx`, `frontend/src/ui/SourceMenu.tsx`, and `app/runtime_actions.py` (journaled unassign).
- `docs/plans/2026-10-19-1432-feat-unified-sources-refresh-plan.md` for the adjacent refresh work.

## Planning Contract

### Code reality that shapes this plan

- **No toggle exists on main (63181d4).**
  - `Queue.tsx` renders urgency bands, a legacy "Excluded in this browser" lane and `SkippedToday`.
  - `TaskRow.tsx` has Complete and Skip today buttons. Only `docs/plan/2026-10-14-handoff.md:42` describes a toggle.
  - This plan builds the toggle; it does not extend one.
- **No pool reaches Plan tasks.** `wire.ts:5-12` drops `digest.suggested`. R1's "only items already sourced for today" is the current `inputs.assigned`.
- **Todoist unassign does not always advance the date.**
  - For a non-recurring task, `todoist.unassign` clears the date (`runtime_actions.py:907-908`).
  - Only a recurring task's due date is advanced, without completing it (`:900-906`).
  - Undo restores the exact before-image (`:1067-1078`).
  - Either way the task leaves the Todoist "today" read, so it drops out of `/plan-inputs`.
- **The `assign` verb writes to Todoist.** `todoist.assign_today` reschedules the task to `"today"` and skips recurring tasks (`:880-889`).
- **Unassign is not a cycle verb.**
  - `unassign` is not in `CYCLE_VERBS` (`:1535`).
  - While an earlier unassign stays `applied`, a second unassign of the same row returns a duplicate no-op (`:1588-1596`).
- **`runstate.dropped` already gives R4's exact semantics.**
  - It makes no source write and is date-scoped (`runstate.py:79-83`).
  - It is filtered from the digest and index (`main.py:4740-4750`), returned as `dropped_today` (`:4793`) and re-filtered at commit (`:5896-5910`).
  - So a dropped Capacities row never reaches `shadow.py` PHEP routing.
- **Single-action undo has no conflict check.** `undo_action` (`:1665`) has none, but batch undo uses `step_conflict` (`:1481`), which already handles `todoist.unassign`.
- **Local `included=false` overrides are a legacy path.**
  - They are client-only (`persist.ts:96-102`) and filtered only from the client submission (`store.ts:1296`).
  - `Queue.tsx` labels them legacy and says they convert to Skip today.

### Key Technical Decisions

- **KTD1. Re-assigning a Todoist item reverses its own journaled unassign.** Settles Q1. Governs R2 and R3.
  - Re-assign does not use the `assign` verb, and it does not set the due date to "today".
  - New `reassign(valid_date, identity)`:
    - Finds the newest `applied` unassign action for that identity in today's journal.
    - Runs `step_conflict` on its `todoist.unassign` step. If the due date was changed in Todoist since, it refuses with that reason.
    - Otherwise calls `undo_action`, which restores the prior due date exactly.
  - Why not `assign`:
    - It no-ops on recurring tasks, so a skipped occurrence could never come back.
    - It flattens a datetime due to "today".
    - It leaves the unassign `applied`, so the next unassign would be a silent duplicate.
  - After an undo the action is `undone`, so the next unassign cycle runs normally.
- **KTD2. TDTB keeps Unassigned Todoist rows listed from the journal.** Governs R1.
  - `/plan-inputs` gains `unassigned_today: [{identity, name, action_id, source:"todoist", recurring, minutes?}]`.
  - It is derived from today's `runtime-journal/<date>.json`: actions with `verb=="unassign"`, `status=="applied"` and an applied `todoist.unassign` step, newest per identity.
  - Rows from the Source menu, a batch-of-one or a multi-row batch all appear, because they share the verb.
  - No new state store is needed. On the next day the journal is new and the task is no longer sourced, which matches R1.
- **KTD3. Unassigned Capacities and calendar rows use the existing date-scoped drop.** Settles Q3. Governs R4.
  - Unassigned = `drop_from_plan` (a `runstate.dropped` entry). Assigned = `restore_today`.
  - The entry is stored in the dated runstate note. It survives reload because it is server-side and returned in `dropped_today`.
  - It expires at day rollover by construction.
  - Not `persist.ts`: that is client-only, not enforced at commit, and the legacy path.
  - Not `rowDraft`: drafts are unsaved shaping and never persisted.
  - No commit or PHEP change is needed. Tests prove R4.
- **KTD4. The toggle is per row and is the only assignment control.** Settles Q2. Governs R5.
  - Plan tasks lists Assigned rows in the bands. A new "Unassigned" section lists `unassigned_today` plus Capacities and calendar entries from `dropped_today`.
  - The Source menu loses "Unassign in source"/"Skip occurrence" and the Capacities note. It keeps Hide always and Delete.
  - Skip today stays for Todoist rows only, because its effect differs: no source write and the due date stays today (D2).
  - On Capacities and calendar rows, Skip today would duplicate the toggle, so it is hidden there. The C key on a Capacities row sets Unassigned instead.
  - "Skipped today" then lists Todoist drops only.
- **KTD5. Gating is unchanged.** The toggle uses `taskActionDisabledReason` and the server's `_require_day_setup`, so before Day Setup is confirmed it is disabled with the existing reason.

### Conflicts between requirements and code

- **C1.** The problem frame says a toggle exists in `Queue.tsx`. It does not on main. U4 builds it.
- **C2.** R3 and the user decision say Unassign "advances its due date". The existing path clears a non-recurring task's date (D1).
- **C3.** "TDTB-only assign" conflicts with the `assign` verb's Todoist write. The toggle never calls `assign`. The forgot-strip still does, which is out of scope.
- **C4.** For Capacities rows, Skip today and Unassigned are the same `dropped[]` state, so two controls would have the same effect. Resolved by KTD4.
- **C5.** Single-action undo skips the conflict check that batch undo applies. KTD1 adds the check only on the re-assign path; the footer Undo is unchanged.

### Decisions needed (user)

- **D1. (RESOLVED: A) What should Unassign do to a non-recurring Todoist task?**
  - (A, recommended) Keep the existing journaled path: clear the date. R3 then reads "clears the date, or advances a recurring occurrence". There is no new write path, and batch Unassign stays identical.
  - (B) Move the due date to tomorrow. This needs a new step behavior, its reversal, conflict checks and tests, and it also changes batch Unassign.
- **D2. Skip today for Todoist rows.**
  - (A, recommended) Keep it. It is the only "not today, Todoist untouched" action.
  - (B) Remove it everywhere. The toggle becomes the only hold-back control, and every Todoist hold-back changes Todoist.
- **D3. Batch "Unassign" in the batch bar.**
  - (A, recommended) Keep it unchanged. Its rows show as Unassigned through KTD2. Capacities rows stay excluded and the reason text points to the toggle.
  - (B) Remove it from `BATCH_ACTIONS`.

### Resolved Questions

- **OQ-1.** Re-assign undoes the item's own unassign, restoring its prior due date. It never writes "today" (KTD1).
- **OQ-2.** The toggle replaces "Unassign in source". Skip today stays for Todoist only. Hide always and Delete stay in the Source menu (KTD4, pending D2/D3).
- **OQ-3.** Unassigned Capacities items live in `runstate.dropped` in the dated note. They survive reload and expire at rollover (KTD3).

### Sequencing

- Backend: U1, then U2. They touch disjoint files, but U2 imports U1.
- Frontend: U3 runs alongside U1, against the KTD2 wire shape. U4 and U5 run in parallel after U3, on disjoint files.
- U4 is a visual change. Route it to `design` (unattended) with the path allowlist below.

## Implementation Units

- **U1. Journal-derived Unassigned list and reassign engine**
  - **Goal:** `unassigned_today(valid_date)` and `reassign(valid_date, identity, todoist=â¦)`, as in KTD1 and KTD2.
  - **Requirements:** R1, R2, R3.
  - **Files:** `app/runtime_actions.py`, `app/tests/test_runtime_actions.py`.
  - **Patterns:** `load_journal`, `step_conflict`, `undo_action`, `drop_identity_of`, and the fake Todoist client in the test module.
  - **Test scenarios:**
    - Unassign, then `unassigned_today` lists the row with `action_id`.
    - Unassign then reassign restores the exact before-image due date, for date, datetime and recurring tasks. The list is then empty.
    - Unassign â reassign â unassign runs a real second write, not a duplicate.
    - A due date edited in Todoist after unassign makes reassign refuse with "task due changed after unassign". Nothing is written.
    - Reassign of an identity with no applied unassign raises a clean `RuntimeActionError`.
    - A `failed`, `compensated` or `undone` unassign is not listed.
  - **Verification:** `app/.venv/bin/python -m pytest -q tests/test_runtime_actions.py`
  - **Depends on:** none.

- **U2. Plan-inputs field and reassign route**
  - **Goal:** `/plan-inputs` returns `unassigned_today`, and `POST /runtime-actions/reassign {identity}` is gated by `_require_day_setup`.
  - **Requirements:** R1, R3, R4.
  - **Files:** `app/main.py`, `app/tests/test_runtime_actions_routes.py`, `app/tests/test_plan_inputs.py`.
  - **Patterns:** the `post_runtime_action_undo` route (`main.py:6501`) and the `dropped_today` emit (`:4793`).
  - **Test scenarios:**
    - The field is present and empty by default.
    - An applied unassign appears in the field.
    - The route refuses before Day Setup is confirmed.
    - The route returns 409/422 on a conflict.
    - AE2: a dropped Capacities row is absent from `assigned`, absent from the commit's server-assigned set, and produces no PHEP mirror or Capacities client call.
  - **Verification:** `app/.venv/bin/python -m pytest -q tests/test_runtime_actions_routes.py tests/test_plan_inputs.py tests/test_runtime_actions.py`
  - **Depends on:** U1.

- **U3. Frontend contract, selectors and controller**
  - **Goal:** Add `controller.setAssigned(id, assigned)`, mapping each case to the existing verbs:
    - Todoist, Unassigned: `runRowBatch(id, "unassign")`.
    - Todoist, Assigned: `adapter.reassign(identity)`.
    - Capacities or calendar, Unassigned: `drop_from_plan`.
    - Capacities or calendar, Assigned: `restore_today`.
    - Then `refreshSources`.
  - Also add a selector `assignmentRows(s)` returning `{assigned, unassigned}`, and restrict `skippedTodayRows` to Todoist.
  - **Requirements:** R1âR4.
  - **Files:**
    - `frontend/src/model/types.ts` (`UnassignedTodayRow`, `inputs.unassignedToday`).
    - `frontend/src/adapters/wire.ts` (`projectUnassignedToday`).
    - `frontend/src/adapters/adapter.ts`, `api.ts`, `fixture.ts` (`reassign`).
    - `frontend/src/store/store.ts`, `frontend/src/store/controller.ts`.
    - Tests: `frontend/src/store/task-actions-controller.test.ts`, `frontend/src/adapters/wire.test.ts`.
  - **Test scenarios:**
    - AE1: unassigning a Todoist row puts it in `unassigned`, and setting it Assigned calls `reassign` then refreshes.
    - AE2: unassigning a Capacities row sends `drop_from_plan` only and makes no Capacities adapter call.
    - Dropped Capacities rows appear in `unassigned`, not in `skippedTodayRows`.
    - Malformed `unassigned_today` rows are dropped by the projection.
    - A gate reason blocks every call.
  - **Verification:** `cd frontend && npx vitest run src/store/task-actions-controller.test.ts src/adapters/wire.test.ts && npx tsc --noEmit`
  - **Depends on:** none (uses the KTD2 shape).

- **U4. Toggle and Unassigned section (route to `design`, unattended)**
  - **Goal:**
    - A per-row Assigned/Unassigned switch (`role="switch"`, 44px floor) in TaskRow.
    - A new `UnassignedToday` section under the bands, whose rows carry the switch.
    - Skip today hidden on Capacities and calendar rows.
    - The Queue C key on a Capacities row sets Unassigned. The ShortcutHint and empty-state copy are updated.
  - **Requirements:** R1, R2, R5.
  - **Files:** `frontend/src/ui/TaskRow.tsx`, `frontend/src/ui/Queue.tsx`, `frontend/src/ui/UnassignedToday.tsx` (new), `frontend/src/app.css`, `frontend/src/ui/assign-toggle.test.tsx` (new), `frontend/src/ui/task-row-actions.test.tsx`, `frontend/src/ui/queue-hint-layout.test.tsx`.
  - **Test scenarios:**
    - The switch calls `setAssigned` with the right value.
    - An Unassigned row renders in the section with its source chip.
    - Capacities rows have no Skip today button.
    - The disabled reason is announced before Day Setup is confirmed.
  - **Verification:** `cd frontend && npx vitest run src/ui/assign-toggle.test.tsx src/ui/task-row-actions.test.tsx src/ui/queue-hint-layout.test.tsx && npx tsc --noEmit`, plus render_check screenshots.
  - **Depends on:** U3.

- **U5. Source menu reduction**
  - **Goal:** Remove the unassign/skip-occurrence item, the `"unassign"` branch of `apply`, and the Capacities note. Keep Hide always and Delete.
  - **Requirements:** R5.
  - **Files:** `frontend/src/ui/SourceMenu.tsx`, `frontend/src/ui/source-menu.test.tsx`, `frontend/src/ui/more-menu-consistency.test.tsx` if it asserts the item.
  - **Test scenarios:**
    - The menu has exactly Hide always and Delete sourceâ¦.
    - Keyboard Home/End still cycles the remaining items.
    - Any test expectation that is reversed is called out in the handoff.
  - **Verification:** `cd frontend && npx vitest run src/ui/source-menu.test.tsx src/ui/more-menu-consistency.test.tsx && npx tsc --noEmit`
  - **Depends on:** U3 (only for merge order; no shared files).

## Verification Contract

- Backend full (from `app/`): `.venv/bin/python -m pytest -q`
- Frontend full (from `frontend/`): `npm test` and `npm run typecheck`
- Acceptance mapping:
  - AE1: U1 restore and conflict tests, U3 controller test, U4 switch test.
  - AE2: U2 commit/PHEP test and U3 no-Capacities-call test.
- Manual smoke (operator, live):
  - Unassign a Todoist row; it shows in Unassigned and its Todoist date is cleared or advanced.
  - Re-assign it; the prior due date returns.
  - Set a Capacities row Unassigned, reload, and it stays Unassigned. Commit creates no PHEP task for it.

## Deliberate exclusions

- No Capacities write of any kind, and no change to Capacities selections or to the forgot-strip `assign` verb.
- No migration of legacy `included=false` local exclusions."}],"api":"claude-bridge","provider":"claude-bridge","model":"claude-opus-5-5","usage":{"input":2,"output":6549,"cacheRead":141084,"cacheWrite":495,"totalTokens":148130,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0},"reasoning":36},"stopReason":"stop","timestamp":1792438965618,"durationMs":55171,"thinkingLevel":"medium"},"timestamp":"2026-10-19T19:43:40.790Z","cwd":"/Users/walle-mini/Repos/WALL-E_PIOS"}

## Operator Decisions (2026-10-19)

- D1: A — Unassign keeps clearing a non-recurring task's date (advances a recurring occurrence); re-assign restores the exact prior due date.
- D2: A — Skip today stays for Todoist rows only; hidden on Capacities/calendar rows.
- D3: A — batch Unassign stays unchanged; Capacities rows stay excluded with a reason pointing to the toggle.

