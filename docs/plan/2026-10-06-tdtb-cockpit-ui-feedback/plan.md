# TDTB cockpit UI feedback — approved slice plan

Status: **approved**. All five slices, built in order. S4 uses the
**pinned-status / quieter-pie** option. Branch `TDTB/ui-feedback`.

Source: six annotated items from a Plannotator session against the live
cockpit (`http://127.0.0.1:8746/static/cockpit/`), with the operator's own
clarifications recorded below.

## Operator feedback as clarified

1. **Row action set is confusing** (`.qrow__actions`). Operator confirmed this
   and item 5 are **one issue**: the whole action set, icons and text verbs
   together.
2. **Drop the "would drop" concept** (`span.qrow__drop`). They either keep an
   item scheduled or remove/exclude/delete/complete it; automatic dropping is
   noise and the caution gets accepted every time anyway.
3. **Calendar impact should be inline with the other items**
   (`section.calendar-impact`), not a separate-feeling section.
4. **The rail's capacity feedback doesn't earn its space.** Operator's binding
   correction: the pie and time-remaining indicators are not worthless in
   principle — in their current state they "don't offer any information that's
   useful while actually scheduling besides the flag or warning whenever I'm
   over allotted time". So this is **re-presentation, not removal**.
5. Merged into item 1.
6. **Copy prompt should copy everything needed** — a request, not a
   confirmation.

## Exclude-label finding (resolved)

`Queue.tsx:573` builds the accessible name as *verb + verbatim item name +
"today"*. `Tooltip.tsx:34-58` adds `aria-describedby` only — no status suffix,
no replacement name. `adapters/wire.ts:538-539` preserves `row.name`, and the
Capacities reader uses the source title. Neither checked-in bundle contains
the literal `Upper Fast` or `Active today`.

Therefore `item.name = "Upper Fast — Active"` reproduces the annotation
exactly: the row's name simply **is** `Upper Fast — Active`. Not a stale
deployment.

**Do not strip "Active" from names** — names participate in sequence identity.

## Common execution and verification

Dedicated `TDTB/` branch/worktree; `main` stays integration-only. Preserve the
four foreign dirty paths. TDD: demonstrate the failing assertion first, then
implement.

Every slice: its focused suites, then full `npm test`, `npm run typecheck`, and
the repo-root backend pytest including bootstrap tests. **Both bundles must be
rebuilt and read back** — `npm run build:mockup` → `mockups/cockpit/`,
`npm run build:prod` → `app/static/cockpit/`. Verify with fixtures/mocks on
scratch `:8790` across keyboard, narrow viewport, short viewport, and both
themes. Prove zero billed calls and zero real source writes. Independent review
and owned-path diff inspection before integration.

Historical evidence is immutable: approved replacements need **new** acceptance
evidence, never retroactive edits to FEEDBACK-07/10/13/17.

## S1 — Complete the external scheduling handoff (item 6)

**Objective:** make Copy prompt self-contained enough to schedule safely.

**Change** (`store/exportPrompt.ts:29-35,83-120,159-194`, existing state only):
- Export capacity components, task room, locally selected effort, and signed
  remaining/overage. Reuse `budgetTotal`/`localSelected`; never export stale
  server-selected arithmetic after local edits.
- **Identify recurring tasks explicitly**, and export native `scheduledStart`
  plus applicable time-adjustment permission. Recurring timed tasks are
  immutable commitments (`model/types.ts:142-145`), not freely reschedulable.
- Preserve current sequence/placement precedence, but mark whether the staged
  sequence is current or dirty.
- Export resolved preset and effective allotment as *context*, not capacity.
- Preserve source identity/path metadata; invent no write route for Capacities
  or unidentified tasks.

**Needed vs speculative:** recurrence identity, native-time protection, and
capacity limits are necessary. Preset context is cheap and useful but the
resolved numbers govern. `relatesTo` and meaningful tags preserve existing
relationship context. Synthetic grouping IDs, nesting, colours, and speculative
preferences are unnecessary.

**Contract:** none reopened. Retain approval-before-write, Todoist
update-by-ID, and fixed-only calendar publication
(`store/exportPrompt.test.ts:137-157`). Clarify recurrence instructions without
widening external-write authority.

**Tests:** extend `exportPrompt.test.ts` with recurrence/non-recurrence,
protected vs opted-in times, local duration changes, missing capacity, dirty
sequence, and identity cases; extend `ui/components.test.tsx:106-145` with
clipboard success and click-time freshness, keeping the manual fallback.

**Risk:** medium — it shapes an external scheduler's decisions. Reads the
sequence path but modifies nothing in it, no `runtimeAction`, no adapter.
**First**, because an incomplete handoff causes consequential mistakes.

## S2 — Redesign actions as one coherent affordance (items 1 + 5)

**Objective:** explain selection, duration, completion, today-only handling,
and secondary actions together.

**Change** (`ui/Queue.tsx:97-130,569-607`): replace icon-only exclusion and
duration controls with visible **"Exclude today" / "Include today"** and
**"Exact duration"** labels. Group planning adjustments distinctly from
item-handling verbs; retain visible scope notes. Explain exclusion as a
reversible selection toggle and Leave as-is as journaled today-only handling.
**Keep Mark complete and Leave as-is direct**; keep source removal/Delete and
placement actions in More. Adjust wrapping at `app.css:1408-1417,2493-2510`,
preserving control sizes and keyboard behaviour.

**Contract:** **none reopened** — FEEDBACK-07 A13, FEEDBACK-10 frozen verbs,
`ui/locked-contract.test.tsx:25-40`, `store/staging.test.ts:40` all intact. No
change to `STAGING_VERBS` directness or verb IDs.

**Tests:** visible labels, exact source names, scope distinctions, excluded
rows, busy state, responsive reachability, unchanged dispatch mapping; extend
feedback10 tooltip and components exclusion tests without removing guarantees;
test the exact `Upper Fast — Active` label construction.

**Risk:** medium despite visual scope — these verbs reach
`controller.stagingAction → runtimeAction` (`store/controller.ts:694-746`).
Keep handlers unchanged; verify with fake adapters. **Second**, as it affects
every scheduling row.

## S3 — Remove the misleading hypothetical trim (item 2)

**Objective:** show actual over-allotment, not proposed automatic dropping.

**Change:** remove flagged-row derivation and badge rendering at
`Queue.tsx:414,462,777-778` and the associated CSS at
`app.css:1170-1177,1245`. Replace `ActionDock.tsx:110-126`'s hypothetical
"Accept the trim…" with actual selected/budget/overage. Replace
`AllocationPie.tsx:159-165` "trim or drop" with explicit choices, e.g. "Reduce
durations or exclude items". Keep existing aggregate warnings and user-driven
actions. **Retain the now-unused pure trim utility and its tests** — deleting it
is unnecessary to remove the UI concept.

**Contract:** **reopens FEEDBACK-10 LP01/A09**, pinned by
`ui/feedback10.test.tsx:118-135` and its overflow-caption assertions.
Operator-approved supersession.

**Tests:** replace the superseded badge expectation with stronger checks — no
candidate marking, no hypothetical acceptance instruction, unchanged inclusion
after overbooking, accurate overage, preserved readable names and excluded-state
styling. Keep `model/bands.test.ts:110-145` unchanged.

**Risk:** low–medium. **Do not touch the sequence path** — server-side
dropped-row recovery at `controller.ts:539-585` is separate. **Third:** small,
high-value, independently gated.

## S4 — Re-present capacity for scheduling decisions (item 4)

**Objective:** keep over-allotment prominent while making remaining space
actionable. **Approved option: pinned status, quieter pie.**

**Change:** keep the inspectable pie, legend buttons and complete readout, but
reduce competing visual emphasis. Pin a compact task-room status outside the
rail scroll area showing selected/task room plus "N minutes available" or
"N minutes over — reduce durations or exclude". Retain the five-quantity ledger
as quieter supporting evidence. **Do not equate aggregate remaining effort with
a contiguous free calendar gap.** Recompose `Rail.tsx:28-114,167-184`; adjust
sticky behaviour at `app.css:48-55,203-235`. Preserve readiness controls
(`ReadinessStrip.tsx:32-132`) and shortcut content.

**Contract:** **none reopened** — preserves inspection, clearing, legend
buttons, and the planned/capacity/over-remaining hierarchy of
`locked-contract.test.tsx:54-72`. Removing inspection or readout was **not**
proposed and would reopen FEEDBACK-07 and locked items 8/9.

**Tests:** immediate slider/inclusion updates; over/balanced/zero-capacity
cases; pinned-status layout checks. Retain calendar-capacity's five quantities,
a11y status/legend tests, feedback14 shortcuts, panels readiness assertions.

**Risk:** medium responsive-layout risk; no controller, sequence, or adapter
changes. **Fourth**, as it needs visual validation rather than deletion.

## S5 — Put calendar evidence inside the work surface (item 3)

**Objective:** present calendar commitments with the other planning items.

**Change:** replace the separate mount at `App.tsx:75-78` with an inline mode
rendered within `Queue.tsx:803-830`, including its empty-assigned branch. Adapt
`CalendarImpact.tsx:83-120,140-219` for visible inline rows and shared surface
styling. Retain chronological calendar order and the existing seven-cell
desktop/narrow grids. **Do not merge calendar records into assigned-task
state.**

**Contract:** no FEEDBACK-13/17 row-contract reopening. **Explicitly replaces**
the compact-shell disclosure/separate-order acceptance in
`compact-cockpit.test.tsx:24-31,72-79` — a presentation change requiring
acceptance, not a new calendar semantic. Operator-approved.

**Tests:** containment, visible rows, empty/ignored-only frames, committed
execution preceding planning; preserve feedback13/17 hierarchy, quarantine,
hard-wall, accounting, and zero-source-write assertions.

**Risk:** medium. `saveAnchoredOverride` is unchanged — local accounting saves
are not Calendar-source writes. **Fifth**, as composition is broader.

## Ordering and parallelism

No slice depends on an unlanded sibling. S1 and S2 may develop in parallel with
separate ownership. Serialize S2–S5 where Queue/CSS/generated assets overlap,
rebuilding both bundles on each integrated slice.

## Non-goals

No source-adapter changes, no commit-path changes, no new backend semantics, no
live writes, no deployment/restart/probes of the live `:8746` service, and no
Claudius-hosted checks.
