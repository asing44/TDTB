# TDTB cockpit UI — round 2

Status: **approved**. Branch `TDTB/cockpit-ui-round2`, based on `ff15ac3`.

Source: the operator's second annotated pass over the live cockpit, carried
forward in the session handoff. Round 1 (the approved slice plan in
`docs/plan/2026-10-06-tdtb-cockpit-ui-feedback/plan.md`) is landed; this
document governs the follow-on work only.

## Operator decisions recorded

Four questions were put to the operator at the start of this round. Answers:

| Question | Decision |
|---|---|
| W5 — "Exclude today" vs "Leave as-is" | **Relabel to show durability.** Keep both mechanisms; make the session-only vs persisted difference legible in the labels. No merge, no backend change. |
| Phantom `Task` in `NATIVE_TASK_STRUCTURES` | **Remove it.** The shipped default becomes `frozenset({"RootTask"})`. Behaviourally a no-op today; fixes the permanently-stale settings-drawer entry. |
| W4 — "running list of sources" from Capacities | **Move only.** Relocate Day setup onto the main page; the Capacities-backed pickup list is deferred (see below). |
| Landing | **Land on `main` and push**, after the full backend and frontend gates pass. |

## W1 — Duration labels resolve to the wrong minutes

A row chip reading `30min SOURCE` was annotated "should be 01:00". This is a
correctness bug, not a display preference.

`app/duration_tags.py` recognizes exactly `^dur(\d+)$` and
`^🚀\s*(\d+)\s*min$`. Every other duration-shaped label falls through
`resolve_duration` (locked precedence, frozen item 11: remembered → tag →
native → preset → type → default) to `DEFAULT_MINUTES = 30`.

Verified on live data: Todoist task `Inspect behind dishwasher`
(`6hhMxMM27Rf5FcwG`, due 2026-10-07, P4) carries the single label `🏃‍♂️ Hour`
with no native duration, and resolves to 30 instead of 60.

| Label | Should mean | Before |
|---|---|---|
| `🚀 10min` / `🚀10min` | 10 min | recognized |
| `🍅 Half-hour` | 30 min | unrecognized → default 30 (accidentally correct) |
| `🏃‍♂️ Hour` | 60 min | unrecognized → default 30 (**wrong**) |
| `🐢 Multi-hour` | no duration | unrecognized → default 30 (**wrong**) |

The 30-minute default masks the `Half-hour` case, which is why only `Hour` was
noticed.

**Decisions.** `🍅 Half-hour` → 30; `🏃‍♂️ Hour` → 60; `🐢 Multi-hour` →
**recognized but assigned no duration**, falling through to remembered memory
and then the default rather than being guessed. Extend the **built-in
patterns**, not a configurable label→minutes map.

**Design constraint.** `duration_tag_minutes() -> int | None` cannot express
"recognized duration label with no value" — `None` already means "not a
duration tag". The recognizer must distinguish *not a duration label* from
*duration label with no value*, while `is_duration_tag` keeps its meaning.

**Traps.** `Multi-hour` contains `hour` — match most-specific-first or the
longest label silently becomes the shortest. `_duration_tag` raises
`ValueError` on a same-precedence collision where two matching labels give
*different* minutes; keep that intact.

**Tests.** Extend the existing suite pinning `duration_tag_minutes` /
`resolve_duration`; add the failing assertion first.

## W2 — Calendar as a collapsible band with task-row controls

Operator's choice: *Collapsible band + task-row controls*. The calendar band
becomes collapsible in the priority flow like the urgency bands, and each event
row gains the task-row control vocabulary so a calendar event is handled like a
task.

Today `frontend/src/ui/CalendarImpact.tsx` has no disclosure; its only controls
are a counted-duration −/+ stepper and an Exclude/Count toggle saving day-local
participation overrides. It is mounted inline in `Queue.tsx`. The collapsible
pattern to copy is the urgency band header (`aria-expanded` + toggle handler).

Calendar rows are a deliberately separate data model from task rows. Whether
"task-row controls" means adapting the control components or widening the
calendar row's data was settled by design review before implementation, and the
verdict is recorded in this round's acceptance notes.

**Test-locked:** `compact-cockpit.test.tsx`, `locked-contract.test.tsx`,
`feedback10.test.tsx`, `feedback13.test.tsx`, `feedback17.test.tsx`.

## W3 — Trim the rail's verbose paragraph

Operator's choice: cut the **hardcoded frontend text**, keep the **server
warning verbatim**, and fix the flagged spacing.

`ReadinessStrip.tsx` renders each `s.inputs.sourceWarnings` entry verbatim
(server text — keep) and separately adds hardcoded frontend copy: the
five-minute-cache explanation and a second "Wait at least a minute" paragraph.
That hardcoded block is what was circled and is safe to delete. Spacing lives
at `.rail__partial` / `.rail__chips` and the rail/scroll gaps in `app.css`.

**Test-locked:** `failure-states.test.tsx` pins warning visibility and verbatim
behaviour.

## W4 — Move Day setup onto the main page

Operator's choice: *Move Day setup itself onto the main page* as a drop-down,
out of the Settings dialog. Today `SettingsShell.tsx` holds three tabs — Day
setup, Capacities, Tag exclusions — and the banner he means is the rail's
"Setup pending — start here" chip and/or ActionDock's setup prerequisite.

**Deferred (recorded at operator's direction).** He also asked for "a running
list of sources for both Stoic items and the live block items that I can pull
from Capacities". Nothing existing matches: `Stoic` is a single capture
textarea mapped to `stoic_intention`, and "Live" means two distinct things — an
anchored `Live` window block and a separate micro-adventure pick/pool. The
operator chose **move only** for this round and asked that the deferral be
recorded here. The pickup list needs its own scope pass: which field it feeds
(Stoic intention, the anchored Live window, the micro-adventure pool, or all
three), what a "source" is in Capacities terms, and whether the mapping
contract must widen first.

**Test-locked:** `feedback16.test.tsx`, `settings-shell.test.tsx`.

## W5 — "Exclude today" vs "Leave as-is"

The operator asked whether these row controls are the same. They are not, but
the labels do not say so:

| | `Exclude today` | `Leave as-is` |
|---|---|---|
| Mechanism | in-memory store override (`Queue.tsx` → `controller.setOverride`) | `POST /runtime-actions` verb `drop_from_plan` |
| Server write | none | date-scoped runstate exclusion |
| Durability | session only, lost on reload | persists; eligible again tomorrow |
| Lands in | "Excluded today" | "Dropped today" |

**Decision: relabel to show durability.** Both mechanisms stay; the labels make
the session-only vs persisted distinction legible. No merge, no verb change, no
backend change, no store rework.

## W6 — Phantom `Task` in the native task structures

`app/capacities_assignment.py` ships `NATIVE_TASK_STRUCTURES =
frozenset({"RootTask", "Task"})`, but `Task` is the *display name* of the
`RootTask` structure — asking Capacities for object type `Task` returns
`objectTypeId: RootTask`. The default therefore holds a name where a structure
ID is required and can never match, which is why it shows as permanently stale
in the settings drawer. Removing it is behaviourally a no-op today.

**Decision (operator-approved): remove `Task`.** Default becomes
`frozenset({"RootTask"})`.

## Common execution and verification

Dedicated `TDTB/` branch/worktree; `main` stays integration-only. TDD for
Python and frontend behaviour: demonstrate the failing assertion first, then
implement. Preserve foreign dirty paths and foreign worktrees; stage only owned
paths with path-scoped commits.

Gates: repo-root backend pytest; from `frontend/`, `npm test`,
`npm run typecheck`, `npm run build:mockup`, `npm run build:prod`. **Both
bundles must be rebuilt from source and read back** after the final frontend
change — read both `index.html` files and confirm no superseded hash is still
referenced, including the CSS hash. Independent review and owned-path diff
inspection before integration.

The live `:8746` service loads backend code at process start, so a backend
change on this branch needs an attended restart to take effect; rebuilt cockpit
bundles go live on reload alone. No restart is performed by this round.

## Non-goals

No source-adapter changes, no commit-path changes, no new backend semantics, no
live source writes, no billed `sequence`/`adjust` calls, no deployment or
restart of the live service, and no Claudius-hosted checks.
