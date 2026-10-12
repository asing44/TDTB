# TDTB: remove the Obsidian vault entirely (S6 finish)

Status: **V1 in progress.** Operator directive (2026-10-12): anything "vault" in the Obsidian sense is
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
| **V1** | `config/tdtb.json` seeded from the live config; `app_config` loads repo file then local override; `config_reader.read_config` stops reading `00 - META/Skill-Configs/tdtb-bridger.md`; micro-adventure pool read from config (fallback pool stays in code). Schema Reference ids and Color Palette move into the same file. |
| **V2** | Remaining vault-written runtime state moves to the state dir: micro-adventure log, duration memory (+lock), runtime journal. Vault fallbacks for already-moved stores (runstate, recent selections, exclusion/capacities settings, deferrals, source mapping) are removed. |
| **V3** | Delete Commit's vault steps (`write_daily_note`, `write_frontmatter_flips`, `vault.trash`, orchestrate/shadow "vault" surface). Replace the `### Live` daily-note probe with state-dir done state. |
| **V4** | Delete the vault gather, `schema_guard` + `.base` mirror, `active-inventory`/precompute caches, `vault_enabled`, `TDTB_VAULT_ROOT`, `resolve_vault_root`, `tools/migrate_vault_*`, and the launchd/restart/bootstrap vault plumbing. |
| **V5** | Wire and UI: drop the `"vault"` source, `source_counts.vault`, `system: "vault"`, the VAULT chip and every user-facing "vault" string; update contract fixtures. |

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
