# TDTB App Pilot — Issues Log

App-code defects found during the build/acceptance run. This is the task-local
issue tracker (spec § findings surface). App code, NOT skills — so bugs land
here, not in `Skills/corrections/` (correction-logger is for skill behavior).
Status enum: OPEN / IN-PROGRESS / FIXED.

---

## ISS-1 — `build_plan_manifest` section-key lookups are app-shape-only (config-key convention mismatch)

- **Status:** FIXED (commit `e41f89f`, 2026-07-13) — option (a), narrow
  Adam-approved unfreeze of `shadow.py`; dual-handle `Presets` / `Anchored
  Lifestyle Blocks` at the manifest choke point; `TestPopulatedTitleCaseConfig`
  regression proven to fail without the fix; 393 app tests pass.
- **Found:** 2026-07-13, during T19 §5 [C1] shadow run (shadow ran clean — no
  mutation, `unavailable_surfaces:[]`, counts sane — but the manifest it built
  was silently wrong).
- **Severity:** High. Silently disables two Phase-5 write classes (PHEP routing,
  Step-E anchored events) on real vault config. This is exactly the "app quietly
  disagrees with the skill" class
  the acceptance gate exists to catch — the reason §5 was HALTED rather than
  papered over (Option 3).

### The defect

`shadow.build_plan_manifest` (`app/shadow.py`) is half-migrated between two
config shapes:

- Its **row-level parsers dual-handle** skill-shape + app-shape and are fine:
  - `_anchored_specs` (line ~171) accepts `block["id"]` OR `block["Block"]` OR `block["name"]`.
  - `_preset_type` (line ~159) accepts `preset["name"]`/`["Name"]` and `["type"]`/`["Type"]`.
- Two of its **section-key lookups are app-shape-only** (lowercase-snake), while
  the real vault config uses **title-case section headings**:

  | shadow.py reads (line) | real config key (from `config_reader.parse_config_markdown`) |
  |---|---|
  | `config.get("presets")` — `_preset_type`, ~159 | `"Presets"` |
  | `config.get("anchored_blocks")` — `_anchored_specs`, ~171 | `"Anchored Lifestyle Blocks"` |

  `config_reader.parse_config_markdown` keys `sections` by **raw heading text**
  (config_reader.py:503, `m.group(2).strip()`) — so the real keys are the exact
  title-case `## ` headings. Both lowercase lookups return empty when fed a real
  config.

  **NOT part of this bug:** the third lookup, `config.get("micro_adventure")`
  (~211), is correct as-is — see ISS-2. `micro_adventure` is a *runstate*
  selection dict (`runstate.py:54`, shape `{id,idea,category}`), injected at
  runtime under the lowercase key, NOT the parsed `## Micro-Adventures` config
  section (which holds rotation settings — a different shape). Mapping it to the
  title-case section would feed rotation config where an `idea`-bearing selection
  is expected and mint a Todoist task named "🌱 None". The original handoff
  conflated the two; the fix is presets + anchored_blocks ONLY.

### Blast radius (silently disabled on real config)

1. **PHEP routing** — `_preset_type` returns `None` for every assigned item →
   `routing = "PHEP" if ptype in _PHEP_TYPES else "Inbox"` always falls to
   `"Inbox"`. Every vault effort that should round-trip through PHEP routes to
   Inbox instead. (Violates spec § 3.2 "All vault efforts round-trip through PHEP".)
2. **Step-E anchored calendar events** — `_anchored_specs` returns `{}` → no
   sequence row ever matches `str(row_id) in anchored` → the Step-E branch never
   fires → anchored Lifestyle Block calendar events are never emitted; those
   rows fall through to the generic Step-D `else`.
*(The "Live micro-adventure reroute disabled" symptom the handoff listed here is
real but a distinct cause — tracked as ISS-2, not this title-case bug.)*

### Reproduction

- **Symptom today:** "Townhome Pontification" (an assigned pursuit that is also a
  Preset) routes to **Inbox** in the shadow manifest instead of **PHEP**.
- **Verified fix probe:** renaming the section key `"Presets" → "presets"` in the
  config dict fed to `build_plan_manifest` makes routing correct (`_preset_type`
  finds the row, `ptype="pursuit" ∈ _PHEP_TYPES` → PHEP). Confirms the section-key
  convention is the sole cause.

### Why no test caught it

Every `commit`/`shadow` unit test passes `config={}`, so the three section-key
lookups were never exercised against a populated config. Zero coverage of the
populated-config path.

### Same-convention collision (one dict, two conventions)

`main.py`'s `/commit` route (line ~376) passes `body.config` straight into
`build_plan_manifest`, then at line ~402 reads `config.get("Calendar Titles")`
**title-case** off the *same dict*. So within one request, `main.py` reads
title-case while `shadow.py` reads lowercase-snake — the migration is genuinely
half-done, not a one-line typo.

### Latent in the T14 gate too

`commit_run.py` (T14 plan-only gate) feeds raw sections into the same manifest
builder — same latent gap, unexercised for the same `config={}` reason.

### Constraint on the fix

`shadow.py` is in the **FROZEN set** (new files only, do not edit). So the fix
location is a real decision, not a free edit:

- **(a)** Unfreeze `shadow.py` narrowly and make its section-key lookups
  dual-handle title-case (mirror the row-level dual-handling already there).
- **(b)** A shared config-normalization adapter with a canonical home that every
  client (`build_commit_body.py`, `commit_run.py`, future T16 UI) calls before
  hitting the manifest — cleaner, since a future T16 UI hits the identical bug
  otherwise, but adds a layer.

Decision pending (held for main-session judgment + Adam sign-off). Fix must ship
with a test that feeds a **populated title-case** config and asserts PHEP routing
+ Step-E firing — the exact gap the `config={}` tests never covered.

### Ties to acceptance

Blocks T19 §5 [C1] live commit. §5 re-runs on a real workday (work-zone blocks
present → natural calendar rows + real frontmatter flips) after this is fixed.

---

## ISS-2 — commit-body assembler never injects today's micro-adventure selection

- **Status:** FIXED (commit `75a76c7`, 2026-07-13) — `build_commit_body.py`
  gains a pure `inject_micro_adventure(config, vault, today)` that reads today's
  **exact-date** run-state note (`tdtb-runstate-<today>.md`, via
  `runstate.runstate_rel_path` + gather's `_extract_json_block` — NOT
  `load_runstate`, which reads strictly-prior for the diff base) and merges
  `micro_adventure` into a config copy before manifest build. No frozen-file
  edit — the fix lives entirely in the new (non-frozen) assembler. Seam test
  `test_build_commit_body.py` drives run-state → injected config → manifest
  reroute and asserts the raw-config path stays Step-E calendar (fix is
  load-bearing / fails-without). 400 app tests pass.
- **Found:** 2026-07-13, disentangling ISS-1.

`build_plan_manifest` reroutes a Live block to a Step-A Todoist create when
`config["micro_adventure"]` is set (SKILL.md Step E Live rule). That key is a
**runstate** field (`runstate.py:54`, shape `{id,idea,category}`), meant to be
injected into the config dict at request-assembly time from the day's chosen
micro-adventure. But `build_commit_body.py:80` emits `config = config.sections`
verbatim and never merges the runstate `micro_adventure` selection in — so the
Live→Todoist reroute can never fire through the headless commit-body path, and a
Live block always emits a Step-E calendar event even on a day with a chosen
micro-adventure.

**Not the same as ISS-1** (that's a title-case section-key miss; this is a
missing runtime injection). Correct fix: the commit-body assembler (and the
future T16 UI) must read the runstate `micro_adventure` field and set
`config["micro_adventure"]` before building the manifest. Deferred because
today's vault state has no Live micro-adventure selected, so §5 doesn't exercise
it — but it had to land before the Live-reroute path could be trusted in
production. Now closed: `build_commit_body.py` is committed (`75a76c7`) and
suite-covered. Last code gap before §5 acceptance is cleared.

---

## ISS-4 — bake-in differ's `gather_live_state` Todoist read is index-lag-stale → false `would-create` DIFFs

- **Status:** FIXED (2026-07-13, same session as found) — see Resolution below.
  Found running the first bake-in day (T18) against the verified-correct T19 §5
  live commit.
- **Severity:** High (blocks the bake-in from ever logging a valid app-day PASS —
  every app-day would false-DIFF on its own same-day creates). NOT a commit-path
  bug: the TDTB live commit is correct; this is a defect in the **bake-in tooling's
  read path**.
- *(ISS-3 was the §4 `/sequence` `proposal_override` 200-not-422 candidate —
  adjudicated **by-design**, not a defect: `SequenceRequest` defines no
  `proposal_override` field, so the route always generates-then-validates. Not
  filed as a bug.)*

### The defect

`shadow.gather_live_state` (used by `bake_in_run.py` → `shadow.diff_against_live`)
reads live Todoist through a **query/filter/search** path. Todoist's query index
lags task creation: a task created via the REST write API is immediately
readable **by ID** (strongly consistent) but does **not** appear in
filter/search/project-list results for some minutes-to-longer (a separate sync
index). So when the differ reads live state shortly after a commit, it does not
see the freshly-created tasks and classifies them `would-create` (net-new) —
producing a false `DIFF`.

### Evidence (2026-07-13)

- Live commit ledger (`00 - META/Cache/tdtb-runstate-2026-07-13.md`):
  `todoist: {status: ok, created: [6 ids], reconciliation: {expected 6, found 6}}`.
  The commit's own read-back (**by-ID**) found all 6.
- Direct `fetch-object` by-ID confirms the tasks exist with correct due-times:
  `6h5Pc3h9JF9gR7FG` Garage Buildout 09:00, `6h5Pc49C8P3rccxG` Volunteering 11:30,
  `6h5Pc44xrqJjhmQq` Townhome 11:00.
- Yet `find-tasks searchText:"Garage Buildout"` → 0, `filter:today` → 7 (none of
  our 5 Inbox creates), `projectId:inbox` → 15 (none of them). Same stale index
  the differ hits.
- Differ verdict was `DIFF` (7 agree / 6 unexplained): the 6 Todoist rows all
  false-flagged (5 `would-create`, 1 `would-update` with `due_time old:None`).
  Vault side (Step B daily-note + 6× Step C flips) correctly AGREE.

### Root cause + fix direction

The differ must read Todoist the same consistent way the commit's reconciliation
does — **by ID**, using the `commit_ledger.surfaces.todoist.created/updated` IDs
from the day's run-state note — rather than re-discovering tasks by filter/search.
On an app-day the created/updated IDs are already persisted in the ledger; the
differ should reconcile against those, not a filter query. (Alternatively: tolerate
index lag with a bounded retry, but by-ID reconciliation is the deterministic fix
and mirrors T14's post-write read-back.) Until fixed, app-day bake-in rows are
INCONCLUSIVE, not PASS — today does not count toward the 5.

### Resolution (2026-07-13)

Fixed in `bake_in_run.py` (non-frozen; `shadow.py` untouched):

- **`_ledger_todoist_ids(vault, today)`** reads the exact-date run-state note
  (like `orchestrate.py`, never `load_runstate` — the ISS-2 lesson) and returns
  the `commit_ledger.surfaces.todoist.{created,updated,noops}` IDs, deduped.
- **`gather_live_state_by_id(...)`** calls `shadow.gather_live_state` for the
  vault/calendar surfaces (consistent) but overrides `todoist_tasks` by fetching
  each ledger ID via `client.get_task` (strongly consistent). No ledger IDs
  (skill-day / pre-commit) → keeps the filter read. Wholesale fetch failure →
  `todoist_unavailable` (UNAVAILABLE, never a false DIFF); a 404 (hard-deleted)
  is skipped as genuinely absent.
- **Two shape bugs surfaced and handled during the fix:**
  1. *`due.date` vs `due.datetime`* — the unified-API v1 task (both `/tasks/filter`
     **and** `/tasks/{id}`) carries the timed due under `due.date`, but frozen
     `shadow._todoist_due_time` reads `due.datetime`, so it read every real task's
     time as `None` (the filter path was silently mis-reading times too, masked by
     fixtures that used `due.datetime`). `_normalize_task_due` mirrors timed
     `due.date`→`due.datetime` in the non-frozen path. *(Latent bug in frozen
     `_todoist_due_time`; worth a follow-up unfreeze so the filter path reads times
     correctly too — the bake-in no longer depends on it, but other shadow callers
     might.)*
  2. *Unreliable `is_deleted`* — v1 `/tasks/{id}` returned `is_deleted: true` for
     tasks the REST-v2 view (independent MCP client) confirmed **live** with correct
     due-times. The differ therefore keys on presence + due time, **not** the flag
     (gating on it would manufacture false `would-create` DIFFs on live tasks).
- **Tests:** 7 new in `tests/test_bake_in_diff.py::TestByIdReconcile` (ledger-ID
  dedup, no-ledger fallback, false-`would-create`→AGREE flip, 404-skip, non-404
  propagate, `due.date`→`datetime` normalization). 413 app tests green.
- **Live proof:** re-ran the real differ against today's commit → **PASS**
  (agree 13 / unexplained 0). `bake-in-log.md` day-1 row now PASS (superseded the
  same-day INCONCLUSIVE false-positive). Bake-in day 1 of 5 = 2026-07-13.

---

## ISS-5 — frozen `shadow._todoist_due_time` reads the wrong due field (`due.datetime` vs live `due.date`)

- **Status:** FIXED (2026-07-13) — narrow Adam-approved unfreeze of `shadow.py`
  (same precedent as ISS-1, `e41f89f`). `_todoist_due_time` now reads the timed
  value from `due.date` when `due.datetime` is absent; date-only `due.date`
  still yields None. Added 3 fixtures with the real v1 shape (2 proven to fail
  without the fix). Full app suite green.
- **Found:** 2026-07-13, while fixing ISS-4 (committed `ef36134`).
- **Severity:** Med. Every live shadow caller (`gather_live_state` filter path +
  any live shadow preview) mis-read real Todoist task times as None, so a
  matching-time task classified as would-update instead of no-op. The bake-in
  differ was already immune — `bake_in_run._normalize_task_due` bridged
  `due.date`→`due.datetime` — but other shadow callers hit the bug directly.

### The defect

`shadow._todoist_due_time` (`app/shadow.py:291`) read `due["datetime"]`, but the
live Todoist unified-API v1 task (both `/tasks/filter` AND `/tasks/{id}`) carries
the timed value under `due["date"]` (e.g. `"2026-07-13T09:00:00"`), with
`datetime` absent. So it read every real task's time as None.

### Why no test caught it

Every unit fixture used the `due.datetime` shape, never the real v1 `due.date`
shape — the same populated-path coverage gap that hid ISS-1.

### The fix

- `_todoist_due_time`: `datetime`-first (unchanged precedence, so every existing
  fixture resolves identically — a strict widening), else read `due.date` **only
  when timed** (contains `"T"`); a date-only `due.date` stays None. The `"T"`
  gate mirrors `bake_in_run._normalize_task_due` exactly.
- `bake_in_run._normalize_task_due` is now redundant but retained as documented
  belt-and-suspenders (idempotent, cheap; guards non-shadow read paths).

### Constraint honored

`shadow.py` is in the FROZEN set — this was a deliberate narrow unfreeze to fix
buggy-frozen code, test-covered before ship (ISS-1 option (a) precedent).

## ISS-6 — frozen `sequence.validate_sequence` walls off `Type: window` anchored blocks (full-span, hard) instead of their duration footprint

- **Status:** FIXED (2026-07-13) — Adam chose option (a) soft-flag; narrow
  test-covered unfreeze of `sequence.py` (ISS-1/ISS-5 precedent). Window-type
  non-permeable blocks no longer contribute a hard wall; overlaps emit ONE soft
  warning per window block (not per-row). Live-verified end-to-end: the LLM
  proposal that previously 422'd now returns 200, timeline renders 12 blocks
  "commit allowed", Foods Breakfast shows amber while Morning Routine (hard)
  still walls. 2 fixtures added (window→soft proven to fail first; hard→still
  walls). Full suite 428 green.
- **Found:** 2026-07-13, first real live app-driven planning attempt (timeline
  `/sequence` rejected 4 tasks). This is a genuine **app⇄headless parity break**
  the bake-in is meant to catch — the app path is NOT yet faithful, so the
  headless `build_commit_body.py` retirement stays correctly gated.
- **Severity:** High (blocks the primary flow). Real live config has
  `Foods Breakfast` = `Type: window, Start 8:30 AM, End 1:00 PM, Duration 45m,
  overlap_allowed: false` — a 45-min breakfast placeable anywhere in the window.
  `Foods Dinner` (window 18:00–20:30, 60m) has the same shape.

### The defect

Two coupled bugs in `sequence.py`:

1. **Full-span, not footprint.** `_anchored_span` (`app/sequence.py:166`) returns
   `(Start, End)` whenever `End` is present, ignoring `Duration` and `Type`. For a
   `window` block that span is the ENTIRE window (08:30–13:00), so the whole
   morning reads as occupied.
2. **Hard, not soft.** The overlap check (`sequence.py:258`) selects every block
   with falsy `overlap_allowed` as `non_permeable` and emits `hard_errors` on any
   overlap. Per the skill SOT (`tdtb-bridger-vault/SKILL.md:512`, the ⬜ anchored
   lifestyle blocks incl. Foods): **"Don't block windows; flag overlaps"** — these
   should surface as warnings, and their windows stay schedulable.

Net effect: the LLM (correctly treating the window as a 45m floater, morning
usable) and the validator (walling the full window, hard) disagree by
construction → every proposal that touches the morning is rejected → re-propose
loop-fails.

### Fix options (Adam's call)

- **(a) Type-aware span + soft flag** — `Type: window` blocks: don't contribute
  their full span to the non-permeable set; overlaps become warnings, not hard
  errors. Matches the "flag overlaps / don't block windows" SOT. Restores parity.
- **(b) Duration-footprint only** — window blocks reserve `Duration` minutes, but
  placement floats, so there's no fixed span to hard-check; collapses toward (a)
  for the validator layer regardless.
- Recommend **(a)**. Requires a narrow, test-covered unfreeze of `sequence.py`
  (ISS-1/ISS-5 precedent): fixtures with a real `window`-shaped block proven to
  fail before the fix; confirm `hard`/duration-only blocks (Morning/Night Routine,
  Sudsing) still hard-block correctly.

### Constraint

`sequence.py` is FROZEN and this is the THIRD substantive frozen-validation issue
touching read-shape semantics. Not hot-patched live — logged for a deliberate
cycle. (Distinct from the shadow.py read-shape adapter trigger; that counter is
shadow.py-specific.)
