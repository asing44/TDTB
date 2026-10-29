---
title: Unified Sources Refresh - Plan
type: feat
date: 2026-10-19
topic: unified-sources-refresh
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-brainstorm
execution: code
---

# Unified Sources Refresh - Plan

## Goal Capsule

- **Objective:** After the operator presses one refresh control, today's plan inputs are current and commit is blocked only when the data may be wrong.
- **Means:** One "Refresh sources" control replaces the separate Refresh and Rescan controls in the cockpit.
- **Product authority:** The operator's decisions in the 2026-10-19 brainstorm, marked `session-settled` below.
- **Open blockers:** None blocking planning; see Outstanding Questions.
- **Not active scope:** the agent-run `tdtb-refresh` artifact (habits, micro-adventure ideas) and any change to how Todoist is read.

## Product Contract

### Summary

The cockpit has a Refresh button and a Rescan button, and a leftover warning can block commit even when today's rows are correct. This plan gives the operator one refresh control that does what is needed, waits out Capacities rate limits visibly, and blocks commit only for failed reads or possibly wrong rows.

### Problem Frame

The operator always refreshes from the UI and assumed that updated everything. In `sources.mode: live` with `capacities_intake: direct`, Todoist is read live and Capacities rows come from the app's own published refresh cache, so the assumption was largely right (`app/main.py`, `app/capacities_refresh.py`). What confused them was everything around it.

- Refresh and Rescan are two controls with different scopes ("Rescan additionally re-lists structures", `frontend/src/ui/CapacitiesRefreshPanel.tsx`).
- On 2026-10-19 a rescan stalled at 176 of 227 reads after a Capacities rate limit, with the "Rescanning sources…" pill stuck and no explanation.
- Two warnings blocked commit without meaning the rows were wrong: "structure unchanged since 2026-10-16; Refresh to check it again" and "ignored Assigned On declaration … unknown property" (`app/capacities_intake.py`, `app/capacities_adapter.py`, `frontend/src/adapters/wire.ts` `sourceHealthOf`).
- Todoist and Calendar dots turned amber from a Capacities-only warning, because non-Capacities dots read global health (`frontend/src/ui/connections/ConnectionsScreen.tsx`).

### Key Decisions

- **One refresh control, no separate Rescan.** (session-settled: user-directed — chosen over keeping both with explanations or leaving both as-is: the operator is confused by two controls and always uses the UI.) Governs R1, R2.
- **Commit blocks only on failed reads or possibly wrong rows.** (session-settled: user-directed — chosen over blocking on any warning or never blocking.) Governs R5, R6.
- **A rate-limited refresh waits visibly and finishes by itself.** (session-settled: user-directed — chosen over stopping with saved progress or discarding as today.) Governs R3, R4.

### Requirements

**Refresh control**

- R1. The cockpit offers one control, "Refresh sources", and no separate Rescan control.
- R2. Pressing it re-reads Todoist, refreshes Capacities, and re-lists Capacities structures only when they may have changed or a current warning calls for it.
- R7. After a run, the panel states what the run did and when it finished.

**Rate limits**

- R3. When Capacities rate-limits a run, the run keeps waiting, keeps what it already read, and shows a countdown with read progress (for example "Capacities busy, resuming in 40s · 176/227").
- R4. A waiting or running refresh can be cancelled, and the previously published data stays in use until a run completes.

**Commit gating**

- R5. Commit is blocked when a Todoist or Capacities read failed, when a refresh was discarded or incomplete, or when a mapped structure has never been read.
- R6. Every other warning, including "unchanged since…", an unknown Assigned On property, and skipped not-plannable objects, appears as a visible note with a way to fix it and does not block commit.

**Source status**

- R8. Each source's status dot in Connections reflects that source's own state, not the global health.

### Key Flows

- F1. Routine refresh
  - **Trigger:** The operator presses "Refresh sources".
  - **Steps:** Todoist is re-read and Capacities refreshed; structures are re-listed only if needed; the panel shows the result and time.
  - **Outcome:** Plan inputs are current and commit is available unless R5 applies.
  - **Covered by:** R1, R2, R7
- F2. Rate-limited refresh
  - **Trigger:** Capacities returns a rate limit mid-run.
  - **Steps:** The panel shows the countdown and progress, the run resumes by itself, and the operator may cancel.
  - **Outcome:** The run completes without a second press, or ends cancelled with old data intact.
  - **Covered by:** R3, R4

### Acceptance Examples

- AE1. **Covers R3, R4.** Given a run at 176 of 227 reads receives a rate limit, when 30 seconds pass, then the panel shows the countdown and 176/227, the run resumes by itself, and Cancel leaves the prior published data in use.
- AE2. **Covers R5, R6.** Given a Capacities structure shows "unchanged since 2026-10-16" and all reads succeeded, when the operator opens commit, then commit is available and the note is visible with a fix link.
- AE3. **Covers R5.** Given the Todoist read failed in the last run, when the operator opens commit, then commit is blocked and the reason names the failed read.
- AE4. **Covers R8.** Given only a Capacities warning exists, when the operator views Connections, then the Todoist and Calendar dots are not amber.

### Scope Boundaries

- Not changing the agent-run `tdtb-refresh` artifact or the habits and micro-adventure data it supplies.
- Not changing how Todoist is read or its credentials.
- Not changing which structures are mapped or how Assigned On is declared; only how problems with them are reported.

### Dependencies / Assumptions

- Assumes the direct refresh coordinator keeps its single-flight rule and 30 requests per 60 seconds pacing (`app/capacities_refresh.py`).
- Assumes the machine runs `sources.mode: live` with `capacities_intake: direct`.

### Outstanding Questions

None. The questions deferred from the brainstorm are resolved under Planning Contract.

### Sources / Research

- `app/capacities_refresh.py` for modes, pacing, retry bounds, single-flight, cancel, and `staleConfiguration`.
- `frontend/src/adapters/wire.ts` `sourceHealthOf` and its advisory regex for current blocking rules.
- `frontend/src/ui/CapacitiesRefreshPanel.tsx` and `frontend/src/ui/Header.tsx` for current controls and pill text.
- `docs/plan/2026-10-09-1345-feat-tdtb-capacities-cached-refresh-plan.md` for the cached-refresh requirements this builds on.

---

## Planning Contract

### Code reality that shapes this plan

- Every full-scope run already re-lists every mapped structure. `start("refresh")` calls `ensure_contract()`, which re-fetches structures, and then `enumerate_structure` for every configured type (`app/capacities_refresh.py` `_acquire`).
- In full scope, the only extra thing Rescan does is ignore the 10-minute content TTL (`_read_or_reuse(rescan=True)`). The panel hint "Rescan additionally re-lists structures" does not match the code.
- "unchanged since <day>" (`app/capacities_intake.py` `_type_warnings`) fires only when one type's `listing_checked_at` trails the newest by more than the skew. Only a scoped Rescan produces that. Any full-scope run resets every check time and clears the warning.
- "is not in the published generation; needs Refresh." is emitted for every mapped structure that is missing from `snapshot.type_check_times`. Today it is advisory and does not block (`wire.ts` `advisoryMissingGeneration`; `store.test.ts` "a generation-only warning keeps health ok").
- A failed, interrupted or stale run writes its outcome only to the job document (`state/jobs/<sha256(space_id)>.json`). It never reaches `/plan-inputs` `source_warnings`, so commit gating cannot see it today.
- Production builds the coordinator with the default `max_retries=3` (`app/capacities_builder.py`). The pacer caps every wait at `max_backoff=30` and clamps `Retry-After` to that cap.
- One string gate drives every commit/sequence block: `sourceHealthOf(warnings)` gives `sourceHealth`, which feeds `sourceHealthBlocked`, `footerBlockedReason`, `ApprovalDrawer` and `ActionDock`.

### Key Technical Decisions

- KTD1. **The single control runs the existing `refresh` mode, full scope. No new `auto` mode.** Governs R1, R2.
  - Listing is already unconditional and cheap: one call per page per type, at most `max_pages`. That settles question (a): no "may have changed" heuristic is needed. Every press re-lists all structures and re-reads only content that is uncached or older than the TTL.
  - The `rescan` API mode and scoped Rescan stay server-side for compatibility. Tests, curl and `docs/operations/capacities-intake-cutover.md` use them. The UI stops exposing them.
  - `CapacitiesRefreshMode` keeps `"rescan"` so wire projection of old job documents still works.
- KTD2. **Todoist re-read stays on the existing terminal hook.** Governs R2.
  - `useCapacitiesRefreshJob` `onTerminal` already calls `controller.refreshSources()`, which does one `GET /plan-inputs`, and that reads Todoist live.
  - The control falls back to `controller.refreshSources()` directly when Capacities is unconfigured (`status.configured === false`) or the start returns 503.
  - This replaces the degraded-state "Retry" path, so there is one action in both pill states.
- KTD3. **Rate-limit waits are bounded by a per-run wait budget, not a retry count.** Governs R3, R4.
  - `RefreshCoordinator(max_retries: int | None = None, rate_limit_budget_seconds: float = 900.0, max_backoff: float = 120.0)`.
  - `None` means unlimited retries per request, stopped when the cumulative rate-limit sleep in the run exceeds the budget.
  - An explicit int keeps today's count bound exactly. The test helper `_coordinator` passes `max_retries=3`, so existing tests keep count semantics.
  - `max_backoff` rises to 120s so a 60s `Retry-After` is honored rather than clamped to 30s and re-hit.
  - When the budget runs out, the run fails as today: `noCapacities`, `FAILURE_WARNING` plus a `Cause:` line, partial reads kept, no install. That answers question (d).
- KTD4. **The wait is visible in the job document.**
  - The pacer gains an `on_wait(endpoint, seconds, reason)` callback. Before each rate-limit sleep, the coordinator writes `progress.waiting = {"reason": "rateLimited", "endpoint", "retry_after", "resume_at"}`, where `resume_at` is a wall-clock epoch. It clears `waiting` to `null` after the sleep.
  - Ordinary window pacing does not set `waiting`. Only 429 or reset-header cooldowns do.
  - After listing, the coordinator writes `progress.to_read`: the count of listed objects that are uncached, past the TTL, or all objects under rescan. That gives "176/227".
  - Status keeps its existing keys. New keys are additive.
- KTD5. **The outcome of the last run becomes a source warning.** This is the one string that lets R5 reuse the existing gate. Governs R5, R4.
  - `/plan-inputs` with direct intake reads the job document through a new pure helper. It appends one bounded warning when the last run ended `failed` (any outcome), `interrupted` or `staleConfiguration`, and no live coordinator is running: `Capacities refresh did not complete (<outcome>); showing the previous complete result. Refresh sources to retry.`
  - A `cancelled` run adds no warning (OQ-P2).
  - A running job adds no warning, so prior data stays in use (R4).
  - A non-terminal job document with no live coordinator counts as `interrupted`, mirroring `reconcile()`.
  - The warning joins `direct_warnings`, so `capacities_intake.state` turns `degraded` or `unavailable` truthfully.
- KTD6. **Reclassify blocking by extending the advisory allowlist, not by inverting it.** Governs R5, R6.
  - Unknown future warnings keep blocking (fail closed).
  - Add as advisory:
    - `… unchanged since …; Refresh to check it again.`
    - `ignored Capacities Assigned On declaration … unknown property …`
    - `… is not a date`
    - `settings Assigned On declaration … shadows …`
  - Keep as advisory: skipped-object and reminder notices.
  - Remove from advisory, so it blocks: `… is not in the published generation; needs Refresh.` This is R5's "mapped structure never read", and it is already derivable from the generation, which settles question (b). This deliberately reverses a prior decision (OQ-P3).
  - Keep blocking: `Capacities Assigned On values could not be parsed`, because rows may be misassigned (OQ-P4).
  - Keep blocking: Capacities partial-coverage warnings, refresh-required warnings, read-failure warnings and the KTD5 warning.
  - Make advisory: calendar warnings (`calendarWarnings`). This reverses the locked decision-17 gate by the operator's explicit choice on 2026-10-19; the calendar dot still shows its own amber note, and U3 must also check `frontend/src/adapters/api.ts` `refreshSources`, which throws on a calendar failure today.
- KTD7. **Per-source health is derived on the frontend from attributed warnings. The global pill keeps meaning "commit blocked".** Governs R8.
  - New `sourceHealthBySource(warnings) -> { todoist, calendar, capacities }`. It attributes each blocking warning by prefix: `/^Todoist/i`, `calendarWarnings`, `/Capacities/`.
  - Warnings from config, prompts, habits or artifacts colour no source dot.
  - Answer to question (c): R8 changes only the Connections dots. The global pill and drawer stay driven by `sourceHealth`. After KTD6 that only turns amber for real blockers, so its "commit blocked" text stays truthful.
- KTD8. **Notes carry fix actions in the approval drawer.** Governs R6, R7 and AE2/AE3.
  - The drawer lists advisory notes, each with one fix action:
    - "unchanged since": Refresh sources.
    - Assigned On declaration: open Connections.
    - Skipped objects: existing Sources details hint.
  - The drawer's source blocker names the first blocking warning verbatim instead of "Sources degraded".

### Assumptions

- `sources.mode: live` with `capacities_intake: direct`, as the requirements plan assumes. In artifact mode, KTD5 adds nothing.
- A plan-inputs read of the job document is a local file read, with no provider call and no coordinator construction. It must not call `_refresh_coordinator()`, which can raise 503. Liveness comes from `getattr(app.state, "refresh_coordinator", None)` and `.is_running()`.
- The job namespace is `_digest(space_id)` (the default). The builder never passes `namespace`, so the helper can derive the path from `space_id`.

### Sequencing

- Backend: U1 then U2, serial because both touch `app/capacities_refresh.py`.
- Frontend: U3 first, because it holds the types and classifiers. U4, U5, U6 and U7 then run in parallel on disjoint files.
- Frontend can start alongside U1, against the KTD4/KTD5 wire shapes. The fixture adapter carries the new fields.

### Resolved Questions

- OQ-P1. Calendar warnings become advisory (operator choice, reverses decision-17). Governs R5, R6.
- OQ-P2. A cancelled run does not block commit; the last complete result stays valid.
- OQ-P3. A mapped structure missing from the published generation blocks commit until one refresh reads it (reverses today's advisory handling).
- OQ-P4. "is not a date" and "shadows" warnings are advisory; "values could not be parsed" stays blocking (default accepted).
- OQ-P5. Every press lists all structures unconditionally; no "may have changed" heuristic.

## Implementation Units

- U1. **Coordinator: budgeted rate-limit waits with visible countdown and read target**
  - **Goal:** A rate-limited run waits and resumes by itself within a wait budget. Status shows `waiting.resume_at` and `progress.to_read`.
  - **Requirements:** R3, R4. KTD3, KTD4.
  - **Files:** `app/capacities_refresh.py`, `app/tests/test_capacities_refresh.py`.
  - **Patterns to follow:**
    - Existing `ProviderPacer.call`/`sleep` and `_default_sleeper` cancellation. Waits must stay interruptible.
    - `_progress` and `_persist_job` for job writes.
    - Fake `Clock`/`Sleeper` in the test module.
  - **Approach:**
    - `max_retries: int | None`. Stop when `attempt >= max_retries` (int) or when cumulative rate-limit sleep plus the next delay exceeds `rate_limit_budget_seconds`.
    - Pass `max_backoff` through to the pacer.
    - Add an `on_wait` hook, called only from the 429 retry path and the reset-header cooldown in `before_request`.
    - After the listing loop, compute `to_read` with the same TTL predicate as `_read_or_reuse`. Factor out a `_needs_read(object_id, type_key, rescan)` helper.
  - **Test scenarios:**
    - Default (`max_retries=None`): five consecutive 429s with `retry_after=40` then success. The run publishes, `sleeper.calls` holds five 40s waits, and the status captured inside the sleeper shows `waiting.reason == "rateLimited"` and `resume_at == wall+40`. After completion, `waiting` is `None`.
    - The budget is exhausted (`budget=100`, `retry_after=60`): `failed`/`noCapacities` with a `Cause: CapacitiesRateLimited (retry after 60s)` line, partial reads kept, no snapshot.
    - `retry_after=60` is honored, not clamped to 30.
    - `progress.to_read` equals the stale/uncached count on refresh and the full count on rescan. On a back-to-back refresh within the TTL it is 0.
    - Cancel during a long wait gives `cancelled` and keeps the prior generation (AE1).
    - Unchanged and must still pass as-is (they pass explicit `max_retries` through the helper):
      - `test_rate_limit_retries_are_bounded_and_fail_truthfully`
      - `test_rate_limit_retry_honors_retry_after_and_eventually_publishes`
      - `test_cancel_interrupts_rate_limit_backoff`
      - `test_rate_limited_failure_records_retry_after_and_stage`
    - If the helper default changes, update these deliberately and say so in the handoff.
  - **Verification:** `app/.venv/bin/python -m pytest -q tests/test_capacities_refresh.py tests/test_capacities_refresh_route.py`.
  - **Depends on:** none.

- U2. **Plan-inputs: last-run outcome warning**
  - **Goal:** A failed, interrupted or stale last run blocks commit through `source_warnings` until a later run publishes.
  - **Requirements:** R5, R4. KTD5.
  - **Files:**
    - `app/capacities_refresh.py`: add the public pure helper `read_last_job(root, space_id) -> dict | None`, reusing `_load_job`'s validation.
    - `app/capacities_intake.py`: add `refresh_outcome_warnings(job, running: bool) -> list[str]` and the warning constant.
    - `app/main.py`: wire it into the direct-intake block next to `load_direct_rows`.
    - `app/tests/test_plan_inputs_sources.py`.
  - **Patterns to follow:**
    - `_type_warnings` for bounded fixed-string warnings.
    - The `intake_read` handling in `main.py`.
    - `capacities_builder._refresh_state_root` for the root. Expose a small `capacities_builder.refresh_state_root()` wrapper if it is needed.
  - **Test scenarios:**
    - A job doc with `outcome: noCapacities` gives the warning in `source_warnings` and `capacities_intake.state` becomes `degraded` (AE3 analogue for Capacities).
    - `interrupted` and `staleConfiguration` each give the warning.
    - `cancelled` and `published` give none.
    - A non-terminal doc while the coordinator is running gives none. A non-terminal doc with no live coordinator gives the warning.
    - A missing or corrupt doc gives none and no 500.
    - No provider call is made, asserted the way the existing route tests do.
  - **Verification:** `app/.venv/bin/python -m pytest -q tests/test_plan_inputs_sources.py tests/test_capacities_refresh.py`.
  - **Depends on:** U1 (same module).

- U3. **Frontend contract: status projection and health classifiers**
  - **Goal:** Types and wire carry `waiting`/`toRead`, and the blocking classification matches KTD6/KTD7.
  - **Requirements:** R3, R5, R6, R8.
  - **Files:**
    - `frontend/src/model/types.ts`: `CapacitiesRefreshProgress.toRead: number | null` and `waiting: {reason, endpoint, retryAfter, resumeAt} | null`.
    - `frontend/src/adapters/wire.ts`:
      - Projection of the new fields.
      - New advisory regexes.
      - Remove `advisoryMissingGeneration` from `sourceHealthOf`. Keep the `missingGenerationWarnings` export for notes.
      - New `sourceHealthBySource`, `advisoryNotes(warnings)` returning `{text, fix: "refresh" | "connections" | "details"}[]`, and `blockingWarnings(warnings)`.
    - `frontend/src/adapters/fixture.ts`: echo the new fields.
    - `frontend/src/adapters/api.ts`: calendar failure is non-throwing in `refreshSources`.
    - Tests: `frontend/src/adapters/wire.test.ts`, `frontend/src/store/store.test.ts`.
  - **Patterns to follow:** existing `advisorySkippedObject` and its doc comments; `projectCapacitiesRefreshStatus`.
  - **Test scenarios:**
    - "unchanged since" and "ignored … unknown property" give health `ok` (AE2).
    - "not in the published generation" gives `degraded`. Deliberately update the `store.test.ts` case "a generation-only warning keeps health ok and live commit unlocked" to expect blocked, and invert "the type-staleness warning still blocks live commit" to expect unblocked.
    - `Todoist read failed (timeout)` stays blocking.
    - A calendar warning no longer blocks (health `ok`); `refreshSources` in `frontend/src/adapters/api.ts` no longer throws on a calendar-only failure.
    - The KTD5 warning blocks.
    - An unknown warning blocks.
    - `sourceHealthBySource(["Capacities partial — …"])` gives `todoist: ok`, `calendar: ok`, `capacities: degraded` (AE4).
    - Projection of `waiting`/`to_read` from the wire, with null when absent.
  - **Verification:** `cd frontend && npx vitest run src/adapters/wire.test.ts src/store/store.test.ts src/adapters/capacitiesConnections.test.ts && npx tsc --noEmit`.
  - **Depends on:** none. Uses the KTD4/KTD5 shapes.

- U4. **Connections panel: one control, countdown and run summary**
  - **Goal:** The panel offers only "Refresh sources" and Cancel. While running it shows "Reading 176/227" or "Capacities busy, resuming in 40s · 176/227". After the run it states what happened and when.
  - **Requirements:** R1, R3, R4, R7. KTD1.
  - **Files:**
    - `frontend/src/ui/CapacitiesRefreshPanel.tsx`: remove the Rescan button and the inaccurate hint. Render the progress line and a "Finished <time>: <outcomeText>; N read, M reused" summary from `job.finishedAt` and progress.
    - `frontend/src/ui/useCapacitiesRefreshJob.ts`: add an exported pure `refreshProgressText(status, nowMs)` and a 1s tick while `waiting` is set. Update the header comment that says "Refresh and Rescan are two DISTINCT actions".
    - `frontend/src/ui/capacities-refresh.test.tsx`.
  - **Patterns to follow:** existing poll lifecycle (`POLL_MS`, clear on terminal/unmount) and `outcomeText`.
  - **Test scenarios:**
    - No "Rescan" button. Clicking "Refresh sources" calls `start("refresh","all")`, replacing the existing `toHaveBeenCalledWith("rescan","all")` assertion.
    - With `waiting.resumeAt = now+40` and read 176 of `toRead` 227, the text is "Capacities busy, resuming in 40s · 176/227". Fake timers advance the countdown.
    - Cancel is visible while waiting.
    - A terminal `published` status shows the finished time and summary.
    - A `failed` status shows the outcome text and warnings.
  - **Verification:** `cd frontend && npx vitest run src/ui/capacities-refresh.test.tsx && npx tsc --noEmit`.
  - **Depends on:** U3.

- U5. **Header: a single "Refresh sources" action**
  - **Goal:** The header pill action is always "Refresh sources" (job start, with an unconfigured fallback). It shows the waiting countdown and never says "Rescanning".
  - **Requirements:** R1, R2, R3. KTD1, KTD2.
  - **Files:** `frontend/src/ui/Header.tsx`, `frontend/src/ui/header.test.tsx`.
  - **Patterns to follow:**
    - The existing `refreshBusy` double-click guard.
    - `refreshProgressText` from U4. If U4 has not landed, import it from `useCapacitiesRefreshJob.ts` once both are merged. Otherwise sequence U5 after U4.
  - **Test scenarios:**
    - In both healthy and degraded states, the button label is "Refresh sources" and clicking starts the job.
    - With `status.configured === false`, it calls `controller.refreshSources()`.
    - While waiting, the pill text includes the countdown and progress.
    - A rescan-mode job from an old process still shows "Refreshing sources…". Update the existing rescan assertion near `header.test.tsx` `startCapacitiesRefresh("rescan", "all")`.
    - On terminal, `refreshSources` fires once.
  - **Verification:** `cd frontend && npx vitest run src/ui/header.test.tsx && npx tsc --noEmit`.
  - **Depends on:** U3, U4.

- U6. **Connections: per-source dots**
  - **Goal:** The Todoist and Calendar dots read their own health. A Capacities-only warning never colours them.
  - **Requirements:** R8. KTD7.
  - **Files:** `frontend/src/ui/connections/ConnectionsScreen.tsx` (`sourceDot`, plus the status text near the `sourceHealth === "ok"` render), `frontend/src/ui/connections-screen.test.tsx`.
  - **Patterns to follow:** existing `sourceDot` and `structureDot`. Keep the Capacities branch's intake-state and unmapped logic.
  - **Test scenarios:**
    - Only a blocking Capacities warning: Todoist and Calendar dots are green and the Capacities dot is amber (AE4).
    - A Todoist read failure: only the Todoist dot is amber.
    - A calendar warning: only the Calendar dot is amber.
  - **Verification:** `cd frontend && npx vitest run src/ui/connections-screen.test.tsx && npx tsc --noEmit`.
  - **Depends on:** U3.

- U7. **Approval drawer: named blockers and advisory notes with fixes**
  - **Goal:** A blocked commit names the failing read. Non-blocking warnings show as notes with a fix action.
  - **Requirements:** R5, R6. KTD8. AE2, AE3.
  - **Files:**
    - `frontend/src/ui/ApprovalDrawer.tsx`: `sourceBlockers` uses `blockingWarnings`. A new notes block uses `advisoryNotes`, where "refresh" calls `controller.refreshSources()` (or the job start if it is exposed through the controller) and "connections" dispatches `screen: "connections"`.
    - `frontend/src/ui/approval-source-notes.test.tsx` (new).
  - **Patterns to follow:** existing `blockers` list rendering; the Header `store.dispatch({type:"UI", patch:{screen:"connections"}})`.
  - **Test scenarios:**
    - AE2: with an "unchanged since" warning, the drawer has no source blocker and shows a note with a "Refresh sources" fix.
    - AE3: a `Todoist read failed (timeout)` blocker text contains that warning.
    - An Assigned On unknown-property note has an "Open Connections" fix that switches screen.
  - **Verification:** `cd frontend && npx vitest run src/ui/approval-source-notes.test.tsx src/ui/approval-resequence.test.tsx && npx tsc --noEmit`.
  - **Depends on:** U3.

## Verification Contract

- Backend focused (from `app/`):
  - `.venv/bin/python -m pytest -q tests/test_capacities_refresh.py tests/test_capacities_refresh_route.py tests/test_plan_inputs_sources.py`
- Backend full (from `app/`): `.venv/bin/python -m pytest -q`
- Frontend focused (from `frontend/`): each unit's `npx vitest run <files>`.
- Frontend full (from `frontend/`): `npm test` (vitest run) and `npm run typecheck` (`tsc --noEmit`).
- Cockpit bundle: if the repo's landing practice rebuilds `app/static/cockpit/assets`, run `npm run build:prod` at integration only. Root confirms the practice from prior landings.
- Acceptance mapping:
  - AE1: U1 cancel-during-wait test plus U4 countdown test.
  - AE2: U3 classifier test plus U7 note test.
  - AE3: U2 outcome-warning test plus U7 blocker text test.
  - AE4: U3 `sourceHealthBySource` test plus U6 dots test.
- Manual smoke (operator, live): press Refresh sources. Panel shows progress, then "Finished … published". Commit is available with any "unchanged since" note visible.

## Definition of Done

- R1–R8 and AE1–AE4 each map to a passing test named above.
- No UI path starts a `rescan` job. The `rescan` API mode still passes its existing tests.
- Rate-limited runs resume without a second press within the wait budget. Exceeding the budget fails truthfully, keeps partial reads, and installs nothing.
- Blocking is fail-closed: unknown warnings still block. Every test-expectation reversal (the `store.test.ts` generation and staleness cases, and the `capacities-refresh.test.tsx` rescan call) is called out in the unit handoff.
- OQ-P1..OQ-P5 are resolved above and reflected in U2, U3 and U7.
- Backend and frontend full suites and `tsc` pass. No secrets, paths or provider messages appear in new warnings.
- Dead code is removed: the Rescan button, the "Rescanning sources…" label, the inaccurate panel hint, and any abandoned-attempt code.
