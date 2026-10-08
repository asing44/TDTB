# TDTB — Capacities-first migration (retire Obsidian vault reads)

Status: **design complete; operator decisions D1–D4 recorded. Implementation begins at S0.**
Date: 2026-10-08. Surface: this file (there is no `progress.md` in this repo).

## ARCHITECTURE PIVOT (2026-10-08) — artifact contract + producer skill

Operator decision: *"Worth it, build it."* This supersedes the direct-API approach for source
reads and reshapes D10.

**Why.** The app speaks REST and can never use MCP; only an agent can. The REST surface is the
real blocker, and every workaround explored earlier (predicate DSL, tag consumption, budget
pacing) was a workaround for *that* choice. Verified from the public OpenAPI spec:
- List endpoints (`/objects/structure|tag|collection`) return only `id`, `structureId`, `title` —
  **no properties**, so property-based filtering costs one content read per object.
- Object CRUD is **30 requests / 60 seconds** per token+endpoint, exposed via a `RateLimit` header.
- There is **no saved-query endpoint**, so Capacities queries cannot be read by the app.
- Objects have **no tag write path** (`POST`/`PATCH /object` take `properties`/`collections`/
  `blocks` only), and there is **no `tag` property type** — so "tag removed on read" is not
  implementable.

By contrast the agent-side MCP servers already expose what the REST clients lack:
`capacities_listObjectsByTag`, `listObjectsByCollection`, `getObjectTypeShape`,
`updateObjectViaMD`, and on Todoist `find-completed-tasks` and `find-filters`.

**The seam.** One normalized artifact file, consumed by the app, produced by a *swappable*
producer:

| Producer | When |
|---|---|
| Skill over MCP | Now — richest tools, no new system |
| n8n workflow | Later, if it should run without an agent |
| The existing Capacities adapter | Fallback only, or retired |

**Consequences accepted by the operator:**
- Planning becomes two steps: regenerate the artifact, then plan.
- The app loses live self-refresh — it no longer talks to Capacities directly.
- **D10's rule table moves into the skill's config**, where it is just a file the operator edits.
  The rule vocabulary is no longer constrained by what the app can evaluate cheaply.
- The artifact is hand-editable, which is the mutability the operator has been asking for.
- The fate of the existing Capacities adapter and the vault gather must be **decided, not drifted
  into** — two readers coexisting is the main risk.

### Artifact design decisions (operator, 2026-10-08)

- **O1 — Pool rows: assigned plus rules marked `pool: true`.** The artifact carries `assigned: true`
  rows plus pool rows emitted by rules the operator marks as pool sources. Keeps the artifact small
  and read cost bounded while preserving a pool to pick from.
- **O2 — Stale policy: use only when `logical_day` matches today.** A prior day's assignments are
  *wrong* rows, not merely old ones, so this is the line. Age alone does not disqualify.
- **O3 — Skill home: the TDTB repo, canonical**, versioned with the consumer and the rules schema.
  Operator additionally requires a **symlink projection plus documentation in the main
  (WALL-E_PIOS) repo** — per that repo's convention, `.agents/skills/` is a discovery surface and
  never an independent canonical source, so the projection must not be a second copy.
- **O4 — Hand edits: an overlay file merged on load.** `state_dir()/planning-overlay.json`, so
  hand-added rows survive regeneration. Nothing is silently erased by a refresh.

**Slice A1 scope (next):** `artifact_source.py` with the schema validator and staleness logic, the
`sources.mode` knob, the digest `artifact` block, and a hand-written fixture artifact with Todoist
rows only, tested against `build_digest`. Proves the seam with no producer. A1–A3 must precede S5;
**A3 supersedes S2.**

**A1 landed (2026-10-08).** `app/artifact_source.py` (new), `app/app_config.py` (+77),
`app/main.py` (+93), plus `app/tests/test_artifact_source.py` and a fixture artifact. Gate:
**2392 passed** (baseline 2361 + 31). Default mode remains `live`, so nothing changed at runtime.

**GAP FOUND IN A1 — must be fixed before A4 (flip the default).** In artifact mode
`build_clients()` is never called (`app/main.py:2478` branch), so the EventKit `store` stays
`None` and **the calendar degrades** with its existing warning. The design says calendar, habits
and anchored blocks stay on their current readers. The "no live client constructed" rule was
over-applied to the calendar store as well as the Todoist client.

- **Impact today: none.** `sources.mode` defaults to `live`, so the running service is unaffected.
- **Impact at A4: the calendar would break**, which is why this is recorded rather than noted in
  passing.
- **Fix:** split the seam so artifact mode builds the calendar store but not the Todoist client,
  and narrow the `test_artifact_source.py` "no live client" assertions to the Todoist and
  Capacities clients only.

## Operator directive

1. "Capacities has ENTIRELY replaced Obsidian" — retire **all** vault reads.
2. TickTick is never involved — not calendar capacity, not habits.
3. Habits come from **Todoist**.
4. Stoic / intention / For Meegy pick-lists become a **cockpit-edited store**.
5. Remove the stale `"Task"` from persisted Capacities settings. **DONE** — revision 4 → 5,
   `native_task_structures` is now `[Project, RootTask]`.

## Finish line (operator-confirmed 2026-10-08)

"Done" for the core loop. Recorded here because the effort note
(`Pursuits/TDTB.md`) has an empty `Why`, `IN`/`OUT` Scope and `Done When`, which is
why "not done yet" could not be acted on.

**Core loop**
1. I can query my own sources — Todoist and Capacities — with queries I control, not a fixed list.
2. I can assign items from those queries to today.
3. Those items, plus calendar events and existing commitments, are placed and spaced into time blocks automatically.
4. Placement obeys my settings — total time allotted per day, how much of it work may occupy, spacing and ordering.

**Flexibility — the crux**
5. I can change **how sources are read** — which structure, which status or property means "assigned",
   which field means duration — myself, in an editable file, without passing the edit through an agent.
6. Those changes take effect with no code edit, no rebuild, no restart.

**Content pools**
7. An ongoing pool persists across days, and each day draws different selections from it.
8. The pool and pick-list contents — live items, Stoic, For Meegy — are editable in that same file.

**Commit**
9. A day's plan commits and lands on the calendar.

**Bar for 5/6 (operator wording):** "it's okay to hard code, but I need to be able to edit it easily."
The bar is *not* zero hardcoding — it is that the value lives where the operator can change it
without an agent. The test: the mapping must be able to express "source by collection" **or**
"source by tag", so the operator can change their Capacities model without a code change.

## S1 outcome (2026-10-08) — landed, with a found gap

Landed as `7c3e871`. Gate 2361 passed (baseline 2337 + 24 new). Migration run and
verified: 49 files copied, all byte-identical to the vault originals, idempotent on
re-run, vault cache still 80 files and untouched.

**The plan's store inventory was INCOMPLETE.** A census of `00 - META/Cache/tdtb-*`
found four more vault-resident stores that S1 did not move and the design never
listed:

| Store | Files | Status |
|---|---|---|
| `tdtb-duration-memory.json` (+ lock) | 2 | **Real feature store — remembered durations. Genuine gap.** |
| `tdtb-runtime-journal-*.json` | 25 | Not migrated; ownership unestablished |
| `tdtb-precompute-cache.md` | 1 | Not migrated; cache |
| `tdtb-micro-adventure-log.md` | 1 | Not migrated; its write is dropped by D1 |

None of these break anything today — they keep working from the vault — but
`tdtb-duration-memory.json` must be re-homed before the vault can be retired, or
"retire all vault reads" is not achieved. Carry this into S6, and audit for further
vault-relative stores rather than trusting the original five-store list.

**Operational caveat:** the migration tool copies-if-absent (`kept`), not
copy-if-newer. If the old service writes to the vault after a migration run but
before the restart, that newer data does NOT carry over on a re-run — the state copy
must be refreshed deliberately.

## Scope correction (design finding)

The vault is also **written** to, so retiring reads alone leaves a half-vault app:

| Write | Anchor |
|---|---|
| Daily note Step B | `app/commit.py:1200` |
| Daily note Step B (2nd) | `app/commit.py:1225` |
| B6 capture frontmatter | `app/commit.py:1313`, `:1351` |
| Deferrals log | `app/deferrals.py:214` |
| Micro-adventure log + daily-note probe | `app/main.py:1267-1299`, `:1379` |
| Shadow / commit / bake-in tooling | `app/shadow_run.py:41`, `app/commit_run.py:48`, `app/build_commit_body.py:58`, `app/bake_in_run.py:167` |

`intention` "lives only in B6 frontmatter" (`app/shadow.py:587`).

## Current state (verified)

- **Every app-owned store is vault-relative** under `00 - META/Cache/`:
  `app/runstate.py:35` (`CACHE_DIR_REL`), `app/exclusion_settings.py:65-66`,
  `app/capacities_settings.py:74`, `app/capacities_builder.py:81`.
- Vault config `00 - META/Skill-Configs/tdtb-bridger.md` (`app/config_reader.py:37`) supplies
  calendar titles, capacity classes, disabled calendars, ignore list, presets, anchored blocks,
  colour tokens, micro-adventure pool and habits config. It holds **no** Capacities structure data.
- Only out-of-vault channel today: env vars (`TDTB_VAULT_ROOT`, `TDTB_JUDGMENT_*`).
  The structure-titles cache is already vault-free (`app/capacities_structure_titles.py:44-46`).
- **Digest sources**: vault 96, todoist 19, capacities 4, calendar 16 (118 rows).
  Vault rows come from `run_data["pool_items"]` + `["assigned_items"]`, merged at `app/main.py:2511-2521`.
- **Calendar rendering**: `calendar_decisions` has no frontend consumer. The cockpit renders
  `anchored_blocks` (`app/main.py:2659-2660` → `wire.ts:990-1003` → `CalendarImpact.tsx`,
  mounted `Queue.tsx:778`, `:859`). Visibility filter `CalendarImpact.tsx:90`.
  Whole-calendar exclusion today is vault-dependent (`calendar_bridge.py:140-176` →
  `external_sources.py:306-321`, `:508-521`); a vault-free per-calendar flag exists but is dead
  in production (`calendar_bridge.py:393` never sets it).
- **Ignore list** (`config_reader.py:200-227`) matches digest items only — it can never match a
  calendar event, so migrating it creates no calendar exclusion.
- **Capacities read budget**: `max_content_reads = 20` hardcoded at `app/capacities_adapter.py:126`,
  never passed (`capacities_builder.py:1217-1223`), not configurable. Live read defers ~30 of ~50.

## Bugs confirmed

- **exportPrompt leak** — `frontend/src/store/exportPrompt.ts:155-157` filters only `quarantined`,
  so an `ignored` calendar row is still exported under "Fixed commitments — do not move these".
  Fix: add `capacityClass !== "ignored"`, matching `CalendarImpact.tsx:90`.
- Stale phantom `Task` also lingers in `frontend/src/adapters/fixture.ts:206` and several test
  fixtures (dev/test only, no runtime effect).

## Target state

**Config** → `~/.config/tdtb/config.json` (version 1) with sections `calendar` (titles, capacity
classes, disabled ids), `ignore`, `presets`, `anchored_blocks`, `colors`, `micro_adventure_pool`,
`habits`, `todoist.read_query`. New `app/app_config.py`; `config_reader.py:37` rewired to it; the
`get_*` accessors keep their signatures so callers are untouched. One-shot
`tools/migrate_vault_config.py` parses the vault file once and leaves it untouched.

**Stores** → `~/.config/tdtb/state/` — `runstate/`, `exclusions.json`,
`capacities-settings.json`, `capacities-source.json`, `deferrals.json`. Sits beside the existing
titles cache so there is one root with one `TDTB_HOME` override.

Rejected: **Capacities-hosted config** — adds a live-API dependency to every startup, the read
budget is already exhausted, and config must stay readable when Capacities is down.

## Staging (staged, not cutover)

Rollback point is any slice before S5.

| Slice | Content | Rollback |
|---|---|---|
| **S0** | `TDTB_HOME`, `app_config.py`, state-dir helper, dual-read with vault fallback, run migration tool. No behaviour change. | delete `config.json` |
| **S1** | Move the five stores; writes go to new paths; vault copies frozen as backup. | restore paths |
| **S2** | Make Capacities coverage complete within the rate limit (paced convergence; budget configurable with a safe ceiling); add the vault-row diff report. Must land before S5. | config only |
| **S3** | Habits via Todoist behind `habits.source = obsidian \| todoist`; fix calendar-disable wiring in the same slice. | flip flag |
| **S4** | Pick-lists store + UI; fix the exportPrompt leak. Independent of S3. | revert slice |
| **S5** | Cutover: `vault_enabled=false`. Last reversible point. | flip flag back |
| **S6** | Delete vault readers, fallbacks, `TDTB_VAULT_ROOT` requirement, dead disabled flag; amend locked decisions. | — |

## Risks

- **Silent data loss**: ~96 rows vanish with nothing flagging it (see §2 mitigation).
- Missing vault config makes `result.config` None → falls through to defaults
  (`main.py:2528-2541`); presets, ignore list and anchored blocks silently empty.
- An empty Todoist habit query is indistinguishable from "all done" or "misconfigured".
- The 20-read cap defers objects with no per-object error.
- Test locks on the vault paths, `config_reader` parse, `fetch_habit_status` and the settings blobs
  will fail by design; update them in the slice that moves the thing.
- Locked decisions to amend: decision 14 precedence (`main.py:1448`), the "frozen vault gather
  module" statement (`external_sources.py:5`).
- Implicit vault deps a `vault` grep misses: `vault` parameters in signatures, `resolve_vault_root()`
  (`main.py:1818`), env default (`main.py:86`), `daily_note_path` (`runstate.py:50`),
  Capacities cache-namespace keys, legacy-static `setup.js`.

## Decisions (operator, 2026-10-08)

- **D1 Writes — DROP THEM.** Stop writing the daily note (Step B), the B6 captures, the deferrals
  log and the micro-adventure log. Consequences to handle in the slice that drops each one:
  - **Deferrals log is feature data, not just history.** It is the sole source for
    `deferrals.bias_map` (`main.py:2528`, `deferrals.py:214`). Dropping the write leaves the
    deferral-bias feature with nothing to read. Flagged; the feature must be re-homed or retired.
  - **B6 captures must be re-homed, not merely dropped.** `intention`, `megan_nicety` and
    `stoic_intention` live only in B6 frontmatter (`shadow.py:587`). With the daily-note write
    gone, they must persist in the new cockpit store (`state/picklists.json` / runstate) or
    captures stop saving. Must land with S4, not after it.
- **D2 Config home — `~/.config/tdtb`.**
- **D3 Habits — a Todoist project named `🔁 Habits`.** Outstanding = active tasks in that project;
  reached through the existing `get_filter_tasks` using filter syntax `##🔁 Habits`, so no new
  projects endpoint is needed. Done-today still requires a completed-tasks call
  (`/tasks/completed/by_completion_date`), which the client lacks; ship done=0 (matching today's
  behaviour, which already reports done 0) and add the endpoint as a follow-up.
- **D4 Vault rows — triage then migrate.** Produce the diff report, match against the Capacities
  listing, operator triages keepers into Capacities or Todoist before S5.

Applied as defaults unless overridden: **D6** seed pick-lists from current vault values via the
migration tool · **D7** migrate `## Disabled Calendars` into `config.calendar.disabled` ·
**D8** provide a `tdtb-state export` snapshot command rather than syncing `~/.config`.

### D5 CORRECTED (2026-10-08) — the read budget is a rate-limit valve, not a tunable

The original D5 ("raise `max_content_reads` to 60, configurable") was **wrong** and would have
broken the Capacities read. `app/capacities_adapter.py:119-126` documents why the value is 20:

> The live API allows **30 requests per minute** and its structure listing carries no typed
> properties, so every property-based decision costs one content read. A cold read spends this
> plus one listing per structure (**20 + 3 = 23**), leaving room for a second refresh inside the
> same minute before the window fills; objects left unevaluated are reported, never silently
> dropped.

60 content reads plus 3 listings would exceed the 30-request window by more than 2x and trigger
429s. The budget exists so the adapter degrades *deliberately* instead of letting the provider
rate-limit the whole read (`:621-622`), and 429 handling is already present (`:1261`).

**Revised D5:** keep the budget tied to the rate limit. Make it configurable with a documented
safe ceiling of `30 - (one listing per contributing structure)`, and prefer paced convergence
over a larger single-read budget.

**Why this is now a prerequisite, not a nicety:** Capacities supplies only 4 of 117 live rows
today, but D4 asks it to carry the 96 vault rows. At 30 requests/minute that is multiple minutes
of paced reading, so a single cold read can never cover the post-migration corpus. The symptom
("20 evaluated · 30 deferred, budget reached") is therefore by design today and a hard blocker
after cutover.

- **D9 Pick-list / pool store — an editable config file, NOT a cockpit-edited store.** This
  **supersedes** the earlier cockpit-edited-store choice (which itself superseded the vault config
  section). Reason: the operator needs to tune sourcing and content himself without an agent, and a
  file is less machinery than a new persistence layer plus a settings UI. Recorded as a change to a
  settled decision (`user-approved`). Consequence: the S4 pick-list UI slice is dropped; the
  pick-lists become sections of `config.json`, and the cockpit renders them read-only.
- **Ongoing pool (finish-line point 7).** The operator intends to model it in Capacities as a
  *collection* on an event, and is undecided between collections and tags. This does not block
  TDTB provided the D9 mapping can express either (see the finish-line bar above). It is a
  Capacities-modelling question for the operator, not a TDTB decision.
- **D10 Source-inclusion rules must be declarative and operator-editable.** Operator, 2026-10-08:
  the vault scan roots (`CORE_SCAN_DIRS` / `HATCH_ONLY_SCAN_DIRS`, `app/gather/tdtb_gather.py:59-77`)
  are hardcoded Python constants, and the Capacities equivalent must not be. Stated today: an
  object is a planning candidate when its `assigned` boolean is true; some types (e.g. project)
  qualify when `status = active`. The operator has **not finished** defining the universal rule
  set, so the requirement is a *mechanism that can express whatever he lands on* — not a specific
  rule set. He must be able to change it without an agent and without a code edit.
  **What exists today — a closed shape split across three stores:** which structures participate
  (`active_structures`), the status values that count as active (`active_statuses`), the native
  Auto toggles (`NativeTaskAutoPolicy`: active/due/deadline + horizon), and a per-structure
  assignment marker (`StructureMapping.assignment_property` / `open_status_property`, held in the
  operator-owned source-mapping record). Individually editable, collectively closed: the predicate
  vocabulary is fixed and there is no single place to state "this type qualifies when X".
  **Required:** a declarative per-structure inclusion predicate in `config.json` (structure →
  `{property, equals|in, values}`), defaulting to today's behaviour, plus a report showing what
  each rule admits — so the operator can see the effect of a rule before trusting it.
  **Coupling — read the D5 correction first.** Every admitted object costs one content read and the
  API allows 30 requests/minute, so broadening the rules broadens the corpus: a rule set admitting
  ~150 objects cannot be read in one window. Inclusion rules and the read budget must be tuned
  together, which is why D5's paced convergence is a prerequisite for D10 rather than a nicety.
  Lands before cutover (S5), alongside replacing the vault scan roots.

## Not covered

Capacities structure design for unmatched vault rows; the writes in D1; the legacy-static UI.
