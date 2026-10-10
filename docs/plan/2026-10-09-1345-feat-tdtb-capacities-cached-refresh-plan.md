---
title: TDTB Capacities Cached Refresh - Plan
type: feat
date: 2026-10-09
topic: tdtb-capacities-cached-refresh
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-brainstorm
execution: code
---

# TDTB Capacities Cached Refresh - Plan

## Goal Capsule

**Objective.** A cockpit Refresh keeps the day's Capacities-derived planning
rows complete and trustworthy. It enumerates every object under the operator's
configured Capacities types, finishes pagination, reads only new or uncached
object content, re-applies the operator's editable inclusion rules locally, and
publishes a completed scoped result — or preserves the previous complete result
with a visible warning when a read is incomplete. Freshness and read coverage
stay visible, and no result is ever silently truncated.

**Means.** Standalone TDTB reads Capacities directly over the REST Developer
API and keeps a persistent, machine-local per-object property cache. The
existing consumer, rule evaluator, and caching seams are reused where adequate
rather than rewritten (KD4).

**Product authority.** This is a requirements-only artifact; it records
approved user decisions and carries no implementation steps or process
topology. UI scope is captured as product behavior against an approved mockup
baseline (R20), not as implementation or final visual design. The user approved
the standalone direct-REST-plus-cache direction and rejected the background
deterministic bridge and the agent/MCP bridge as the read path (KD1). No new
autonomous schedule or Capacities write behavior is approved by this capture
(R19); existing unrelated behavior is not retired here.

**Context, not authority.** The migration plan at
`docs/plan/2026-10-08-capacities-first-migration/plan.md` is historical context.
Only its conflicting forward read direction — the agent/MCP artifact pivot — is
superseded for the chosen direction; its other recorded behavior is not
blanket-invalidated. It is not current authority and is not modified by this
document. The supplied planning mockups are an approved layout-and-behavior
baseline, subordinate to the explicit user decisions recorded here.

## Product Contract

### Problem Frame

TDTB's Capacities-derived rows are read under a hard provider rate limit and a
bounded read budget, so a single refresh can defer objects and report a partial
result. The existing seams already do bounded pagination, local eligibility
evaluation, and content caching, but they were built around a producer/agent
artifact seam rather than the standalone app. The user has now approved a
standalone direction: the app reads Capacities directly, completes its scope,
and serves results from a persistent cache so that ordinary refreshes are fast
and cheap while remaining honest about what was actually read.

The day-planning surface is also in scope. Three screens — Set up day, Plan
tasks (replacing Today's work), and Connections (replacing the Capacities and
Tag-exclusions tabs) — are captured here as product behavior against an approved
mockup baseline. The mockups are layout-and-behavior evidence; explicit user
decisions override them, notably on commit gating (R24) and prompt export (R22,
R23). Mock names, example numbers, and billing counters are illustrative.

### Requirements

**Data acquisition and cache**

- R1. **Direct REST reads.** Standalone TDTB reads Capacities directly over the
  REST Developer API. No agent or MCP bridge sits in the read path.
- R2. **Persistent object-property cache.** Property content read from
  Capacities is persisted machine-locally, keyed to object identity, so later
  refreshes reuse cached properties instead of re-reading them.
- R3. **Discover all objects across configured types.** A Refresh enumerates
  every object under each operator-configured Capacities type, not a bounded
  subset.
- R4. **Complete pagination.** Enumeration follows pagination to the end of each
  configured type. A listing that cannot be completed is an incomplete result,
  never a truncated success.
- R5. **Fetch uncached and new objects.** A Refresh reads content for objects
  that are new or absent from the cache; cached objects are not re-read by an
  ordinary Refresh.
- R6. **Complete scope or nothing published.** Refresh publishes a scoped result
  only when the read for that scope is complete. A refresh with no complete
  Capacities result never publishes a partial Capacities slice; the run proceeds
  in the clearly-labeled no-Capacities mode (R24b), which is graceful
  degradation rather than partial success.
- R7. **Visible progress.** Refresh shows progress while it runs.

**Inclusion rules and evaluation**

- R8. **Local, editable inclusion predicates.** Inclusion is re-evaluated
  locally from the operator's editable rules. The predicate vocabulary stays in
  that editable rules source; no rule edit passes through an agent or a code
  change.
- R9. **Rule changes take effect without object reads.** Editing a rule changes
  the published result without re-reading object content, whenever cached
  properties already carry the fields the rule reads.

**Rescan**

- R10. **Rescan scope.** A rescan covers one configured type or all configured
  types, at the operator's choice.
- R11. **Rescan rereads cached properties.** A rescan re-reads content for the
  selected scope even when those objects are already cached.
- R12. **Explicit rescan for existing property edits.** An edit to an existing
  object's properties in Capacities becomes visible only after an explicit
  rescan. Caching is acceptable because the operator sets objects up once; the
  ordinary Refresh does not have to detect such edits.

**Failure, freshness, and removal**

- R13. **Preserve the previous complete result on failure.** On a failed or
  incomplete read, the previous complete result remains in place.
- R14. **Warn, never silently truncate.** An incomplete or failed refresh raises
  a visible warning. Results are never silently truncated or partially
  published.
- R15. **Remove disappeared objects only after a complete listing.** A cached
  object is removed only after a successful, complete listing of its type no
  longer returns it.
- R16. **Freshness and coverage visible.** The cockpit keeps last-check
  freshness visible, and distinguishes cached-complete coverage from freshly
  reread values.

**Target and boundaries**

- R17. **Ordinary refresh target.** An ordinary (warm, cached) refresh targets
  roughly two minutes. This is an approximate target, not a guarantee.
- R18. **Initial import and forced rescans may exceed the target.** The first
  import and forced rescans may run longer than the ordinary target.
- R19. **Read-side scope only.** This work adds neither an autonomous refresh
  schedule nor Capacities writes. It does not disable existing automatic refresh
  triggers or existing write behavior; changing those needs separate scope.

**Reuse**

- R21. **Reuse existing seams.** There is no mandate for a wholesale rewrite.
  Reuse the current consumer, rules, and caching seams where they are adequate,
  including `app/capacities_builder.py`, `app/producer_rules.py`,
  `app/artifact_source.py`, and `frontend/src/store/controller.ts`.

**Planning UI (integrated scope)**

- R20. **Integrated planning UI scope.** The three-surface morning flow is in
  scope as product behavior, not as final visual design: Set up day, Plan tasks
  (replacing Today's work), and Connections (replacing the Capacities and
  Tag-exclusions tabs). The supplied mockups are the approved layout-and-behavior
  baseline; explicit user decisions override the mockups where they differ.
  Mockup names, example numbers, and billing counters are illustrative and not
  mandated.
  - R20a. **Set up day.** Day frame (preset, start, end, buffer); Mint and anchor
    strips with drag interaction; anchors counted inside the frame versus omitted
    as outside-frame or skipped; morning prompts and the micro-adventure prompt.
  - R20b. **Plan tasks.** A capacity budget with an already-placed commitments
    and anchors timeline; lanes for Critical, When there's room, Fixed, and Out
    today; per-row assignment, duration, exclude/undo/complete/reorder; sequence
    & commit; and the existing copy-prompt action.
  - R20c. **Connections.** Selection of configured Capacities types; flexible,
    editable inclusion rules (not only fixed switches); property mappings with
    discovery; independent native/active/assigned settings where compatible; tag
    exclusions with a stale-tag notice; an admission-reason preview; and
    save/discard.

**Morning prompts**

- R22. **Prompt export as Todoist tasks, at Commit only.** Each morning prompt
  (Intention, For Meegy, Stoic) is an independent, optional export as a Todoist
  *task*. The operator opts in per prompt. Tasks are created only during Commit;
  prompts are never written to a daily note or the vault. A prompt-response
  export goes to the Todoist **Inbox**; other Todoist task exports go to
  **PHEP**. Calendar routing and other existing source-completion behavior are
  unchanged, and non-Todoist outputs are not broadly rerouted. Inbox and PHEP
  are intended project names; resolving their concrete IDs is an implementation
  concern. Prompt-response tasks are due on the current date at Commit.
- R23. **Opt-in persists; prompt text does not carry forward.** A per-prompt
  opt-in setting survives TDTB restarts, while the prompt text entered for a day
  does not carry into the next planning day. Same-day text does survive a
  restart within the same planning day (R38).

**Commit gating and source availability**

- R24. **Source-availability and capacity gating.** Commit is blocked only by a
  genuine safety or validation failure, not by degraded source visibility or a
  full day. Specifically:
  - R24a. **Failed refresh with a last complete cached result.** When a refresh
    fails but a last complete cached result exists, commit is allowed with an
    explicit cache-staleness warning.
  - R24b. **No complete Capacities result.** When no complete Capacities result
    exists at all, planning and commit proceed in an explicit no-Capacities mode
    that omits Capacities rows and warns. This is graceful degradation, not a
    partial Capacities success (see R6).
  - R24c. **Overcapacity.** When planned load exceeds day capacity, commit is
    allowed with a visible overcapacity warning.
- R25. **Relaxations are bounded.** The R24 relaxations cover source availability
  and overcapacity only. They do not weaken validation, safety, or integrity
  protections and do not change the refresh/rescan requirements of R1–R21.

**Eligibility, mappings, and exclusions**

- R26. **Per-type nested editable rule builder with AND/OR/NOT.** Each
  configured Capacities type has an editable nested inclusion rule whose
  property comparisons combine with AND, OR, and NOT. Eligibility is fully
  determined by that per-type editable configuration; there is no separate or
  hidden "active pull" eligibility mechanism, and Active/Started vocabulary is
  expressed as ordinary property comparisons in the rule.
- R27. **Missing mappings never block admission and the schema is never
  guessed.** Missing Duration, Assignment, or Completion property mappings do
  not by themselves block admission when eligibility can still be determined.
  Per type, the operator can edit an estimated fallback duration used when no
  Duration mapping exists. When Assignment is unmapped, assignment is manual.
  When Completion is unmapped, source completion is unavailable and the app
  must not infer or guess a completion schema.
- R28. **Unassigned candidates require deliberate selection.** A candidate that
  is not assigned counts toward neither the capacity budget nor commit until the
  operator deliberately selects it.
- R29. **Hard exclusions override inclusion; unknown completion is visible.**
  Global tag exclusions override rule inclusion. Objects mapped to a completed
  or dropped state are always excluded. An object whose completion state cannot
  be determined is surfaced visibly as unknown, never treated as complete or
  eligible.
- R30. **Rule changes and type removal preserve selections, except hard
  exclusions.** When a rule changes or a configured type is removed, items
  already selected are kept with a warning. The hard exclusions of R29 — an
  excluded tag, a completed state, or a dropped state — win: those items are
  removed from schedulable selections with a visible notice.

**Rule validity, unknown results, and cancellation**

- R31. **Save a valid rule with insufficient cache; mark unknowns and offer
  Rescan.** A new rule that is valid is saved even when cached properties are
  insufficient to evaluate it fully. Objects that cannot be fully evaluated are
  marked unknown and a Rescan is offered. Such objects may appear as *warning
  candidates* explicitly flagged as not fully evaluated; they are never
  presented as a completed eligibility evaluation.
- R32. **Explicit selection permits commit without rescan.** The operator may
  explicitly select a warning candidate and commit without a rescan, using its
  safe mapped values or fallbacks. The app must not claim that the refresh fully
  evaluated that object.
- R33. **Invalid rules stay draft; invalid activation is prevented.** A rule
  that references a removed property, or expresses an incompatible comparison,
  remains a draft; the previously valid rules stay active, and activating the
  invalid rule is prevented.
- R34. **Cancellation preserves the previous complete result and successful
  reads.** Cancelling a refresh or rescan keeps the previous complete published
  result in place and retains the reads that succeeded for later attempts. The
  cancelled state clears; no partial result is published.
- R35. **Complete-results rule, warning-candidate review, and no-Capacities mode
  are reconciled.** The ordinary rule publishes only a complete scoped result
  (R6). Not-fully-evaluated objects are exposed through a supplementary
  warning-candidate review surface, not as a partial success. When no complete
  Capacities result exists, the run proceeds in the graceful no-Capacities mode
  (R24b). Neither path silently claims partial success.

**Morning prompt export lifecycle**

- R36. **Prompt export is task-only, and repeat Commit updates the same task.**
  A prompt-response export creates a Todoist task only: it carries no Duration
  and no Calendar routing. A repeated Commit on the same day updates the same
  prompt/day task rather than creating another. If that task is already
  completed it is left untouched, with a warning when the incoming text differs
  from the completed task's text. A deleted task is not recreated
  automatically; recreating it requires an explicit re-export action. Opting a
  prompt out leaves any existing task untouched and prevents future export or
  update for that prompt.
- R37. **Blank response skips export.** A blank prompt response skips export and
  leaves any existing task untouched.
- R38. **Same-day drafts survive restarts; next day clears; opt-ins persist.** A
  same-day prompt draft survives TDTB restarts. The next planning day clears the
  prompt text while per-prompt opt-ins persist.
- R39. **Export retry, partial success, and no false idempotency.** An export
  response that is uncertain is retried immediately at the operator's explicit
  choice, accepting the possibility of duplicate task creation; the app reports
  that risk accurately and never claims guaranteed idempotency. Known successful
  exports are not replayed. After a partially failed export the UI shows partial
  success and retries only the failed actions.

### Key Decisions

- **KD1. Standalone direct REST plus persistent cache, chosen over both
  bridges.** (session-settled: user-approved — chosen over the background
  deterministic bridge and the agent/MCP bridge: those share the same REST cost
  limits while adding operational surface.) Governs R1, R2, R5.
- **KD2. Publish only complete scoped results, preserving the previous complete
  result on failure.** (session-settled: user-approved — chosen over
  incremental or truncated publication: an incomplete pool must never look
  complete.) Governs R4, R6, R13, R14.
- **KD3. Caching is acceptable; explicit rescan covers property edits.**
  (session-settled: user-approved — the operator configures objects once, so
  ordinary refreshes need not detect existing-property edits.) Governs R12,
  R11.
- **KD4. Reuse existing seams; no wholesale rewrite.** (session-settled:
  user-approved — reuse the current consumer, rule evaluator, and cache where
  adequate.) Governs R21.
- **KD5. Commit degrades gracefully instead of blocking on source visibility or
  overcapacity.** (session-settled: user-approved — a failed refresh with a last
  complete cached result, an entirely absent Capacities result, and overcapacity
  each warn and still allow commit; only genuine safety or validation failures
  block. This replaces the mockup's blanket "degraded → commit blocked" rule.)
  Governs R24, R25.
- **KD6. Prompts are optional Todoist task exports, not daily-note content.**
  (session-settled: user-approved — each prompt opts in independently, the
  opt-in survives restarts, the text does not carry to the next day, and tasks
  are created only at Commit with no daily-note or vault write. Prompt responses
  route to the Todoist Inbox and other Todoist exports to PHEP, per R22.)
  Governs R22, R23.
- **KD7. The three-screen planning flow is integrated scope, not deferred.**
  (session-settled: user-approved — Set up day, Plan tasks, and Connections are
  captured as product behavior against the approved mockup baseline; illustrative
  mock detail is not mandated.) Governs R20.
- **KD8. Prompt export lifecycle is settled.** (session-settled: user-approved —
  prompt-response exports are task-only with no duration and no Calendar routing
  and are due the current date at Commit; a repeat Commit updates the same
  prompt/day task; a completed task is left untouched with a text-difference
  warning; a deleted task is never recreated automatically and needs an explicit
  re-export action; opting out leaves the existing task untouched and stops
  future export/update; a blank response skips export; an uncertain response is
  retried immediately accepting duplicate risk with no idempotency claim; known
  successes never replay and a partial failure retries only the failed actions.)
  Governs R22, R23, R36–R39.
- **KD9. Eligibility is expressed entirely by per-type editable rules.**
  (session-settled: user-approved — the per-type rule is a nested AND/OR/NOT
  property-comparison builder; there is no hidden "active pull" eligibility
  separate from the type configuration, and Active/Started is expressed as an
  ordinary property comparison.) Governs R8, R20c, R26.
- **KD10. Missing mappings never block admission; fallbacks are explicit and the
  schema is never guessed.** (session-settled: user-approved — missing Duration,
  Assignment, or Completion mappings do not block admission when eligibility is
  determinable; a per-type editable estimated duration, manual assignment, and
  unavailable source completion are used instead, and no completion schema is
  inferred.) Governs R27.
- **KD11. Hard exclusions win; other changes preserve selections.**
  (session-settled: user-approved — global tag exclusions and mapped
  completed/dropped states always exclude; a rule change or type removal keeps
  already-selected items with a warning except where a hard exclusion applies,
  which removes them from schedulable selections with a notice.) Governs R29,
  R30.
- **KD12. Incomplete evaluation yields warning candidates, not partial success.**
  (session-settled: user-approved — a valid rule saves with unknowns marked and
  a Rescan offered; explicit selection permits commit without a rescan using safe
  values; an invalid rule stays draft with prior valid rules active; cancellation
  keeps the previous complete result and successful reads; the complete-results
  rule, warning-candidate review, and no-Capacities mode are reconciled with no
  partial-success claim.) Governs R31–R35.

### Acceptance Examples

- AE1. **Cached refresh.** All objects of the configured types are cached and
  rules are unchanged; Refresh completes from the cache, spends no content
  reads, and publishes a complete scoped result. Coverage reads as
  cached-complete; listing-check time advances, but cached property-read times
  do not. (Covers R2, R5, R6, R16.)
- AE2. **New object.** A new object appears in a configured type; Refresh reads
  only that object's content, admits it if the rules qualify it, and publishes a
  complete result. (Covers R3, R5.)
- AE3. **Changed rule without object reads.** The operator edits a rule so an
  already-cached object now qualifies; the next Refresh republishes with the
  object included while spending zero content reads. (Covers R8, R9.)
- AE4. **Rescan for a property edit.** The operator edits a property on an
  existing object in Capacities; the change appears only after an explicit
  rescan (one type or all), which re-reads the cached properties. An ordinary
  Refresh would have kept the cached value. (Covers R10, R11, R12.)
- AE5. **Pagination failure.** A type listing fails midway; Refresh keeps the
  previous complete result and shows a warning. Nothing is silently dropped or
  truncated. (Covers R4, R13, R14.)
- AE6. **Removal.** An object is deleted in Capacities and a subsequent listing
  of its type completes without it; the cached object is removed from results
  only after that successful complete listing. (Covers R15.)
- AE7. **First import over target.** On an empty cache the first import may
  exceed the ordinary target; it still shows progress and publishes only a
  complete scoped result. An incomplete first import preserves prior state with
  a warning. (Covers R6, R7, R17, R18.)
- AE8. **Prompt export opt-in and commit-only.** The operator opts a single
  morning prompt into Todoist export; that opt-in survives a TDTB restart. A
  Todoist task is created for that prompt only during Commit, with a
  prompt-response going to the Inbox and other Todoist exports going to PHEP,
  and no prompt text is written to a daily note or the vault. (Covers R22,
  R23.)
- AE9. **Prompt daily reset.** Prompt text entered for one planning day is absent
  from the next planning day even though a per-prompt opt-in persisted; only the
  opt-in carries forward, not the text. (Covers R23.)
- AE10. **Cache-failure commit.** A refresh fails but a last complete cached
  result exists; planning continues and commit succeeds with an explicit
  cache-staleness warning. (Covers R24a.)
- AE11. **No-Capacities commit.** No complete Capacities result is available;
  planning and commit proceed with Capacities rows omitted and a visible
  no-Capacities warning, without publishing a partial Capacities slice.
  (Covers R6, R24b.)
- AE12. **Overcapacity commit.** Planned tasks exceed day capacity; the budget
  shows the overage and commit remains allowed with a warning. (Covers R24c,
  R25.)
- AE13. **Connections rule editing.** The operator edits an inclusion rule, maps
  or discovers properties, adds or removes a tag exclusion (with any stale-tag
  notice surfaced), reviews the admission-reason preview, and saves or discards;
  the published admission result follows the saved rule without re-reading object
  content. (Covers R8, R9, R20c.)
- AE14. **Nested rule builder.** The operator writes a per-type rule such as
  `(Status = Active) AND NOT (Tag = archived)`, combining property comparisons
  with AND/OR/NOT; Active eligibility follows that rule with no separate
  active-pull setting. (Covers R8, R26.)
- AE15. **Missing mappings.** A configured type has no Duration or Assignment
  mapping; its eligible objects still admit, using the type's editable estimated
  fallback duration and manual assignment, and completion reads as unavailable
  rather than guessed. (Covers R27.)
- AE16. **Unassigned candidate.** An admitted candidate has no assignment; it
  neither counts toward capacity nor commits until the operator deliberately
  selects it. (Covers R28.)
- AE17. **Hard exclusion overrides inclusion.** A rule would include an object,
  but a global tag exclusion matches it, so it is excluded; an object mapped to a
  completed or dropped state is excluded; an object with an undetermined
  completion state is shown as unknown, not eligible. (Covers R29.)
- AE18. **Rule change / type removal retention.** After a rule edit or removal
  of a configured type, already-selected items remain with a warning, except
  items that hit a hard exclusion (excluded tag, completed, or dropped), which
  are removed from schedulable selections with a notice. (Covers R30.)
- AE19. **Valid rule with insufficient cache.** The operator saves a new valid
  rule while cached properties are missing; the rule saves, the under-evaluated
  objects appear as warning candidates flagged not-fully-evaluated, and a Rescan
  is offered. (Covers R31.)
- AE20. **Explicit commit without rescan.** Without rescanning, the operator
  explicitly selects a warning candidate; commit proceeds with safe mapped values
  or fallbacks and the UI does not claim the object was fully evaluated. (Covers
  R32.)
- AE21. **Invalid rule stays draft.** A rule references a removed property or
  uses an incompatible comparison; it remains a draft, the previous valid rules
  stay active, and the invalid rule cannot be activated. (Covers R33.)
- AE22. **Cancelled refresh/rescan.** The operator cancels mid-run; the previous
  complete published result stays, the reads that succeeded are retained for the
  next attempt, and the cancelled state clears with no partial publication.
  (Covers R34.)
- AE23. **Warning-candidate review vs. no-Capacities mode.** An ordinary run
  publishes a complete scoped result and lists not-fully-evaluated objects as
  warning candidates in a supplementary review surface; with no complete
  Capacities result the run shows no-Capacities mode. Neither is presented as a
  partial success. (Covers R6, R24b, R35.)
- AE24. **Repeat Commit updates the same prompt task.** The operator Commits
  twice in a day with the same prompt; the second Commit updates the same task
  instead of creating a duplicate. If the task is already completed and the text
  changed, it is left untouched with a warning. (Covers R22, R36.)
- AE25. **Deleted prompt task.** A prompt task is deleted in Todoist; a later
  Commit does not recreate it automatically, and recreation happens only through
  an explicit re-export action. (Covers R36.)
- AE26. **Prompt opt-out and blank response.** Opting a prompt out leaves its
  existing task untouched and stops future export/update; a blank response skips
  export and leaves the existing task untouched. (Covers R36, R37.)
- AE27. **Same-day draft survivor.** A same-day prompt draft survives a restart;
  the next planning day the text is cleared while the per-prompt opt-in persists.
  (Covers R23, R38.)
- AE28. **Uncertain export retry.** An export response is uncertain; the app
  offers an immediate retry and states the possible duplicate-creation risk
  without claiming guaranteed idempotency. Known successful exports are not
  replayed, and after a partial failure only the failed actions retry. (Covers
  R39.)

### Success Criteria

- An ordinary warm refresh completes within roughly the two-minute target often
  enough to be an operational norm, observed rather than asserted as a
  guarantee (R17).
- A complete scoped result is published after a full enumeration of every
  configured type, with no silent truncation (R4, R6, R14).
- Commit proceeds, with an explicit warning, when Capacities is unavailable or
  the day is over capacity; only genuine safety or validation failures block
  commit (R24, R25).

### Scope Boundaries

- No new Capacities writes or autonomous refresh schedule in this work.
- Existing automatic triggers and write paths are not removed by this plan.
- No new agent, MCP, or background deterministic bridge in the read path.
- The three-screen planning flow (Set up day, Plan tasks, Connections) is in
  scope as product behavior against the approved mockup baseline; mock detail is
  not mandated and this is not a full visual redesign.
- The commit relaxations in R24 are limited to source availability and
  overcapacity; validation, safety, and integrity protections are unchanged.
- No wholesale rewrite; existing consumer, rules, and cache seams are reused.
- The historical migration plan at
  `docs/plan/2026-10-08-capacities-first-migration/plan.md` is untouched and is
  not authority for this direction.

### Open Questions

Open implementation questions that remain; they are deferred and do not block
admission of the product contract, and none invents implementation steps or
mandates mock detail.

- Cache schema and versioning: how object identity keys the persisted entries
  and how property shapes are stored.
- Cache bounds and eviction, and how cached-complete coverage is computed and
  reported distinctly from freshly reread values.
- Pacing strategy for finishing pagination within the provider's 30-requests /
  60-seconds limit.
- How per-type completeness and rescan progress are tracked and surfaced.
- How the existing content cache in `app/capacities_builder.py` maps onto the
  standalone scope without behavior or data loss.
- Whether the current predicate vocabulary in `app/producer_rules.py` fully
  covers the operator's editing needs or requires extension.

### Sources

- **Official provider facts.** `https://developers.capacities.io/openapi.json`:
  `GET /object` is subject to a 30-requests / 60-seconds limit, and
  `/objects/structure` summaries carry only `id`, `structureId`, and `title`,
  paged at 100 per page. Do not claim the absence of any bulk or property-list
  capability without independent verification.
- **Existing code evidence.** `app/capacities_builder.py` (TTL content cache,
  300s, plus a durable content cache), `app/producer_rules.py` (recursive
  editable predicates), `app/artifact_source.py` (artifact consumer),
  `frontend/src/store/controller.ts` (`refreshSources`).
- **Approved UI baseline (layout and behavior).**
  `/Users/walle-mini/Downloads/Planning Mockups.dc.html`, plus captured
  screenshots
  `/Users/walle-mini/Library/Application Support/CleanShot/media/media_04I5zlPQLi/capture-005345_2026-10-09.png`,
  `/Users/walle-mini/Library/Application Support/CleanShot/media/media_dLRjWXlrY8/capture-005346_2026-10-09.png`,
  and
  `/Users/walle-mini/Library/Application Support/CleanShot/media/media_7svWD2sJUY/capture-005347_2026-10-09.png`.
  Baseline for R20; explicit user decisions override it.
- **Context, not authority.**
  `docs/plan/2026-10-08-capacities-first-migration/plan.md` — only its
  conflicting forward read direction (the artifact/agent-MCP producer pivot) is
  superseded; its other recorded behavior is not blanket-invalidated. Retained
  as historical context only.
