# 2026-10-13 — Schedulable all-day calendars (Unit 1: backend intake)

User-approved feature. A user-chosen **list** of calendars turns its all-day
events into schedulable plan rows (default 30 min / 1 block) instead of
all-day strip entries. The original source event is **never** modified; every
other calendar's all-day events keep the frozen contract-18 behaviour
(all-day, non-timed, no `Start`/`End`).

This document covers **Unit 1 (backend intake) only**. Commit/eligibility,
shadow, runtime actions, and the frontend are Units 2–3 (see below).

## Status

Unit 1 is implemented and green:

- `app/tests/test_schedulable_all_day.py` — 30 passed (written TDD-first).
- Full backend suite `app/.venv/bin/python -m pytest app/tests -q` —
  **3155 passed** (baseline 3125 + 30 new).
- Contract-18 suites unchanged and green: `test_locked_contract_red.py`,
  `test_external_sources.py`, `test_plan_inputs_sources.py` (112 passed).

The feature is **inert** until a calendar is opted in: with no opt-in the
new list is empty, the intake branch is skipped, `split_schedulable_all_day`
is a pass-through, and `exclude_already_committed` finds nothing.

## Config key

New key `calendar.schedulable_all_day` (a **list** of calendar titles or
identifiers). `app_config.sections_from_document` maps it to the
markdown-shaped section `Schedulable All-Day Calendars`, which the routes
pass to `external_sources.fetch_calendar_decisions` as
`schedulable_all_day` (the legacy section name is accepted too).

The machine-local document is layered key-wise over the tracked
`config/tdtb.json`, so a local file may carry only the new key without
restating `calendar.titles`. `version` must equal
`app_config.CONFIG_VERSION` (currently `1`). A missing `sources` section is
accepted.

Exact machine-local example (`~/.config/tdtb/config.json`, or
`$TDTB_HOME/config.json`):

```json
{
  "version": 1,
  "calendar": {
    "schedulable_all_day": ["Evan & Nate"]
  }
}
```

Matching is an **exact casefolded title-or-identifier** match against the
live calendar inventory (each entry may also be a row dict with `Title` /
`BusyCal title` / `Calendar title` / `Identifier`, or a comma-separated
string). The schema is a **list only**: a bare string, number, object, or
`None` opts nothing in — schedulability is an opt-in, so an ambiguous or
corrupt key degrades to today's behaviour rather than accidentally
converting a calendar.

## Design (Unit 1)

`external_sources.fetch_calendar_decisions` takes the all-day branch only
after the disabled (`excluded_disabled_calendar`) and unknown
(`unresolved_unknown_calendar`) filters have already run, and the
`ignored`/`quarantined` capacity classes still win. When the event's
calendar is opted in **and** the event has a stable event id, it emits a
schedulable block instead of the strip entry:

- `schedulable_all_day: True`, `all_day: True`
- `event_id: <stable id>`
- `identity: calendar:<event_id>:<YYYY-MM-DD>`
- `path: calendar://<event_id>/<YYYY-MM-DD>`
- decision `included` / `reason_code: included_schedulable_all_day`

The `<YYYY-MM-DD>` is the **logical day the route plans** (`today`), not the
raw event start, so the identity is stable per planning day and matches the
Ignore List's path form. Events without a stable id keep today's behaviour.

Two seams sit between intake and the digest:

- `split_schedulable_all_day(blocks) -> (busy_blocks_without_them, rows)` —
  removes schedulable blocks from the busy frame and returns assigned-row
  shapes `{name, source: 'calendar', assigned: True, blocks: 1, identity,
  path, event_id, calendar_id, calendar_title, capacity_class}`.
- `exclude_already_committed(rows, blocks) -> (kept_rows, decision_rows)` —
  drops a row when a **timed** event on TDTB's own zone-write calendar
  (`capacity_class == 'ignored'`, i.e. an `own_ids` match) has an **exactly
  equal title** (title only). A non-ignored class or an all-day own block
  never dedupes. Each drop emits decision `excluded` /
  `reason_code: excluded_already_committed`.

`app/main.py` wiring:

- `/plan-inputs`: `ext_cfg` carries `schedulable_all_day` from
  `config.get("Schedulable All-Day Calendars")`. After
  `fetch_calendar_decisions`, the busy frame is split, the rows are
  deduped against the remaining timed busy blocks, and the drop decisions
  are appended to `calendar_decisions`. The kept rows join the
  `disambiguate_names` chain, then `assigned_input`, so the Ignore List
  (name/path), tag exclusions, and the Drop-from-plan filter all apply via
  `build_digest`. `build_digest_index` already retains
  `source`/`identity`/`path`/`blocks`, so the dated digest index carries the
  calendar row for later units.
- `/capacity-preview`: same `ext_cfg` key and the same split, so the two
  consumers agree on the busy frame. The schedulable rows are not part of
  the capacity math (they are assigned plan rows); an all-day event already
  cost zero capacity.

## Exact signatures

- `calendar_bridge.normalize_schedulable_all_day(raw: Any) -> set[str]`
- `app_config.sections_from_document(document: dict) -> dict` (adds the
  `Schedulable All-Day Calendars` section)
- `external_sources.fetch_calendar_decisions(store, config, today) ->
  tuple[list[dict], list[str], list[dict]]`
- `external_sources.split_schedulable_all_day(blocks: list[dict]) ->
  tuple[list[dict], list[dict]]`
- `external_sources.exclude_already_committed(rows: list[dict],
  blocks: list[dict]) -> tuple[list[dict], list[dict]]`

## What Units 2–3 must still do

- **Route identity + commit eligibility** — add a `calendar` branch to
  `_route_source_identity` and `_server_identity_matches` so a submitted
  calendar row resolves against the server digest index. Until then, Build
  preview (`POST /commit?mode=shadow`) and Live commit **refuse** these rows
  because server-index eligibility has no calendar branch.
- **Shadow** (`app/shadow.py`) — a Blocks-only manifest entry for the
  calendar row (so re-runs do not double-place).
- **Runtime actions** (`app/runtime_actions.py`) — drop / unassign /
  complete / delete semantics for a calendar-sourced row.
- **Frontend** — extend the `Source` type with `'calendar'`, wire
  `projectAssigned`, and render a calendar badge/source label.

## Unverified gaps

- No live end-to-end run against a real opted-in calendar (fakes only; no
  EventKit, no network, no source writes).
- Commit/eligibility refuses calendar rows until Unit 2 lands (expected and
  documented above).
- Frontend rendering of calendar rows does not exist yet (Unit 3).

## Activation

Leave `calendar.schedulable_all_day` absent or empty to keep the feature
completely inert. To activate, add the list to
`~/.config/tdtb/config.json` as shown above and restart `:8746` as an
attended deployment action. Do not activate before Unit 2 if commit must
work for these rows (it will otherwise refuse them).

## Unit 3 (frontend)

Implemented green. The cockpit now carries a schedulable all-day calendar
row end-to-end: the source survives projection, the row keys on its
`calendar:<event_id>:<YYYY-MM-DD>` identity, the queue renders a CALENDAR
chip, and the manual-fallback prompt names the row's identity instead of
`(unidentified)`.

### Changed surface

- `frontend/src/model/types.ts` — `Source` gains `"calendar"`.
- `frontend/src/adapters/wire.ts`
  - `itemIdentity` returns the row's `identity` for a `calendar` source
    (before the generic non-`todoist://` path fallback).
  - `projectAssigned` maps `source === "calendar"` to the real source (not
    the neutral Todoist-like fallback), keeps `path` (`calendar://…`), and
    passes that path into `itemIdentity` alongside `identity`.
  - `shapeAssignedWire` already spread raw rows verbatim, so `source`,
    `identity`, `path`, `event_id`, `calendar_id`, and `calendar_title`
    survive the `/sequence`, `/validate`, and `/commit` bodies unchanged;
    the new test pins that.
- `frontend/src/store/exportPrompt.ts` — `sourceIdentity` gains a `calendar`
  branch reading `identity ?? path`, mirroring the Capacities branch, so a
  calendar row is named, never `(unidentified)`.
- `frontend/src/ui/Queue.tsx` — unchanged: `sourceChip` already mapped
  `calendar` -> `CALENDAR` and `app.css` already carried
  `.qrow__src--calendar`; only the projection kept rows from reaching it. A
  rendering test now pins the chip.

### Tests (TDD, red first)

- `frontend/src/adapters/wire.test.ts` — `projectAssigned` projects a
  calendar row's source/path/identity; `itemIdentity` prefers the calendar
  identity over its path; `shapeAssignedWire` preserves the calendar row
  fields through shaping.
- `frontend/src/ui/row-labels.test.tsx` — `sourceChip` maps calendar to
  CALENDAR, and a row projected from a calendar wire payload renders
  `.qrow__src--calendar` with the CALENDAR text.
- `frontend/src/store/exportPrompt.test.ts` — a calendar row exports
  `(calendar · calendar:ev-1:2026-10-13)`.
- `frontend/src/store/staging.test.ts` — a refused staging verb for a
  calendar row surfaces through `runtimeError` and does not refresh sources
  (covering test; `controller.stagingAction` already behaved this way).

### Verification

- Focused suites: 205 passed (4 files).
- Full frontend suite `npx vitest run` — **1412 passed | 6 skipped**
  (baseline 1409 | 6; +3 net new tests).
- `npx tsc --noEmit` — clean; the wider `Source` union flags no exhaustive
  switch.
- `npm run build:mockup` and `npm run build:prod` — both exit 0; the
  regenerated `index.html` files reference the emitted assets, and the
  bundles carry `CALENDAR` and the `calendar · ` identity handling.

### Unverified gaps

- No live end-to-end run against a real opted-in calendar (fixtures only).
- Commit/eligibility still refuses calendar rows until Unit 2 lands; the
  frontend surfaces that refusal (pinned by the staging test) but does not
  make it succeed.
- `projectAssigned` carries `source`, `path`, and `identity` only;
  `event_id` / `calendar_id` / `calendar_title` are preserved verbatim on
  the outgoing wire body but are not modelled on `AssignedItem`.

## Review fix — Unit 2 calendar diff scoping (2026-10-13)

Blocking review finding: a schedulable calendar row (source `calendar`) whose
name equals its all-day source event was classified `no-op` by the shadow diff.
`gather_live_state` reads every calendar, and `_match_calendar_event` matched the
row title with an any-time fallback, so the **original** all-day source event (on
another calendar) stood in for our create. `commit.write_calendar` scopes its
idempotency match to the resolved Blocks `calendar_id`, found nothing, and failed
the reconciliation with `calendar: no event landed … (silent drop)`.

Fix: `ManifestEntry` carries an internal `calendar_source` marker; only the
Unit-2 row sets it. `gather_live_state` adds `calendar_own_ids` — the configured
title map resolved to live ids, mirroring `commit_run._resolve_calendar_ids` /
`commit.plan_writes`. `diff_against_live` narrows a `calendar_source` row's
match to events on those own calendars, so an imported source event can never
satisfy the row. `app/commit.py` is unchanged. Preview and live now agree: the
first run is `would-create`; a re-run with the timed Blocks event present is
`no-op`; the source event is never touched.

Tests (TDD, red first) — `app/tests/test_schedulable_all_day_commit.py`:

- the autouse live-state fake no longer masks the source event: it returns the
  `src-1` all-day event on the non-Blocks `CAL-EVAN` calendar, so the existing
  preview/live tests exercise the scoping;
- `TestCalendarSourceDiffScoping` — the source event alone is `would-create`; a
  same-title/same-start timed Blocks event is `no-op`; the real
  `gather_live_state` resolves `calendar_own_ids == {"cal-blocks"}`;
- `TestLiveCommit.test_timed_blocks_event_makes_the_row_a_noop_without_a_second_event`
  — idempotent, no second event, source byte-identical.

Verification: focused suites 308 passed; full backend suite
`app/.venv/bin/python -m pytest app/tests -q` — **3188 passed** (baseline 3184,
+4). Pre-fix the module failed 4 tests with 0 Blocks events created.

## Live finding: explicitly "ignored" calendars (post-restart)

The user's `🍯 A + M Busy Bees!` calendar is classed `ignored` in config
(family calendar: visible, no capacity). The unit-1 gate refused every
`ignored` calendar, so nothing became a row. The gate now refuses only TDTB's
own-write calendars (`calendar_id in own_ids`) and `quarantined`; an explicit
`ignored` class no longer blocks an opt-in. Timed blocks from own-write
calendars carry `own_write: True`, and `exclude_already_committed` dedups only
against those (previously any `ignored` calendar), so a same-title event on the
family calendar cannot hide a row. Tests: `test_schedulable_all_day.py`
(`test_explicit_ignored_class_does_not_block_opt_in`,
`test_non_own_calendar_does_not_dedup`). Full backend: 3189 passed.
