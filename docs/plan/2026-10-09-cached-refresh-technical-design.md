---
title: TDTB Cached Refresh and Planning Flow - Plan
type: feat
date: 2026-10-09
execution: code
---

> **Historical (vault removal V4, 2026-10-12):** this document predates the removal of the Obsidian vault. Any vault root, `TDTB_VAULT_ROOT`, `sources.vault_enabled`, `tools/vault_rows_report.py`, `tools/migrate_vault_state.py` or `schema_guard` step below describes code that no longer exists. See `docs/plan/2026-10-12-vault-removal/plan.md`.


# TDTB Cached Refresh and Planning Flow - Plan

## Goal Capsule

**Objective.** The operator can refresh Capacities, review the day's commitments and candidates, and commit a time-blocked day from the existing TDTB cockpit without a separate Capacities ingestion skill.

**Means.** Direct REST acquisition with persistent properties and complete-result publication, integrated into the existing planning surfaces (KTD1–KTD4).

**Target repo:** TDTB.
**Authority.** This is a CE technical proposal, not approved execution state. The unchanged Product Contract at `docs/plan/2026-10-09-1345-feat-tdtb-capacities-cached-refresh-plan.md` owns R1–R39, KD1–KD12, and AE1–AE28. PIOS owns orchestration, approval, execution containment, review, and integration. TDTB owns domain behavior, TDD, and live-source safety. Root translates the accepted proposal into PIOS execution state; TDTB's approved plan evidence remains under `docs/plan/`. No downstream CE execution or review skill is automatically invoked.

**Execution profile.** Implement in an authorized linked worktree on a non-protected branch. Every behavior change is test-first. Children receive bounded units and do not recursively delegate. Root owns review and landing. Preserve unrelated work, the vault-off configuration, existing calendar routing, and non-Capacities ingestion.

**Stop conditions.** Stop on a failed execution-context guard, changed baseline or foreign ownership, invalidating Product Contract evidence, credential access, or an action requiring live-write/deployment approval. No unattended real-source writes, billed planning calls, or live restart.

---

## Product Contract

### Summary

Cover the full approved cached-refresh and three-screen planning flow by extending existing readers, rule evaluation, state stores, and UI components. Add test coverage for complete publication, cancellation, preserved selections, and prompt-export retry safety. Unrelated cleanup and ingestion changes remain outside this proposal.

### Problem Frame

The current artifact path requires separate Capacities ingestion, while the old live reader can defer content under a request budget. Neither provides the approved persistent complete-result contract or per-type editable rule builder. Existing prompt captures also lack the new opt-in and export lifecycle.

### Requirements

Adopt the source Product Contract's `### Requirements` in full: R1–R18 acquisition/cache, R19/R21 preservation, R20a–c UI, R22/R23/R36–R39 prompts, R24/R25 bounded gating, and R26–R35 eligibility and unknown review. Preserve its acceptance examples and scoped exclusions; the units below trace implementation to those stable IDs rather than restating their behavior.

**Confirmed clarification of R22.** Prompt due dates use the operator's local civil calendar date at Commit, even before the 02:00 TDTB planning-day rollover. Prompt draft and task identity remain keyed to the logical planning day. (session-settled: user-approved — chosen over logical-day due dates: exports are due on the current calendar date.) This clarification should be carried into the approved TDTB plan evidence during root materialization.

**Confirmed change to R20b (2026-10-10).** The copy-prompt action ("paste into any LLM") stays retired as U5 (`ae81772`) left it; Plan tasks has no copy-prompt control, despite the mockup footer. (session-settled: user-directed — chosen over restoring it as a secondary footer action.) Product Contract preservation: changed R20b — copy-prompt removed by operator decision; all other R20 behavior unchanged.

**Confirmed reading of R20 (2026-10-10).** "Not final visual design" means mockup example data, names, counters, and pixel values are illustrative. The screen *composition* is binding: stepper header, capacity headline with one segmented bar, Already-placed timeline, grouped compact task table, and one footer for Plan tasks, plus the equivalent regions for Set up day and Connections. A cockpit that passes behavior tests but renders a different composition does not satisfy R20. (session-settled: user-directed — the landed rail-and-card layout was rejected on sight.)

**Preservation interpretation.** R22 explicitly replaces only prompt-bearing daily-note/vault writes; R19 preserves other existing write paths. R27's unmapped Completion means unavailable, not an inferred unknown state. R29's mapped but undetermined completion is unknown. R31/R32 allow explicit warning-candidate selection, not automatic eligibility, and never bypass a known hard exclusion.

### Scope Boundaries

The source Product Contract's boundaries remain in force. Todoist rows and habit calculations retain their existing artifact intake. No new Capacities writes, autonomous schedule, TickTick integration, complete vault-reader deletion, calendar-class redesign, or general UI redesign is included. Re-composing the three screens to the mockup baseline (R20) is in scope and is not a "general UI redesign"; changing the design tokens, palette, typography scale, or dark-mode theme beyond what that composition needs is out of scope.

#### Deferred to Follow-Up Work

Recurring provider/resumption failures and harness tuning remain separate investigations. Host documentation reconciliations that require deprecated repository access are not silently run from this session. If launch readiness requires a specifically named external instruction or check, root resolves that boundary before execution.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **A dedicated persisted refresh coordinator owns acquisition and publication.** Extend transport/projection seams rather than executing long reads inside `/plan-inputs`. Maintain a server-owned job identifier, generation, scope, progress, and last complete snapshot. Serialize Capacities jobs across Refresh and Rescan with a machine-local lock; status reads do not make provider calls. Applies R1–R7/R10–R18/R34 (instantiates KD1/KD2/KD3; session-settled: user-approved — chosen over bridges and progressive publication: standalone refresh must publish a trustworthy complete result).
- KTD2. **Versioned content storage does not expire valid properties during ordinary Refresh.** Adapt `_ContentCache` behind the coordinator while leaving its legacy live-reader contract intact. Key by provider origin, space, type, and object; store content-read time separately from listing-check time. Import only compatible legacy entries without modifying their source. Use atomic per-object persistence and a separate atomic complete-generation pointer. Unlike the current 4096-entry/16-MB LRU, the new store may not silently evict members of a published scope: reaching a storage bound stops with a warning and preserves the last result. Applies R2/R5/R12/R15/R16/R21 (session-settled: user-approved — chosen over TTL-driven rereads: existing property changes require explicit Rescan).
- KTD3. **Completion evidence is scoped and generation-bound.** A successful ordinary Refresh completes all configured types before one generation is published. A type Rescan updates its type only after all pages and required reads succeed; combine it with unchanged, visibly dated prior type snapshots rather than claiming other types were rescanned. Any all-types failure prevents combined publication. Complete listing absence is the sole removal evidence. Failed/cancelled runs retain successful content reads but not candidate removals or partial result pointers. A configuration revision change invalidates stale job publication. Applies R4/R6/R10/R13–R16/R30/R34/R35.
- KTD4. **One source per provider at the consumer seam.** In the new Capacities mode, take Todoist rows and habits from the existing artifact, discard artifact Capacities rows, and merge coordinator rows once. Do not switch globally to live mode or alter artifact files to achieve this. Preserve the existing Capacities identity and URI path, duration-memory overlay ownership, name disambiguation, and digest indexing. Add provider-specific intake configuration with an explicit legacy rollback choice; a missing direct snapshot warns and yields no-Capacities, never silent artifact/live fallback. Applies R1/R21/R24b/R28.
- KTD5. **A typed per-type rule revision replaces hidden eligibility switches.** Reuse the safe recursive predicate machinery in `producer_rules.py` through a Capacities-specific normalized record interface. Persist active and draft revisions plus mappings and fallback duration through `SourceRecord`-style optimistic revision and atomic save. Validate properties and operators against discovered type shape; distinguish absent cached values from false predicate results. Compatible native/active/assigned controls edit visible rules rather than running extra hidden admission logic. No arbitrary code or user regex execution is added. Applies R8/R9/R20c/R26/R27/R31/R33.
- KTD6. **Published membership, evaluation, and selection are separate states.** Keep evaluated admitted rows distinct from supplementary warning candidates. Resolve hard exclusions before selection retention. Store explicit selections server-side by stable identity and revision, including acknowledged warning candidates and retained disabled-type rows. Feed selected trusted rows into the existing assigned digest index and commit eligibility boundary; never accept browser-supplied identity/routing as authorization. Treat uncertain tag evidence conservatively and keep the existing exclusion safety behavior. Applies R25/R27–R35.
- KTD7. **Capacity/source warnings do not become write authorization.** Audit frontend and server gates individually. Apply R24's fallback specifically to Capacities availability and overcapacity; do not waive calendar timing integrity, day-setup confirmation, native pins, overlap grants, snapshot checks, single-flight locking, or source identity validation. Unknown capacity/absent reliable calendar data is not the same as a measured overcapacity day. Applies R24/R25.
- KTD8. **Prompt draft, preferences, and export receipts have separate machine-local lifecycles.** Add versioned atomic stores under the existing state-directory abstraction. Drafts and receipts use logical day plus prompt key; opt-ins are undated. Use the server's persisted draft and choice at the manifest boundary, not unchecked request text. Remove B6 prompt-frontmatter output without retiring unrelated vault operations. Task creation remains Commit-only and excluded from calendar routing/duration. Applies R22/R23/R36–R39 (instantiates KD6/KD8).
- KTD9. **Prompt exports have explicit per-action receipts.** A known task ID drives updates, completed/deleted checks, and explicit re-export. Persist success immediately after each confirmed action so retry skips it. An ambiguous create is recorded as uncertain, not successful or deduplicated by content. Offer the operator's immediate retry with duplicate-risk acknowledgment under R39. Store the original civil due date with a confirmed export; a repeat logical-day Commit updates that same task using the current civil due date when permitted. Prompt tasks route to Inbox; apply PHEP to other Todoist export creation without moving existing upstream tasks between projects. Applies R22/R36–R39.
- KTD10. **UI integration replaces the view layer, not the engine.** Re-compose Set up day, Plan tasks, and Connections to the mockup baseline. The store, controller, adapters, model selectors, and wire contracts stay the source of truth, and existing behavior tests keep passing or are re-pointed to the new structure, never deleted to make room. Retain drag/accessibility interactions, duration overrides, native fixed timing, completion, and undo. Copy-prompt stays retired (R20b change). Replace warning-text parsing with structured Capacities job/coverage fields in the API adapter. Applies R7/R16/R20/R24/R25.
- KTD11. **The left rail and the tab strip are retired; a stepper header plus one footer replaces them.** Today the rail carries capacity, keys, setup chips, source health, and call counts, and `ScreenNav` floats above it as a tab pill. In the mockup these move into page regions: the step state (Set up day ✓ / Plan tasks / Commit) and the source-health pill with Retry go in the header, Connections is a header link, capacity becomes the headline card, the keyboard legend becomes a hint row above the task table, and readiness becomes the footer's blocked reason. The existing `ui.screen` state remains the routing source. Step 3 (Commit) maps onto the current approval/commit flow, not a new screen. (session-settled: user-directed — chosen over polishing the rail layout: the 5ad346d polish pass was rejected as the wrong target.) Applies R20/R20a–c.
- KTD12. **Task grouping maps onto the existing band and state model, without a new allocator.** "Critical" is the Critical band. "When there's room" merges the High and Everything-else bands in the view and keeps their order for trimming. "Fixed" is the scheduled/native-fixed set. "Out today" combines Excluded today and Dropped today, collapsible, with Include today. The trim plan and sequencing order in the model stay unchanged; only grouping labels and section membership change in the view. Applies R20b.
- KTD13. **Visual acceptance against the mockup is a gate, not a review nicety.** Every UI unit ships with fixture screenshots at 1500×1000 and 900×1000. Each is checked region by region against a reference render of the matching mockup screen, plus a sparse fixture that reproduces the live state Adam saw (stale artifact, vault off, no assigned rows, degraded sources). jsdom tests cannot catch layout, so a unit whose screenshots show a missing region, overlap, clipping, or a dead area fails even when the tests pass. Live acceptance is a GET-only screenshot after an attended `restart-live.sh`. (session-settled: user-directed — the U1–U5 landing passed 4,000+ tests and two reviews without anyone looking at the render.) Applies R20.

### High-Level Technical Design

**Component and data-flow topology**

```mermaid
flowchart TB
  UI[Three-screen cockpit] --> API[Job and configuration API]
  API --> JOB[Single-flight Capacities coordinator]
  JOB --> REST[Configured-type REST listings and content]
  JOB --> CACHE[Versioned property store]
  CACHE --> EVAL[Typed rule and exclusion evaluation]
  EVAL --> SNAP[Atomic complete snapshot and warning review]
  ART[Existing artifact: Todoist and habits] --> INTAKE[Provider-specific consumer]
  SNAP --> INTAKE
  INTAKE --> INDEX[Trusted digest and explicit selection index]
  INDEX --> PLAN[Existing sequence and commit authorization]
  UI --> DRAFT[Local drafts, opt-ins, export receipts]
  DRAFT --> PLAN
  PLAN --> WRITES[Existing calendar and Todoist writers]
```

**Refresh protocol, mode, and publication decisions**

```mermaid
flowchart TB
  START[Refresh or scoped Rescan] --> REV[Capture configuration revision and acquire job lock]
  REV --> LIST[Finish every page of selected scope]
  LIST --> OK{Listing complete?}
  OK -->|No| OLD[Retain prior snapshot and warn]
  OK -->|Yes| MODE{Ordinary or Rescan?}
  MODE -->|Ordinary| NEW[Read only missing object content]
  MODE -->|Rescan| ALL[Read every scoped object]
  NEW --> EVAL[Evaluate rules and record unknown review candidates]
  ALL --> EVAL
  EVAL --> CHECK{Reads succeeded and revision unchanged?}
  CHECK -->|No| OLD
  CHECK -->|Yes| PUB[Publish one complete generation and confirmed removals]
```

**Acquisition state machine and interruption lifecycle**

```mermaid
stateDiagram-v2
  [*] --> idle
  idle --> listing: start
  listing --> hydrating: enumeration complete
  hydrating --> evaluating: required reads successful
  evaluating --> publishing: configuration revision current
  publishing --> complete: atomic snapshot installed
  listing --> failed: provider or pagination failure
  hydrating --> failed: unreadable required content
  evaluating --> failed: revision changed
  listing --> cancelled: cancel
  hydrating --> cancelled: cancel
  evaluating --> cancelled: cancel
  complete --> idle
  failed --> idle
  cancelled --> idle
```

A process restart leaves the published snapshot usable and reports an interrupted job instead of resuming stale publication. Successful object writes remain available for the next run. Publication is a small protected atomic step; cancel after publication does not undo a completed generation.

**Rule grammar and evaluation decision shape**

Directional grammar: a rule is an AND group, OR group, NOT child, or typed property comparison. Property operands reference discovered stable keys; comparisons are restricted to compatible equality, membership, existence, ordering, and date operations already supported by the reusable evaluator. Three-valued evaluation propagates unknown rather than treating a missing value as false. Known hard exclusions take precedence over inclusion and explicit selection; unmapped Completion is unavailable rather than unknown (R27/R29).

**Prompt export lifecycle**

```mermaid
flowchart TB
  COMMIT[Commit current persisted prompt] --> OPT{Opted in and nonblank?}
  OPT -->|No| SKIP[Leave existing task untouched]
  OPT -->|Yes| RECEIPT{Receipt state?}
  RECEIPT -->|No prior attempt| CREATE[Create Inbox task with civil due date]
  RECEIPT -->|Known ID| READ[Read task and validate status]
  READ -->|Open| UPDATE[Update same task]
  READ -->|Completed| WARN[Leave untouched; warn on changed text]
  READ -->|Deleted| REEXPORT[Require explicit re-export]
  RECEIPT -->|Uncertain| ACK[Offer immediate retry with duplicate-risk acknowledgment]
  ACK --> CREATE
  CREATE --> RESULT[Persist each known success or uncertain outcome]
  UPDATE --> RESULT
```

**Plan tasks screen composition (directional, top to bottom; KTD11)**

```text
┌ header ── date · day kind · frame ─ [✓ Set up day]─[2 Plan tasks]─[3 Commit] ─ (source pill · Retry) ─ Connections ┐
│ section title + scope note ("Edits here affect today only…")                                                       │
│ CAPACITY CARD   headline (Over by / left) · one-line cause · one segmented bar + capacity marker · legend · Details │
│ ALREADY PLACED  hour axis over the day frame · lanes: Calendar / Work / Anchors · selected-block detail row · note  │
│ hint row        hover/focus actions · C E D ←→ ↑↓ · today-only scope note                                          │
│ FIT TODAY       Critical │ When there's room   (row: Assigned · title+source tag · due · slider+dur · actions)     │
│ FIXED           native/scheduled rows with Protected · Allow move                                                  │
│ OUT TODAY       excluded + dropped, collapsible, Include today                                                     │
└ footer ── ← Day setup · blocked reason ───────────────────────────────── [Sequence & commit <dur> · call budget] ┘
```

Set up day (U12) and Connections (U13) share the same header and footer frame and swap only the body regions.

### Implementation Constraints and Risks

The API's content-read limit is 30 requests/60 seconds; listings have separate provider limits and page cost. Use one shared endpoint-aware pacer, honor reset/retry headers and 429 responses, and test with a fake clock. Static per-refresh budgets and producer coverage cursors are not complete-result pacing. Bound retries and surface terminal errors rather than waiting indefinitely. Cache failure must never be represented as a freshly read value.

The existing server commit path validates source identity from a dated digest index and protects native timing. The candidate-selection change must extend that authority, not bypass it. The frontend already retains a last view on failure, but that is not a durable server snapshot. Prompt content is local personal state: never include it or credentials in diagnostic logs, generated fixtures, or repository evidence.

### Deferred Implementation Details

Exact route/helper names, safe storage capacity thresholds, and retry-count tuning are determined within units. Detailed visual spacing is also settled within units, but always judged against the reference renders (KTD13), not chosen freely. Whether the capacity headline and durations display in blocks (current) or hours and minutes (mockup) is a non-blocking open question. Keep the current block formatter until the operator chooses. Measure warm refresh latency rather than claiming the approximate target from mocks. No launch-blocking product question remains; credential provisioning, approved baseline/worktree, and required policy checks must be resolved before implementation begins.

---

## Implementation Units

### U1. Durable cache and complete snapshot foundation

**Goal:** Persist restart-safe content and complete generations without TTL-driven rereads or silent eviction.
**Requirements:** R2/R5/R6/R13/R15/R16/R21/R34; KTD2/KTD3; AE1/AE6/AE22.
**Dependencies:** None.
**Files:** `app/capacities_builder.py`, new `app/capacities_refresh_state.py`, new `app/tests/test_capacities_refresh_state.py`, `app/tests/test_capacities_builder.py`.
**Approach:**
1. Add versioned per-object persistence and a namespace-isolated complete snapshot store using existing atomic state-store patterns.
2. Import compatible legacy cache content read-only, recording original freshness rather than fabricating current read times.
3. Protect generation installation and reconcile interrupted job metadata on restart.
**Execution note:** Demonstrate failing durability/publication tests before behavior changes.
**Test scenarios:**
- Covers AE1. A valid entry older than the old TTL remains available with its original content-read time.
- Covers AE6. Absence in an incomplete listing does not evict a published member; complete confirmed absence does.
- Corrupt schema, namespace mismatch, or exhausted storage prevents installation and preserves the prior generation.
- Covers AE22. Interrupt a run after successful content writes; restarting preserves those reads and the old snapshot.
- Concurrent writers cannot replace a newer revision or leave a torn generation pointer.
**Verification:** Durable state round-trips and failure recovery pass; legacy cache tests remain unchanged in meaning.

### U2. Paced Refresh and scoped Rescan jobs

**Goal:** Complete configured scope with truthful progress and cancellation.
**Requirements:** R1/R3–R7/R10–R18/R19/R34/R35; KTD1–KTD3; AE1/AE2/AE4–AE7/AE22/AE23.
**Dependencies:** U1.
**Files:** `app/capacities_adapter.py`, `app/capacities_builder.py`, new `app/capacities_refresh.py`, `app/main.py`, new `app/tests/test_capacities_refresh.py`, new `app/tests/test_capacities_refresh_route.py`.
**Approach:**
1. Extract reusable listing/content access from legacy budget behavior without rewriting the legacy reader.
2. Add coordinator start/status/cancel seams, shared pacing, revision checks, and complete-scope projection.
3. Replace the max-pages success possibility with an explicit incomplete outcome; retain repeated-cursor protection.
**Test scenarios:**
- Covers AE1/AE2. Warm listing reads no content; one new object costs one successful content read.
- Covers AE4. Single-type Rescan rereads that type only; all-types Rescan rereads all configured types.
- Covers AE5. Failure on page two, repeated cursor, or defensive page bound never publishes truncated membership.
- Covers AE7. Cold import spans multiple paced windows and remains responsive to status/cancel.
- Rate reset and 429 backoff are honored with a fake clock; terminal authentication/transport failure warns without exposing secrets.
- Covers AE22. Cancel during hydration retains successful content writes but installs no new generation.
- Two racing jobs, config save during evaluation, and process interruption cannot publish stale scope.
**Verification:** Fake-provider integration proves request pacing, complete pagination, single-flight behavior, and atomic results without real API calls.

### U3. Typed per-type rules, mappings, and selection authority

**Goal:** Make saved rules authoritative while keeping unknown review and explicit selections safe.
**Requirements:** R8/R9/R20c/R26–R33/R35; KTD5/KTD6; AE3/AE13–AE21/AE23.
**Dependencies:** U1; U2 for job integration.
**Files:** `app/producer_rules.py`, `app/capacities_settings.py`, `app/capacities_builder.py`, `app/tag_exclusions.py`, `app/runstate.py`, new `app/capacities_rules.py`, new `app/tests/test_capacities_rules.py`, new `app/tests/test_capacities_selections.py`, `app/tests/test_capacities_source_route.py`.
**Approach:**
1. Adapt recursive predicates to discovered per-type property schemas and separate active/draft revisions.
2. Normalize admission, completion availability, hard exclusions, and warning candidates without guessed mappings.
3. Persist explicit selections and retained disabled-type rows by identity; supply trusted selected rows to digest indexing.
**Test scenarios:**
- Covers AE3/AE14. Nested AND/OR/NOT changes evaluate cached content without reads or a hidden active toggle.
- Covers AE15. Missing Duration/Assignment/Completion mappings use explicit fallbacks and unavailable completion.
- Covers AE17. Excluded tags or known completed/dropped states remove schedulable selections with notices.
- Covers AE18. Rule change/type removal retains prior selection with warning, except hard exclusions.
- Covers AE19/AE20. Missing cached property creates a supplementary warning candidate; explicit selection succeeds with safe values and no false evaluation claim.
- Covers AE21. Removed schema property or incompatible comparison cannot activate; optimistic save conflict preserves active rules.
- Malicious property paths/unsupported operators and browser-forged identities cannot become trusted selections.
**Verification:** Rule truth tables, settings revision tests, and server-index selection integration pass while producer regressions remain intact.

### U4. Provider-specific intake and bounded Commit gating

**Goal:** Consume direct Capacities once and permit approved warning-only paths without weakening write safety.
**Requirements:** R21/R24/R25/R28/R30/R32/R35; KTD4/KTD6/KTD7; AE10–AE12/AE16/AE18/AE20/AE23.
**Dependencies:** U2/U3.
**Files:** `app/app_config.py`, `app/artifact_source.py`, `app/external_sources.py`, `app/main.py`, `app/runstate.py`, `app/tests/test_main_api.py`, new `app/tests/test_capacities_intake.py`, frontend `src/adapters/api.ts`, `src/adapters/wire.ts`, `src/store/store.ts`, corresponding existing adapter/store tests.
**Approach:**
1. Add provider-specific selection and structured job/coverage metadata while preserving Todoist/habit artifact parsing.
2. Reconcile selected Capacities rows into trusted server indexes and enforce their identity at shadow/live boundaries.
3. Change only applicable warning gates and retain independent safety failures.
**Test scenarios:**
- Old artifact Capacities rows and new direct rows with the same identity yield exactly one direct row.
- Calendar and habits still contribute when live Todoist constructors are stubbed to raise.
- Covers AE10/AE11. Failed direct refresh uses last complete snapshot or explicit no-Capacities, with correct warnings and allowed valid Commit.
- Covers AE12. Measured overcapacity warns; native pin changes, unauthorized identities, malformed input, stale planning snapshot, missing day setup, and concurrent Commit still fail before writes.
- Covers AE16/AE20. Unselected candidates never consume capacity or enter the manifest; acknowledged warning candidates use server-authorized fields.
- Calendar degradation is not accidentally relaxed as Capacities failure.
**Verification:** Fake-writer shadow/live route tests prove both bounded relaxation and preserved denial paths.

### U5. Local prompt drafts and persistent opt-ins

**Goal:** Preserve same-day drafts and choices without prompt vault writes.
**Requirements:** R22/R23/R37/R38; KTD8; AE8/AE9/AE26/AE27.
**Dependencies:** None; integrates before U6.
**Files:** `app/runstate.py`, new `app/prompt_state.py`, `app/main.py`, `app/shadow.py`, new `app/tests/test_prompt_state.py`, `app/tests/test_shadow.py`, `app/tests/test_main_api.py`.
**Approach:**
1. Add atomic logical-day draft and undated preference stores using the state-directory seam.
2. Wire day-setup reading/writing to the new prompt surfaces without changing other runstate fields.
3. Remove prompt frontmatter manifest generation and characterize preservation of unrelated output.
**Test scenarios:**
- Covers AE27. Restart preserves same-day text; logical rollover clears text and preserves each opt-in.
- At 00:30 the prior logical-day draft remains available; civil-date export behavior belongs to U6.
- Covers AE8. Saving drafts/preferences performs no Todoist, calendar, or vault prompt write.
- Corrupt draft/preference input warns safely and does not copy another day's text.
- Non-prompt vault output, day-setup confirmation, and micro-adventure behavior remain unchanged.
**Verification:** Fixture-only state and manifest tests prove the lifecycle and no prompt-vault output.

### U6. Commit-only prompt exports and per-action recovery

**Goal:** Export the three optional prompts with durable task identity and honest retry behavior.
**Requirements:** R22/R36–R39; KTD8/KTD9; AE8/AE24–AE28.
**Dependencies:** U5; U4 for integrated commit authorization.
**Files:** `app/shadow.py`, `app/commit.py`, `app/commit_run.py`, `app/todoist_client.py`, `app/prompt_state.py`, `app/main.py`, new `app/tests/test_prompt_export.py`, `app/tests/test_commit.py`, `app/tests/test_shadow.py`.
**Approach:**
1. Extend manifest construction with independent prompt task actions from persisted server state.
2. Resolve Inbox/PHEP through existing project seams and persist per-action export receipts.
3. Integrate completed/deleted/uncertain results with existing partial-success orchestration and explicit re-export.
**Test scenarios:**
- Covers AE8. One opted-in prompt creates exactly one Inbox task at Commit, with no duration/calendar action; other export creation targets PHEP.
- At 00:30 Commit the task is due on the civil date while receipt identity remains the logical planning day.
- Covers AE24/AE25. Repeat Commit updates known ID; completed task stays complete; deleted task requires explicit re-export.
- Covers AE26. Opt-out and blank text leave existing tasks untouched.
- Covers AE28. Timeout after possible create records uncertainty, offers acknowledged immediate retry, and never claims guaranteed idempotency.
- Three exports with one failure persist two successes; resume retries only the failed action, including after restart.
- Forged draft/task ID or malformed receipt fails safely; unrelated existing Todoist tasks are not moved between projects.
**Verification:** Fake-provider integration demonstrates per-action receipts and zero unintended writer actions.

### U7. Page frame: stepper header, footer, and visual baseline

**Goal:** Replace the rail and tab-pill shell with the mockup's page frame, and set up the screenshot gate that every later UI unit uses.
**Requirements:** R20/R20b, R24 (blocked-reason display only); KTD10, KTD11, KTD13.
**Dependencies:** U1–U5 (landed). Lands first; U9–U13 build inside this frame.
**Files:** `frontend/src/ui/App.tsx`, `ScreenNav.tsx` (becomes the stepper header or is replaced by a new header component), `Rail.tsx` (retired once its content has a new home), `ActionDock.tsx`, `FooterBanners.tsx`, `ReadinessStrip.tsx`, `frontend/src/store/store.ts` (screen ↔ step state only), `frontend/src/app.css`, `frontend/src/fixtures/scenarios.ts` (new sparse/degraded scenario), affected UI tests (`compact-cockpit.test.tsx`, `a11y.test.tsx`, `responsive.test.ts`, `settings-shell.test.tsx`, `components.test.tsx`), reference renders under `docs/plan/2026-10-09-capacities-refresh-ui/reference/`.
**Approach:**
1. Render each mockup screen (A Set up day, B Plan tasks, C Connections) headless at 1500 and 900 widths into the reference directory. These are the KTD13 comparison baseline. Keep the operator's mockup file external.
2. Add a fixture scenario that matches the live data shape: stale artifact, vault off, no assigned rows, degraded sources, legacy intake. Make it selectable in the mockup build.
3. Build the header: date and frame line, three-step stepper bound to `ui.screen`, source-health pill with Retry wired to the existing refresh lifecycle, and a Connections link. Then build the single footer: back link, blocked reason, and primary action with its billed-call note, with no copy-prompt.
4. Move rail content to temporary homes that later units replace. Drop the rail column and the floating tab strip, then center the page in a bounded max-width column.
**Execution note:** Take screenshots before writing CSS, and again after each region lands.
**Test scenarios:**
- Each step activates its screen. Arrow, Home, and End keyboard navigation and visible focus still work. Commit step state reflects the current approval/commit phase and is never a separate screen.
- With degraded sources, the header pill shows the degraded state with Retry, and the footer shows the blocked reason without any "Copy prompt" text.
- With healthy sources and a sequenceable plan, the footer primary action is enabled and the blocked reason is absent.
- Opening and closing the settings drawer still makes the background inert and hidden while the drawer is open.
- At 900px the header wraps without hiding the stepper or the source pill, and the page has no horizontal scroll.
**Verification:** npm test, typecheck, both builds, and KTD13 screenshots for the ready and sparse scenarios. The header and footer match the reference composition, and the rail is gone.

### U9. Capacity headline card

**Goal:** Replace the rail capacity ledger and donut with the mockup's capacity card.
**Requirements:** R20b, R24c; KTD10, KTD13.
**Dependencies:** U7.
**Files:** new capacity-card component under `frontend/src/ui/`, `AllocationPie.tsx` (retired, or reduced to the Details view), `frontend/src/model/pie.ts` and `allocator.ts` (read-only reuse of segments), `app.css`, `calendar-capacity.test.tsx`, new card tests.
**Approach:** The headline states over-by or remaining from the existing allocator summary. One sentence gives the cause (fixed time versus selected tasks). One segmented bar shows calendar, work (Mint), anchors, habits, and tasks, with hatched overflow and a day-capacity marker, followed by a legend. "Details" discloses the full ledger the rail used to show. Dragging a slider still updates the bar in the same frame.
**Test scenarios:**
- An over-capacity day shows an over-by headline, the overflow segment, and a capacity marker. Commit is still allowed with a warning (R24c).
- An under-capacity day shows the remaining amount and no overflow.
- Unknown capacity (no reliable calendar data) says so explicitly and does not render as zero or overcapacity (KTD7).
- Dragging a task slider changes the tasks segment and the headline together.
- The legend and Details are reachable by keyboard, and Details exposes every number the old ledger showed.
**Verification:** Tests plus KTD13 screenshots, where the card region matches reference B in ready and sparse scenarios.

### U10. Already-placed timeline

**Goal:** Replace the Calendar Impact card list with the mockup's lane timeline.
**Requirements:** R20b; KTD10, KTD13.
**Dependencies:** U7.
**Files:** `CalendarImpact.tsx` (rebuilt as the timeline, or a new component that reuses its data), `BlockEditor.tsx` / `AnchoredEditor.tsx` (reused for edit actions), `app.css`, `calendar-capacity.test.tsx`, `feedback16.test.tsx`/`feedback18.test.tsx` where they pin the list layout, new timeline tests.
**Approach:** An hour axis spans the day frame. Calendar, Work, and Anchors lanes place blocks by start time and length, and the work window shows as a shaded zone. Selecting a block opens one inline detail row with the existing duration ± control, Exclude from plan, and close. The work-window toggle and the "tasks aren't shown here" note follow the mockup. The existing exclusion, duration override, and native-fixed rules stay as they are.
**Test scenarios:**
- Blocks render in the correct lane at proportional positions. Blocks partly outside the frame are clipped, and blocks wholly outside are omitted, with a count.
- Overlapping calendar blocks stack within the lane without covering each other's labels.
- Selecting a block with the keyboard (Tab or arrow, then Enter) opens its detail row, and Escape closes it and returns focus.
- Exclude from plan and duration ± change the capacity card and the sequencing inputs the same way the old list did.
- A day with zero calendar rows shows an empty-lane message, not a blank band.
**Verification:** Tests plus KTD13 screenshots. In the sparse scenario the timeline still reads as a timeline and does not collapse to a gap.

### U11. Fit-today task table

**Goal:** Re-compose the Today's work queue into the mockup's grouped, compact task table.
**Requirements:** R20b; KTD10, KTD12, KTD13.
**Dependencies:** U7.
**Files:** `Queue.tsx`, `frontend/src/store/allocatorView.ts` (view grouping only), `app.css`, `row-actions.test.tsx`, `components.test.tsx`, `duration-memory.test.tsx`, `feedback10.test.tsx`, `a11y.test.tsx`, `model/allocatorView.test.ts` where grouping is pinned.
**Approach:** Group rows as KTD12 maps them. Each row is one dense line: Assigned toggle, title with source tag and meta, priority and due chips, duration slider and value, and actions that appear on hover or focus. Actions are Mark complete, Exclude today, Exact duration, Leave as-is, and More. Right-edge notes show Protected · Allow move or warning counts. A hint row above the table lists the shortcuts with the today-only scope note. The frozen verb model and action IDs stay as they are; only labels and placement change.
**Test scenarios:**
- Critical rows group under Critical. High and other rows group under "When there's room" in their original order. Scheduled/native rows group under Fixed, and excluded or dropped rows under Out today.
- Hover and keyboard focus both reveal the row actions. Each action dispatches the same controller call as before, and undo still restores the row.
- Shortcuts C, E, D, ←/→, and ↑/↓ act on the focused row, and the hint row lists exactly the shortcuts that work.
- Out today collapses and expands, announces its state in words, and Include today returns a row to its group.
- A day with no assigned rows shows the quiet-day message inside the table frame and leaves no dead area.
**Verification:** Tests plus KTD13 screenshots. Row density and column alignment match reference B at 1500, and actions stay reachable at 900.

### U12. Set up day screen

**Goal:** Re-compose Day setup into the mockup's step-1 screen, including the drag strip that R20a requires.
**Requirements:** R20a, R22/R23 (opt-in display only); KTD10, KTD13.
**Dependencies:** U7.
**Files:** `DaySetupPanel.tsx` (split into frame, strip, anchors, prompts, and loose-end regions), `SetupDrawer.tsx`, `AnchoredEditor.tsx`, `frontend/src/model/mint.ts` and `anchored.ts` (read-only reuse), `app.css`, `panels.test.tsx`, `feedback_write_mint.test.ts`, new strip tests.
**Approach:** The day frame card holds preset, start, end, and buffer. Below it a strip spans the frame with Mint and Anchors lanes. Dragging on the Mint lane picks sessions, dragging an anchor moves it, and clicking an anchor edits its time and length, each with a keyboard equivalent. A summary line shows Mint sessions and free slots. The anchors list separates counted anchors from "Not counted today" (outside the frame or skipped). The prompts card shows opt-ins and save state from U5, with no copy-prompt. "Continue to tasks →" advances the stepper.
**Test scenarios:**
- Dragging across Mint slots selects those sessions. Slots blocked by Wall are not selectable, and the summary updates.
- The keyboard equivalent (focus a slot, Space to toggle, arrows to move) gives the same result as dragging.
- Moving an anchor outside the frame moves it to "Not counted today", and skipping does the same.
- Changing the preset or buffer updates the frame and Mint availability. "Reset to config" restores the defaults.
- Prompt opt-ins persist the same way U5 tests prove, and no prompt text appears outside the prompts card.
- Continue moves to Plan tasks and sets step 1 to done.
**Verification:** Tests plus KTD13 screenshots against reference A.

### U13. Connections screen

**Goal:** Merge the stacked Capacities and Tag-exclusion panels into the mockup's single Connections page.
**Requirements:** R20c, R26–R33; KTD5, KTD10, KTD13.
**Dependencies:** U7. The panels' behavior from U3 is already landed.
**Files:** `CapacitiesSettingsPanel.tsx`, `CapacitiesRulesEditor.tsx`, `CapacitiesSourceEditor.tsx`, `CapacitiesUnknownReview.tsx`, `CapacitiesRefreshPanel.tsx`, `TagExclusionSettingsPanel.tsx`, `App.tsx` (connections body), `app.css`, `capacities-settings.test.tsx`, `capacities-source-editor.test.tsx`, `tag-exclusion-settings.test.tsx`, `settings-shell.test.tsx`.
**Approach:** One page in mockup order: back link and sync pill, then a structures table, the admit-when rule list, property mapping with Discover, and the tag-exclusion card with the stale-tag notice. An admission preview follows, and a sticky unsaved-changes bar holds Save and Discard. The existing draft revision, validation, and save/discard logic is reused unchanged.
**Test scenarios:**
- Editing a rule, a mapping, and the tag list makes the unsaved bar count three changes. Discard restores all three, and Save persists all three under one revision.
- A stale saved tag shows the notice and can be removed.
- The preview shows admitted and left-out counts with a reason for each item, and updates as the draft changes.
- An invalid rule cannot be activated and the error says why (R33).
- Back to planning returns to Plan tasks without losing unsaved Connections edits until the operator saves or discards.
**Verification:** Tests plus KTD13 screenshots against reference C.

### U8. Integrated evidence, rollout, and restartable landing

**Goal:** Prove the full outcome and prepare a reversible, attended cutover.
**Requirements:** R1–R39 and AE1–AE28; all KTDs.
**Dependencies:** U1–U7, U9–U13. U6 is still unbuilt. Its UI surfaces (re-export and uncertain-retry acknowledgment) land with U6, inside the U7 footer and the U12 prompts card.
**Files:** Existing integration tests, new `app/tests/test_capacities_refresh_end_to_end.py`, frontend fixture walkthrough coverage, scoped acceptance evidence under `docs/plan/2026-10-09-capacities-refresh-ui/`, affected current operator docs.
**Approach:**
1. Run an end-to-end fake-provider walkthrough through restart, rule save, Refresh/Rescan, selection, sequence, shadow, and partial export recovery.
2. Record measured warm-refresh behavior, baseline failures, built-bundle read-back, and independent implementation review.
3. Prepare provider-specific rollback without touching the existing artifact producer; root handles path-scoped landing and separately approved deployment.
**Test scenarios:**
- A mixed-provider planning day survives refresh cancellation/restart and Commit recovery with stable identities.
- Rollback uses the explicitly selected old provider intake and does not mix new/old Capacities rows.
- Large paginated cold scope, storage failure, and missing credentials never produce a truncated success.
- Trace each source acceptance example to obtained deterministic evidence; no source writes or billed calls occur in scratch verification.
**Verification:** The Verification Contract passes or names a blocking baseline issue; integration diff contains only intended work.

---

## Verification Contract

**Planning evidence:** current source inspection and the unchanged approved contract. No implementation tests, builds, service probes, or live source calls were run to generate this proposal.

**Implementation gates:**

| Gate | Applies | Evidence required |
| --- | --- | --- |
| Execution-context and ownership guard | Before each writable slice and review/landing | Correct linked worktree/branch/pinned HEAD and owned-path hashes; no foreign changes overwritten |
| TDD | U1–U7, U9–U13 behavior | Observed failing scenario before implementation, passing scenario after, focused regression evidence |
| Backend suite | All units | Repository Python environment runs backend pytest, including bootstrap runner tests |
| Frontend suite/typecheck | U4, U7, U9–U13, U8 | `npm test` and `npm run typecheck` from `frontend/` |
| Visual acceptance (KTD13) | U7, U9–U13, U8 | Fixture screenshots at 1500×1000 and 900×1000 for the ready and sparse scenarios, compared region by region with the matching reference render. Every listed region is present, nothing overlaps, clips, or leaves a dead area, and there is no horizontal scroll. The screenshots are attached to the unit report. |
| Generated builds | U7, U9–U13, U8 | `npm run build:mockup` and `npm run build:prod`, followed by bundle-reference/content read-back |
| Scratch walkthrough | U8 | Fixture/mocked `:8790` walkthrough, zero billed calls, zero real-source writes |
| Independent review and diff | Before integration | Accepted review, owned-path `git diff --check`, intended diff and fresh Git/worktree evidence |

Use the repository's discovered Python runner for backend pytest rather than assuming the shell interpreter has dependencies. Resolve any externally hosted mandatory policy checks through an explicitly approved boundary before execution; never claim them run from local static evidence. Planning cannot establish runtime warm latency. After implementation, measure ordinary refresh with representative cached scope and report the approximately two-minute target honestly.

Live `:8746` is never a test fixture. No live `POST /commit`, `/sequence`, or `/adjust` is authorized by this plan. A service restart, machine-local intake/config cutover, credential access, or real-provider writes needs its own attended approval. `/version` reflects Git HEAD and cannot alone prove newly loaded backend code; approved deployment needs behavioral GET-only evidence.

---

## Definition of Done

Each unit has observed test-first evidence, passing focused scenarios, and independent acceptance before landing. The integrated cockpit delivers the source Product Contract, including explicit warning review rather than partial publication, per-type cache freshness, safe selection and commit authorization, and prompt recovery.

Each UI unit (U7, U9–U13) is done only when its KTD13 screenshots match the reference composition, not when its tests alone pass. After an attended `restart-live.sh`, a GET-only screenshot of live `:8746` shows the new composition. A sparse live day is data state, not a layout failure, and must not be hidden with CSS. The required suites/builds and fixture walkthrough support the actual outcome. Generated artifacts are read back, no abandoned-attempt code remains, no unrelated work is altered, no secrets/personal drafts enter repository evidence, and all baseline failures or unrun gates are disclosed. Root-approved execution state and TDTB acceptance evidence link to the actual landed commits. Until separately approved deployment and cutover evidence exists, report implementation as locally delivered, not live.

---

## Appendix

**Research anchors:** `app/capacities_builder.py` (`_ContentCache`, `SourceRecord`); `app/capacities_adapter.py` (`_list_objects`, `_hydrate_object`, `_project_object`); `app/producer_rules.py` recursive predicates and coverage cursor; `app/artifact_source.py` never-silent-fallback consumption; `app/main.py` `post_commit` and `_validate_commit_eligibility`; `app/shadow.py` capture/B6 manifest; `app/commit.py` task/vault writers; frontend controller/wire/store/refresh lifecycle.

**Prior lessons:** `docs/plan/2026-10-08-capacities-first-migration/plan.md` provides logical-day, calendar-seam, candidate-routing, and request-budget failure evidence. `docs/plan/2026-10-07-tdtb-cockpit-ui-round2/plan.md` provides bundle provenance and prompt re-homing evidence. Neither historical plan overrides the approved Product Contract.

**External contract:** Capacities Developer API OpenAPI facts recorded in the Product Contract: paginated type summaries omit object properties; content reads cost provider requests. Verify current endpoint limits during implementation using public documentation without credentials before choosing exact pacing ceilings.

**Review routing:** Automatic `ce-doc-review` is suppressed by the PIOS adapter, not a passed review. Root review checks preservation, grounded seams, gate safety, and unit readiness; any unresolved material finding blocks launch. The approved mockup files remain external inputs until portable evidence is deliberately materialized without adding illustrative data as requirements.
