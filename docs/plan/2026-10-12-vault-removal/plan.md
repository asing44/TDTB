# TDTB: remove the Obsidian vault entirely (S6 finish)

 Status: **all slices landed (V1-V5a on main; V5b on branch `TDTB/vault-removal-v5b-20261012`, not merged).**  Operator directive (2026-10-12): anything "vault" in the Obsidian sense is
100% deprecated. Hand-edited config moves into the repo; runtime state moves to the state dir
(`~/.config/tdtb/state/`) or is dropped; nothing replaces the Obsidian note writes at Commit.

Builds on `../2026-10-08-capacities-first-migration/plan.md`: S5 is live (`sources.vault_enabled=false`),
so the vault gather already contributes no rows. This plan is the S6 deletion plus the stores S1 missed.

## Decisions (operator, 2026-10-12)

- **Config home: the repo.** A tracked `config/tdtb.json` is the hand-editable config (anchors, presets,
  ignore list, colors, micro-adventure pool, habits, calendar, sources). `~/.config/tdtb/config.json`
  stays as an optional machine-local override layered on top.
- **Commit vault writes: dropped.** No daily-note `# TDTB Plan` patch, no `assigned: true` frontmatter
  flips, no `.trash` move. Todoist and Calendar remain the Commit targets.

## Slices (each lands reversibly, live keeps running)

| Slice | Scope |
|---|---|
| **V1 (landed)** | `config/tdtb.json` seeded from the live config; `app_config` loads repo file then local override; `config_reader.read_config` stops reading `00 - META/Skill-Configs/tdtb-bridger.md`; micro-adventure pool read from config (fallback pool stays in code). Schema Reference ids and Color Palette move into the same file. |
| **V2 (landed)** | Remaining vault-written runtime state moves to the state dir: micro-adventure log, duration memory (+lock), runtime journal. Vault fallbacks for already-moved stores (runstate, recent selections, exclusion/capacities settings, deferrals, source mapping) are removed. See "V2 as landed". |
| **V3 (landed)** | Delete Commit's vault steps (`write_daily_note`, `write_frontmatter_flips`, `vault.trash`, orchestrate/shadow "vault" surface). Replace the `### Live` daily-note probe with state-dir done state. |
| **V4 (landed)** | Delete the vault gather, `schema_guard` + `.base` mirror, `active-inventory`/precompute caches, `vault_enabled`, `TDTB_VAULT_ROOT`, `resolve_vault_root`, `tools/migrate_vault_*`, and the launchd/restart/bootstrap vault plumbing. |
| **V5 (landed as V5a + V5b)** | Wire and UI: drop the `"vault"` source, `source_counts.vault`, `system: "vault"`, the VAULT chip and every user-facing "vault" string; update contract fixtures. |

**V1 as landed.** `config/tdtb.json` is the tracked config (seeded from the live local config plus the vault-note-only
sections). `app_config.load_document()` layers it under `~/.config/tdtb/config.json` (objects merge, lists replace);
`$TDTB_REPO_CONFIG` relocates the repo file (test seam, conftest isolates it per test). `config_reader.read_config`
ignores its `vault_root` argument and returns `bootstrap_needed` only when neither layer is usable; the markdown parser,
`CONFIG_REL_PATH`, `_merge_sections`, `document_from_sections` and `tools/migrate_vault_config.py` are deleted.
Document keys added: `defaults`, `schedulable_defaults`, `ranking_criteria` (lists), `placement_context`,
`schema_reference`, `day_presets`, `template_blocks`, `overlap_permissions`, `micro_adventure_rotation`.
Surprises:
- **Day Presets, Template Blocks and Overlap Permissions were vault-only and are consumed** (`day_semantics`,
  `external_sources`, `sequence`, the judgment prompt), so they moved into the file too; dropping them would have changed
  zones, allotment presets and the overlap policy sent to the model. Checked: the day-semantics projection and
  `planning_config_fingerprint` are identical before and after for the live config.
- `Overlap Permissions` is stored as the verbatim markdown text (`overlap_permissions`) and surfaces as
  `{"_body": text}`; the `/plan-inputs` `config` echo keeps its dict type.
- Not carried (no consumer): `Pool Eligibility`, `Schema Notes`, the BusyCal fallback table, the Ranking Criteria tier
  documentation tables, `Overlap Permissions` sub-tables as data, and the retired `habits.source_directory`.
- `Placement Context` has no content in the note; the key is supported (`placement_context`) but absent from the file.
- `sources` is absent from the repo file (only machine settings existed); `sources.mode`/`vault_enabled` stay local.
- Tests that wrote the markdown note now call `tests/config_markdown.py` (test-only: the old parser +
  `document_from_sections`), which installs the fixture as the per-test repo config. V4/V5 can retire it with the
  remaining markdown-shaped fixtures. `frontend/scripts/capture_contract_fixtures.py` uses it too; committed contract
  fixtures were already stale against code at baseline (23 files differ) and were not regenerated.
- Still reading the vault in V1 scope on purpose: `TDTB_VAULT_ROOT`, gather, commit steps, state files, `schema_guard`,
  and the legacy-static `setup.js` note-path string (V2-V5).

**V2 as landed.** Destinations under `<TDTB_HOME>/state/` (file formats unchanged, so vault files copy as-is):

| Store | Pre-V2 vault file | State path |
|---|---|---|
| Micro-adventure log | `00 - META/Cache/tdtb-micro-adventure-log.md` | `micro-adventure-log.md` (`micro_adventure.history_path()`) |
| Duration memory (+lock) | `tdtb-duration-memory.json` / `.lock` | `duration-memory.json` / `duration-memory.lock` |
| Runtime journal (incl. undo) | `tdtb-runtime-journal-<date>.json` | `runtime-journal/<date>.json` (`runtime_actions.journal_path()`) |

- Vault fallback reads deleted for runstate, recent selections, digest index (`runstate.legacy_*` and `_first_existing`),
  exclusion settings, capacities settings, capacities source mapping, deferrals (`legacy_*_path`), and gather's
  `load_runstate` (it searched the vault cache after the state dir). An absent state file reads as empty/default.
- `vault_root` parameters stay on every signature (ignored) so call sites compile; V4 removes them. `runstate_read_path`,
  `recent_selections_read_path`, `digest_index_read_path` now just return the state-dir path. The `*_REL_PATH` constants
  stay only because `tools/migrate_vault_state.py` names the vault sources with them (V4 deletes both).
- Duration-memory keys: `todoist:<id>` (and capacities identities) read/write as before. Legacy vault-path keys
  (`vault:<path>` or any `/` path) still pass `normalize_identity`, so a copied file never crashes a read or a write,
  but no digest row carries a vault identity any more (vault gather is off), so they simply never match again.
- `tools/migrate_vault_state.py` copies the three new stores with the same contract (never overwrite, vault untouched,
  dry-run, missing reported, lock never copied); journals are renamed `tdtb-runtime-journal-<date>.json` -> `runtime-journal/<date>.json`.
- Tests: `test_v2_state_dir_only.py` runs the log, duration, journal and S1-store flows against a read-only empty vault
  and asserts nothing appears under it; `gather/tests/conftest.py` isolates `TDTB_HOME` for the gather suite.
- Still vault-touching after V2 (V3-V5): the daily-note `### Live` probe, commit vault steps, `runtime_actions` vault verbs
  for vault-path rows, `active-inventory.md`, precompute cache, `schema_guard`, `TDTB_VAULT_ROOT`.
- **Operator step:** the live micro-adventure log and duration memory (and optionally the 25 runtime journals) exist only
  in the vault. Before restarting live on V2, run `python tools/migrate_vault_state.py --vault-root <vault> --dry-run`,
  then without `--dry-run`. It copies only if absent; if the old service writes to the vault after the copy, refresh
  deliberately.

**V3 as landed.** Commit writes Todoist and the calendar only; no vault byte is read or written by Commit or the runtime verbs.

- `commit.py`: `write_daily_note`, `write_frontmatter_flips`, `_set_assigned_true`, `_patch_plan_section`,
  `_resolve_daily_note`, `_plan_vault` and `WriteIntent.path` are deleted. `plan_writes` has no vault branch: a `system: "vault"`
  manifest row (incl. the retired `frontmatter-captures`) now falls into the existing "unknown surface" refusal, so a crafted or
  stale row still refuses the whole commit closed (the dedicated `frontmatter-captures` refusal was dead and is gone).
- `shadow.py`: the manifest no longer emits the Step B `# TDTB Plan` patch row or the Step C `set-flag` rows;
  `diff_against_live` has no vault/daily-note branches; `gather_live_state` no longer walks the vault or reads the daily note
  (state keys `vault_frontmatter`, `daily_note_text`, `vault_unavailable`, `vault_error` are gone; the `vault_root` argument stays, ignored).
- `orchestrate.py`: `SURFACES == ["todoist", "calendar"]`; the `vault_flips` and `daily_note` ledger surfaces and the `plan_body` argument
  are deleted (`main._render_plan_body`, `commit_run._render_plan_body` too). `bake_in_diff` loses its Step B carve-out (nothing emits Step B).
- `runtime_actions.py`: `vault.complete`, `vault.assign`, `vault.unassign`, `vault.trash` (and `.trash`, the FileClass status table, the
  byte-surgical helpers) are deleted. `complete/done/assign/unassign/delete/delete_permanent` on a target that still resolves to a
  `vault_path` raise `RuntimeActionError("... the Obsidian vault is retired ... nothing was changed")` from `plan_steps` (before any
  journal entry), which `/runtime-actions` already maps to 422; an old journal entry holding a `vault.*` step fails its undo with the
  same message (`undo_failed`). Todoist/Calendar/Capacities steps, `defer`, `drop_from_plan` are untouched.
- Live probe: `micro_adventure.daily_note_live_done`, `resolve_prior(daily_note_live_checked=)` and `main._daily_note_live_probe` are deleted.
  The Live "done" state is the `done` field of `state/micro-adventure-log.md`, set by the cockpit "Did it"/Dismiss route
  (`POST /micro-adventure/loose-end`, response shape unchanged) or the Todoist-completion signal. **Information lost:** a `- [x]` ticked
  under `### Live` in an Obsidian daily note no longer marks the prior adventure done; it has no state-dir equivalent, so the entry
  stays `done: null` and the banner asks "Did it / Dismiss" (degrades to "not done", never reads the vault).
- Result shape: the live commit report's `surfaces` map loses the `vault_flips` and `daily_note` keys (all other keys and the per-surface
  entry shape are unchanged); the shadow/`plan_manifest` entries lose their `system: "vault"` rows (Step B, Step C). `frontend/src/adapters/wire.ts`
  builds `surfaces` from `Object.entries` and `projectShadow` copies `system` through, so nothing in the UI breaks; V5 removes the type literal and
  regenerates `commit-live-*.json` (they still show the two removed keys and were stale at baseline anyway).
- Left for V4/V5 on purpose: `vault_root` parameters, `TDTB_VAULT_ROOT`, vault gather, `schema_guard`, `runstate.DEFAULT_RUNSTATE["daily_note_path"]`,
  `ManifestEntry.system` doc "vault" mentions in frontend types, and every UI string.
- Tests: `test_v3_no_vault_commit.py` (live commit, resume, shadow commit and the Live-done flow against a read-only empty vault root;
  nothing appears under it); removed-verb errors in `test_runtime_actions.py`, `test_final_actions.py`, `test_staging_actions.py`; the daily-note
  patch, frontmatter flip, trash/undo, FileClass-status and live-probe tests are deleted with their code.
- Operator step: none. Live Commit keeps writing Todoist/Calendar as before; the daily note simply stops receiving a `# TDTB Plan` section.

**V4 as landed.** Nothing under `app/` reads, writes, scans or requires a vault root any more.

- **Deleted code:** `app/schema_guard.py` (+ `.base` hashing and CLI), `app/inventory.py`, `tools/migrate_vault_state.py`,
  `tools/vault_rows_report.py`. `app/gather/tdtb_gather.py` keeps only `effective_date`, `empty_run_data` (the always-empty note slice of
  `/gather`, same envelope), `parse_frontmatter` (reads the app-owned recent-selections cache), `_extract_json_block` and
  `load_runstate(valid_date)` (state dir only). `walk_vault`, `select_digest_notes`, the scan dirs, the `.base` predicate mirror,
  `build_cache`/`write_cache` (`active-inventory.md`) and the precompute cache are gone. (Fixed on the way: the unused no-PyYAML
  fallback frontmatter parser looped forever on a boolean value.) All `*_REL_PATH` / `CACHE_DIR_REL` vault constants and
  `runstate_rel_path` / `digest_index_rel_path` / `journal_rel_path` and the dead `RUNSTATE_DEFAULTS["daily_note_path"]` key are deleted.
- **`sources.vault_enabled` is gone** (flag, default, validation). The operator's live `~/.config/tdtb/config.json`
  (`sources: {vault_enabled: false, mode: "live"}`) keeps loading: `app_config.SOURCES_LEGACY_IGNORED_KEYS` makes the key
  silently ignored (no effect, no validation failure, no `sources_config_warnings` entry; whitespace-padded too). Pinned by
  `test_sources_config.py::TestLegacyVaultEnabledKey`, including `/plan-inputs` serving with that exact file.
- **No vault root anywhere:** `create_app()` takes no argument; `resolve_vault_root`, `_refresh_vault_root`, `_vault_root_or_none`,
  `_disabled_vault_root`, `VAULT_ROOT_ENV` and `app.state.vault_root` are deleted; `TDTB_VAULT_ROOT` is never read (a set value is
  ignored). Every `vault_root` / `vault` parameter was removed from the store modules, `runtime_actions`, `orchestrate`,
  `shadow.gather_live_state`, `commit.run_commit`, the builders and the CLI harnesses. The injection seams are now
  `(config) -> ...` (`build_read_clients`, `build_calendar_store`, `build_capacities_adapter`, `build_commit_clients`,
  `build_refresh_coordinator`). The ~40 routes that 503'd without a vault root now just serve (e.g. `/config`, `/settings/*`).
  `shadow_run.py`, `commit_run.py`, `build_commit_body.py`, `bake_in_run.py` lose their env check; their digest has no note rows
  (useful input is `--sequence-file`).
- **`/plan-inputs` is unchanged for `vault_enabled=false`:** note rows were already empty, so the digest is built from Todoist /
  Capacities / calendar / artifact rows exactly as before. `source_counts.vault` is still emitted (0, or the count of artifact rows
  tagged `source: "vault"` which are still accepted until V5). `/gather` still returns `pool_count`/`assigned_count`/
  `pool_items`/`assigned_items` (empty) and still seeds the micro-adventure runstate keys, but no longer writes `active-inventory.md`.
  `POST /digest` without a body now digests an empty pool.
- **Orchestrate:** `run_orchestrated` used to persist the ledger / recent selections only when given a `vault_root`; it now persists
  whenever `persist_ledger` is true (the live route always did), so the `vault_root` argument no longer doubles as a persistence switch.
- **Cache namespaces changed (decision for the operator):** the Capacities content-cache namespace, the structure-title cache namespace
  and the direct-refresh snapshot pointer namespace used to hash the vault-root string together with space and base URL. They now hash
  space and base URL only, so after the first restart on V4 the machine-local content cache is rebuilt from fresh reads (one logged
  "ignored a stored cache" warning), cached structure titles reappear on the next Capacities read, and a published direct-refresh
  generation is not served under the new namespace (`sources.capacities_intake: direct` reports `refresh_required` until one Capacities
  Refresh is run; `legacy` intake, the live setting, is unaffected apart from the cold content cache). Nothing is lost that a refresh does
  not recreate.
- **Renames:** `duration_memory.read_vault_memory` -> `read_all_memory`. Legacy `vault:` / path-shaped keys in an existing
  `duration-memory.json` are still tolerated (never crash a read or write, never match).
- **Deployment:** `launchd/com.walle.tdtb.plist` drops the `TDTB_VAULT_ROOT` key and placeholder; `restart-live.sh` and
  `bootstrap-seat.sh` no longer discover or require a vault (bootstrap `--launchd` no longer fails when none exists). README,
  CONTEXT and AGENTS updated; older plans under `docs/plan/` that describe vault steps carry a "Historical" banner.
- **Tests:** deleted `test_vault_cutover`, `test_schema_guard`, `test_inventory`, `test_migrate_vault_state`, `test_gather_metadata`,
  `gather/tests/test_tdtb_precompute`, the 503-without-vault tests and the vault-scan gather tests; `vault_root` arguments, seam lambdas
  and fake `(config, vault_root)` stand-ins were rewritten; the vault-note digest fixtures became Todoist-row fixtures. New:
  `test_v4_no_vault_root.py` (health / config / version / plan-inputs / day-setup / gather / digest / shadow + live commit, run once
  with `TDTB_VAULT_ROOT` unset and once with it pointing at a read-only empty dir that must stay empty, plus a static guard that no
  module under `app/` names the variable), `test_sources_config.py` (legacy `vault_enabled` shape, key tolerance, warnings),
  restart/bootstrap script tests updated. `app/tests/config_markdown.py` is **kept**: 20 test modules (and
  `frontend/scripts/capture_contract_fixtures.py`) install readable markdown config fixtures through it, and converting them to JSON is
  not cheap; the app itself never parses markdown.
- **Left for V5 on purpose:** the wire `"vault"` source (`artifact_source.VALID_ROW_SOURCES`, `source_counts.vault`, `system: "vault"`
  handling and the `vault_path` target key in `runtime_actions`/`deferrals`, the `"vault"` source label in `main._route_source_identity`), the
  frontend, `app/legacy-static` and the built bundles, the external `tdtb-bridger-vault` skill name, and the word "vault" in test
  fixture names / inert path keys that only feed `tests/config_markdown.py`.
- **Operator steps:** none required. Restarting through `restart-live.sh` re-renders the plist from the template (the old installed
  plist exported `TDTB_VAULT_ROOT`; the new one does not). If the service is restarted against the OLD installed plist the variable is
  simply ignored. Optionally run one Capacities Refresh after the restart (see namespaces above).

**V5a as landed** (frontend half of V5; branch `TDTB/vault-removal-v5a-20261012`, not merged). Nothing under `frontend/src` models or says "vault" outside the stale contract fixtures and the tolerance tests/comments.

- Types: `Source = "todoist" | "capacities"`; `ManifestEntry.system` is `"todoist" | "calendar"`; `sourceCounts` is `{todoist, calendar}`.
- Tolerance (backend still emits the legacy fields until V5b): `projectAssigned` maps an unknown or legacy source (incl. `"vault"`, or none) to a **neutral Todoist-like row**: `source: "todoist"`, `path: null`, no `todoistId`, `identity: null`. It is unidentified, so no source-specific verb or duration-memory save can address it. `source_counts.vault` is ignored; a `system: "vault"` shadow/report entry copies through and renders without crashing (ApprovalDrawer totals show only todoist/calendar).
- Rendering: `sourceChip` fallthrough is a neutral `OTHER` chip (`.qrow__src--other`); `sourceContext` is labels only; `typeToken`/`sourceDetail` and the `VAULT_TIERS` stringified-list urgency parser are deleted (Capacities rows carry `urgency: null`; non-Todoist urgency shows verbatim, untiered); the dead `badge--u*` urgency badge in Queue is removed (its CSS rules remain); export prompt drops `(vault . path)` and the "create in PHEP" line (unrecognized rows read `(unidentified)`).
- Strings: ActionDock, BlockEditor, CapacitiesSettingsPanel, CapacitiesSourceEditor, ConnectionsScreen reworded ("the source mapping").
- Fixtures: scenario rows converted (Magic Mirror, Rowe's, Press, Entryway Design -> Todoist with priority ints so bands are unchanged; Note Processing, Deep CWEAN -> Capacities); shadow fixture lost its two vault rows (the "ready" preview now stages 5 exact writes, not 6).
- Left for V5b: delete `source_counts.vault`, vault `system` rows and any `source: "vault"` emission in `app/`; regenerate `frontend/src/adapters/contract-fixtures/*` (about 12 files carry `source_counts.vault`/vault rows, `shadow-diff.json` has `system:"vault"`, `commit-live-*.json` still show removed surfaces) and then tighten `wire.test.ts` (the legacy-tolerance tests can become plain fallback tests); delete the dead `.badge--ucrit/uhigh/umed/ulow/unone` rules in `app.css`; `app/legacy-static/*` (outside the frontend tree) still has chip-vault, `'vault'` source fallbacks and "writes to Todoist, the vault, and the calendar" copy.

**V5b as landed** (backend wire, fixtures, legacy UI, wording; branch `TDTB/vault-removal-v5b-20261012`, not merged). Nothing under `app/`, `tools/`, `launchd/`, `frontend/src` or `frontend/scripts` models or says "vault"/"Obsidian" except the items listed under "What remains".

- **Wire:** `source_counts` is `{todoist, capacities, calendar}` (no `vault`). `artifact_source.VALID_ROW_SOURCES = {todoist, capacities}`: an artifact row tagged `source: "vault"` is an unknown source, so the artifact is `malformed` and degrades loudly (empty rows plus a banner naming `rows[i].source`), exactly like any other unknown source; producer rules naming it are invalid. `main`'s digest no longer folds vault-tagged artifact rows into the note-row surfaces. `_route_source_identity` gives a bare non-Todoist/non-Capacities path no identity (such a row is never a route match).
- **`runtime_actions`:** `system: "vault"` manifest handling, the `vault_path` target key, `VAULT_REMOVED_VERBS`, `REMOVED_STEP_KINDS` and the "vault is retired" error are gone. A source-less target (e.g. a stale manifest row) takes each verb's ordinary "no ... source" refusal (422 from the route, no journal entry); `drop_from_plan` and `defer` still work (keyed by name). An old journal step of kind `vault.*` loads, and its undo fails as `undo_failed` ("unknown step kind"). `deferrals.record` steps now say `surface: "runstate"`. `deferrals` stops reading `vault_path`; an existing file with `path:` keys loads and writes unchanged (test added).
- **`duration_memory`:** `normalize_identity` accepts only `todoist:<id>` for save/reset/lookup; `legacy_ok=True` (used only when reading/rewriting the cache file) still passes `vault:` and `/`-path keys verbatim, so an existing file never crashes and its legacy keys survive writes. `item_identity` no longer yields a path identity.
- **Fixtures:** all `contract-fixtures/*.json` regenerated with `TDTB_FIXTURE_DATE=2026-07-24` (the fixtures' original day). No file contains `vault`; `commit-live-*.json` carry only `todoist`/`calendar` surfaces. The capture script now drives a scratch home only (`cf.isolate(tmp)`: scratch `TDTB_HOME`/repo config, live mode, a minimal planning artifact supplying the habits block, scratch paths masked). The three note-backed samples (Make, Sample Press, Sample Project) are Todoist tasks; the sample calendar event is "Team Standup". Non-vault catch-up the regeneration pulled in (the fixtures were stale before V1): `artifact`, `exclusion_policy`, `prompt_optins`, `calendar_decisions`, `dropped_today`, `day_semantics` additions, `capacity.work_*`, calendar block identity fields, `overlap_grants`, `verify_details`, `diagnostics`, `rejected_proposal`, `optins`; the removed `captures` commit surface; `Overlap Permissions._body` now includes its own heading (an artifact of the test-only markdown converter). `scratch_integration.py` / `scratch_preview.py` called the deleted `build_vault` and never isolated `TDTB_HOME`; they now use `cf.isolate`.
- **Frontend:** `wire.test.ts` keeps one unknown-source fallback test; the vault-specific tolerance tests were rewritten for the regenerated fixtures; `badge--ucrit/uhigh/umed/ulow/unone` CSS deleted. Bundles rebuilt (`mockups/cockpit`, `app/static/cockpit`).
- **Legacy UI:** `app/legacy-static` (wizard SPA) was dead: not served (`/static/legacy.html` is a 404, pinned), referenced only by its own node tests. Deleted with `app/tests/js/*` and `test_js_logic.py`.
- **What remains:** the Historical banners and this plan; `vault_enabled` in `app_config.SOURCES_LEGACY_IGNORED_KEYS` (+ its tests); `duration_memory` legacy `vault:` key tolerance; the external skill name `tdtb-bridger-vault` (README, `config_reader`, `judgment`, `sequence`, `micro_adventure`, `runstate` docstrings/prompts); `AGENTS.md` (operator-owned, untouched); the superseded root `2026-08-12-tdtb-project-extraction.md`; `app/tests/config_markdown.py` (kept; 20 modules use it); inert test fixture/file names (`vault` tmp-dir fixtures, `vault_root` temp dirs, test names: ~63 files under `app/tests`, `app/gather/tests`, `frontend/src`).
- **Operator steps:** none. Restart picks up the new backend; an existing `duration-memory.json` / `deferrals.json` / runtime journals with legacy keys load as before. If a producer ever emits `source: "vault"` rows the artifact will show the malformed banner until regenerated.

## Inventory (from the 2026-10-12 read-only map)

Reads: `config_reader.py` (bridger.md sections), `micro_adventure.py` pool, `tdtb_gather.py` note scan,
daily notes (`main.py` live probe, `commit.py`, `shadow.py`), `schema_guard.py` `.base` hashes,
`TDTB_VAULT_ROOT` in `main.py`, `shadow_run.py`, `bake_in_run.py`, `commit_run.py`, `build_commit_body.py`,
launchd plist, `restart-live.sh`, `bootstrap-seat.sh`.
Writes: micro-adventure log, duration memory, runtime journal, `active-inventory.md`,
`tdtb-precompute-cache.md`, commit note mutations, `vault.trash`.
Surface: `Source = "vault" | ...` in `frontend/src/model/types.ts`, `wire.ts`, `Queue.tsx`, strings in
`ActionDock.tsx`, `BlockEditor.tsx`, `CapacitiesSettingsPanel.tsx`, `CapacitiesSourceEditor.tsx`,
`ConnectionsScreen.tsx`; about 12 contract fixtures.
Tests/docs: 73 of 99 `app/tests` files mention vault; most are deleted or rewritten with their slice.
