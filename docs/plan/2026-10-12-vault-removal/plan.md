# TDTB: remove the Obsidian vault entirely (S6 finish)

Status: **V1 landed on branch `TDTB/vault-removal-v1-20261012` (not merged); V2-V5 open.** Operator directive (2026-10-12): anything "vault" in the Obsidian sense is
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
| **V2** | Remaining vault-written runtime state moves to the state dir: micro-adventure log, duration memory (+lock), runtime journal. Vault fallbacks for already-moved stores (runstate, recent selections, exclusion/capacities settings, deferrals, source mapping) are removed. |
| **V3** | Delete Commit's vault steps (`write_daily_note`, `write_frontmatter_flips`, `vault.trash`, orchestrate/shadow "vault" surface). Replace the `### Live` daily-note probe with state-dir done state. |
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
