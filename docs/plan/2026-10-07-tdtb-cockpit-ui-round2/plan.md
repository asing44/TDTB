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
participation overrides. It is mounted inline in `Queue.tsx` at two sites (the
normal branch and the empty-assigned branch). The collapsible pattern to copy is
the urgency band header (`aria-expanded` + toggle handler).

**Design verdict (settled before implementation).** Calendar events keep
`AnchoredBlock`. The row gains calendar-specific, capability-limited controls
that share the task row's styling and vocabulary; the task-row data model is
**not** widened and calendar records are **not** merged into assigned-task
state.

Applicable subset: Exclude/Include today, the counted-duration −/+ stepper, and
"Exact duration" explicitly qualified as **counted time**. Refused as
structurally inapplicable rather than merely unimplemented:

- **Mark complete** — an imported event has no completable task source.
- **Leave as-is / `drop_from_plan`** — no runtime target or journal exists for an
event, and aliasing it to calendar exclusion would misdescribe both controls.
- **More → source removal/Delete and placement/Unschedule** — the commitment's
time is fixed and no writable source exists.

No empty More menu is added. Imported events lack both owned manifest membership
and task digest identity, so `runtimeAction` being callable does not confer
eligibility.

**Anchoring note.** The calendar band's participation and accounting controls
write `daySetup.anchored[id]` through `saveAnchoredOverride` — persisted Day
Setup. Unlike a task row's session-only override, these survive a reload. They
are local planning writes and never Calendar-source writes, and they must not
imply that typed accounting resizes the commitment or changes its authoritative
busy interval.

**Disclosure.** Default-expanded, component-local state following the queue's
existing `useState` precedent, with the same `aria-expanded` vocabulary the
urgency bands use. Collapsing performs no save. The calendar is placed at the
start of the priority-band stack, before Critical; `model/bands.ts` is
unchanged because the calendar is not an urgency tier.

**Accounting precision.** The existing integer grid is preserved — typed
multiples of 30 minutes, 30–360 — rather than the task row's five-minute
precision, so `countedBlocks`' round-up behaviour is unchanged.

**Test-locked:** `compact-cockpit.test.tsx`, `locked-contract.test.tsx`,
`feedback10.test.tsx`, `feedback13.test.tsx`, `feedback17.test.tsx`.

## W3 — Trim the rail's verbose paragraph

Operator's choice: cut the **hardcoded frontend text**, keep the **server
warning verbatim**, and fix the flagged spacing.

`ReadinessStrip.tsx` renders each `s.inputs.sourceWarnings` entry verbatim
(server text — keep) and separately adds hardcoded frontend copy: the
five-minute-cache explanation and a second "Wait at least a minute" paragraph.
Verified: the delete candidate is the single hardcoded
`<p class="rail__partial-note">` block at `ReadinessStrip.tsx:140-146`. The
`coverage.warnings.map`, the `role="status"` wrapper and the `{warning}` node
are server-verbatim and stay; the now-unused `.rail__partial-note` CSS rule goes
with the block. Do **not** strip "Wait at least a minute" from the warning node —
`failure-states.test.tsx:52-54` carries it in the server-warning fixture too, and
the adapter's warnings stay authoritative and are never rewritten.

**Spacing — operator: the rail's overall vertical rhythm.** `app.css` carries
three competing values for the same rhythm: `gap: 15px` at `:34-54`, overridden
to `12px` at `:292` and `18px` at `:2844-2846`. Consolidating them into one
coherent scale is this round's change. The operator expects to tune the result
in an interactive editing pass, so the delivered value is a deliberate first
proposal, not a final judgement.

**Test-locked:** `failure-states.test.tsx` pins warning visibility and verbatim
behaviour. `:88-89` pins verbatim server text and must keep passing; `:92-93`
pins the hardcoded copy and must be deliberately updated to absence checks;
`:104`, `:116`, `:133`, `:143`, `:147-148` and `:164` survive the deletion
untouched, and the warning fixture helper at `:52-54` must remain.

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
| Lives in | **this browser** — `localStorage`, keyed by `valid_date` | **the server** — runstate |
| Survives | a same-date reload/retry; dies on a date change | eligible again tomorrow |
| Lands in | "Excluded today" | "Dropped today" |

**Corrected premise.** An earlier draft of this document — inherited from the
session handoff — described `Exclude today` as "session only, lost on reload".
That is **wrong**. `attachSessionPersistence` (`main.tsx:96-101`) persists
overrides to `localStorage` keyed by `valid_date` (`store/persist.ts`, locked
decision 16), and `store/persist.test.ts` asserts both same-date save and
restore. The truthful distinction is **where the state lives** — this browser or
the server — not whether it survives a reload.

**Decision: relabel to show that distinction.** Both mechanisms stay; no merge,
no verb change, no backend change, no store rework.

**Operator-chosen wording: `Exclude today · browser` / `Leave as-is today ·
server`.** Accessible names change, so the affected queries must be updated
deliberately: roughly twenty assertions in `row-actions.test.tsx`, plus
`components.test.tsx`, `rail-status.test.tsx`, `feedback10.test.tsx` and
`locked-contract.test.tsx`. The guarantees survive; the queries change.

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

## Deferred follow-ups

1. **Capacities-backed pickup list** (from W4). Deferred at the operator's
direction; see the W4 section for why "a running list of sources" does not map
onto anything that currently exists.

2. **Session-close diagnostic: delegated-agent termination modes**
(operator-requested). Run the `systematic-debugging` skill against the agent
termination failures observed this round, once the round is landed — not during
the write lane.

   *Evidence gathered from the task transcripts, not inferred:*

   - **The turn-cap wraps are real and in-transcript.** Three build agents
     received an explicitly injected message — "You have reached your turn
     limit. Wrap up immediately" — and only then produced a final report. Where
     they stopped: W2a attempt 1 after 40 tool calls with **no production
     code**; W2a attempt 2 after 40 tool calls with the **implementation and
     tests complete but uncommitted**; the W1/W6 agent with W1 committed and W6
     not started.
   - **The cap is ~40 tool calls per agent run.** Attempt 1 shows a
     discovery-heavy brief consuming the whole budget before any code is
     written; attempt 2 shows the *same* budget fitting implementation plus
     tests once discovery is pre-done. That validates this round's "locate it
     yourself, hand exact `file:line` anchors" strategy as the primary
     mitigation.
   - **A separate transport failure exists and is a different mode.** The W4
     design agent died mid-flight with `stopReason: error` and
     `WebSocketCloseError` (code 1000) on provider `openai-codex` with a
     ~337 KB request payload, producing no answer at all. The three turn-cap
     agents ran on `opencode-go` / `deepseek-v4.1-flash` instead. Provider and
     payload size are worth testing as differentiators.

   *Reported, not yet reproduced from these files:* the operator's account that
these agents "died on session crash". The transcripts show a clean turn-cap
wrap for three and a transport error for one — not a session-level crash. If a
parent-session crash or reload did occur, it may be the upstream cause of the
wrap-up injections, but that needs the operator's observation of what the
session actually did. It matters because it changes the mitigation: resume and
idempotent retry versus smaller slices and a higher turn cap.

   *Why it matters:* the commit silently became a root-owned step, so the
   handoff's "verify the worktree, run the checks yourself, and commit" was
   load-bearing rather than a safety net. Its secondary effect is that
   discovery-time scratch files survive into the tree: one agent's
   `frontend/src/ui/__debug.test.tsx` carried a deliberately failing assertion
   and would have made every subsequent `vitest run` red.

   *Investigate:* whether commit should be removed from child scope entirely
   (root-owned, children stopping at a verified tree); whether the turn cap
   should be raised or declared per slice; whether scratch files need a standing
   `/tmp` rule rather than the one adopted reactively mid-round; and whether the
   `openai-codex` transport's large-payload closes should steer design agents to
   a different provider or tighter context.

## Acceptance evidence — round 2, first landing

Landed as merge `d3f7c50` and pushed; `main == origin/main` verified.

Delivered commits: W1 `688993a`, W6 `e55f6f1`, W2a `6dd88eb`, W3
`0d75908`/`a50d795`/`3898caa`, plan docs `1d543ac`/`50e5f27`, bundles `5bcdfb1`.

Gates run on the **landed** state — the first time this round's changes and the
other session's `judgment.py` change were tested together (backend pytest from
the repo root, frontend from `frontend/`):

| Gate | Result |
|---|---|
| `app/.venv/bin/python -m pytest app/tests -q` | **2315 passed** |
| `npm run typecheck` | **clean** |
| `npx vitest run` | **1020 passed / 6 skipped** |
| Bundle read-back | no superseded hash (`index-Bpkz8NAb`, `index-CXAiTEij`, `index-aSipGF9_`) referenced anywhere; current hashes appear only in the two `index.html` files |

**Independent adversarial review: not refuted.** It reproduced the duration
behaviour over 35 labels including adversarial variants (`hourly`, `pre-hour`,
`24hour`, `multi_hour` correctly not tags; no label silently reaching the
30-minute default), the remembered-memory precedence, the collision path, the
phantom-`Task` default with explicit `"Task"` input still round-tripping, the
band ARIA in rendered markup, and the absence of any weakened assertion. It
confirmed `feedback17.test.tsx` is unmodified and that narrowing
`feedback10.test.tsx`'s `.band` selector was a necessary update rather than a
weakening. It also refuted the duplicate-`id` risk outright: the two
`CalendarImpact` mounts sit in mutually exclusive returns and `Queue` is mounted
once (`App.tsx:76`).

**Not verified:** that a live Capacities object-type request for `Task` returns
`objectTypeId: RootTask` (no network access taken; inherited from the session
handoff). The change is behaviourally inert regardless — the adapter sources
structure ids from the API's own id fields (`capacities_adapter.py:188-189`), so
a display name could never have matched.

**Deliberate deviation to revisit:** the rail's short-viewport
(`max-height: 900px`) scroll gap moved 12px → 15px, retiring a tightening rule.
The operator expects to tune this interactively; a wireframe was produced for
that decision at `/tmp/tdtb-rail-rhythm-wireframe.html`.

**Not in this landing** (the next iteration): W5 relabels, W2b calendar row
controls, W2c the accounting editor, and W4's Day-setup drop-down. Design
verdicts and `file:line` anchors for each are recorded above.

**Operational note:** backend changes only take effect after an attended
restart of the live `:8746` service (`zsh restart-live.sh` in the main
checkout); rebuilt cockpit bundles go live on reload alone.

## Rail rhythm — scroll-dense (landed, and measured)

**Decision:** the operator chose **Scroll-dense** from the rail-rhythm
wireframe. Applied unconditionally (the wireframe modelled it as `8px / 8px`,
same at both heights): `.rail__scroll` gap 15px → 8px and `.rail__section`
padding-bottom 15px → 8px, while the **pinned** groups in `.rail` (date,
status, chips) stay at 15px. Commits `5bf922b` (CSS) and `5fa2b6e` (bundles),
fast-forwarded to `main` and pushed.

**Measured on the live cockpit** (Orca embedded browser, `/static/cockpit/`,
991px viewport, served CSS `index-BzQOP76.css` — the change needs only a reload,
not a restart):

| | Value |
|---|---|
| `.rail` gap (pinned) | 15px |
| `.rail__scroll` gap (stack) | **8px** |
| Rail height / scroll region / pinned total | 991 / **413** / 578 |
| Stack total | **725** |

Stack composition: Capacity section 253 · pie chart 214 · over-caption 32 ·
legend 88 · Keys 138.

**The wireframe's headline benefit does not transfer, and this matters.**

1. **Real relief is ~42px, not the modelled ~94px** — four stack gaps × 7px plus
two section paddings × 7px. The wireframe's representative heights understated
the real rail substantially.
2. **No gap change can protect the allocation block.** In the real app it is the
***first*** stack item, so its visibility depends only on whether the scroll
region exceeds 253px — nothing precedes it for a gap to move. The wireframe
modelled it as being *cut* by preceding content, which is not the real layout.

**Where the space actually goes:** the pinned `.rail__chips` block is **317px**
— the single largest consumer, larger than the whole scroll region's capacity at
this viewport — and the pie block (chart + over-caption + legend) is **334px**.
The stack gap was never the lever.

**Supersedes the earlier short-viewport note.** The "deliberate deviation to
revisit" above (the retired `max-height: 900px` tightening) is now moot: it was
worth 3px by the wireframe's model, and the real bottleneck is pinned-chip
height, not rail rhythm. Any future short-viewport work should target
`.rail__chips` and the pie, not the gaps.
