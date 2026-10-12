# TDTB: remove the Obsidian vault entirely (S6 finish)

Status: **V1 and V2 landed (merged to main); V3 landed on branch `TDTB/vault-removal-v3-20261012` (not merged); V4 and V5a open elsewhere (V5a landed on branch `TDTB/vault-removal-v5a-20261012`, not merged); V5b open.** Operator directive (2026-10-12): anything "vault" in the Obsidian sense is
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
| **V4** | Delete the vault gather, `schema_guard` + `.base` mirror, `active-inventory`/precompute caches, `vault_enabled`, `TDTB_VAULT_ROOT`, `resolve_vault_root`, `tools/migrate_vault_*`, and the launchd/restart/bootstrap vault plumbing. |
| **V5** | Wire and UI: drop the `"vault"` source, `source_counts.vault`, `system: "vault"`, the VAULT chip and every user-facing "vault" string; update contract fixtures. |

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

**V5a as landed** (frontend half of V5; branch `TDTB/vault-removal-v5a-20261012`, not merged). Nothing under `frontend/src` models or says "vault" outside the stale contract fixtures and the tolerance tests/comments.

- Types: `Source = "todoist" | "capacities"`; `ManifestEntry.system` is `"todoist" | "calendar"`; `sourceCounts` is `{todoist, calendar}`.
- Tolerance (backend still emits the legacy fields until V5b): `projectAssigned` maps an unknown or legacy source (incl. `"vault"`, or none) to a **neutral Todoist-like row**: `source: "todoist"`, `path: null`, no `todoistId`, `identity: null`. It is unidentified, so no source-specific verb or duration-memory save can address it. `source_counts.vault` is ignored; a `system: "vault"` shadow/report entry copies through and renders without crashing (ApprovalDrawer totals show only todoist/calendar).
- Rendering: `sourceChip` fallthrough is a neutral `OTHER` chip (`.qrow__src--other`); `sourceContext` is labels only; `typeToken`/`sourceDetail` and the `VAULT_TIERS` stringified-list urgency parser are deleted (Capacities rows carry `urgency: null`; non-Todoist urgency shows verbatim, untiered); the dead `badge--u*` urgency badge in Queue is removed (its CSS rules remain); export prompt drops `(vault . path)` and the "create in PHEP" line (unrecognized rows read `(unidentified)`).
- Strings: ActionDock, BlockEditor, CapacitiesSettingsPanel, CapacitiesSourceEditor, ConnectionsScreen reworded ("the source mapping").
- Fixtures: scenario rows converted (Magic Mirror, Rowe's, Press, Entryway Design -> Todoist with priority ints so bands are unchanged; Note Processing, Deep CWEAN -> Capacities); shadow fixture lost its two vault rows (the "ready" preview now stages 5 exact writes, not 6).
- Left for V5b: delete `source_counts.vault`, vault `system` rows and any `source: "vault"` emission in `app/`; regenerate `frontend/src/adapters/contract-fixtures/*` (about 12 files carry `source_counts.vault`/vault rows, `shadow-diff.json` has `system:"vault"`, `commit-live-*.json` still show removed surfaces) and then tighten `wire.test.ts` (the legacy-tolerance tests can become plain fallback tests); delete the dead `.badge--ucrit/uhigh/umed/ulow/unone` rules in `app.css`; `app/legacy-static/*` (outside the frontend tree) still has chip-vault, `'vault'` source fallbacks and "writes to Todoist, the vault, and the calendar" copy.

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
