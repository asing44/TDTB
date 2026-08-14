# TDTB — Canonical Project Root

**Project-ID:** `TDTB`

This directory is the canonical working directory for TDTB Codex/OpenCode
tasks. Project rules live in [`AGENTS.md`](AGENTS.md); this README is the
source map and lifecycle boundary, not a second instruction file.

## Source map

| Surface | Canonical location | Role |
|---|---|---|
| Project policy | `Projects/TDTB/AGENTS.md` | GPT-first architecture, safety, testing, and completion rules |
| Active app (temporary) | `Tasks/tdtb-app-pilot/` | Current FastAPI + frontend implementation until Phase 2 promotion |
| Canonical skill | `Skills/user/tdtb-bridger-vault/SKILL.md` | Shared TDTB behavior source |
| GPT skill variant | `Configurations/gpt-stack/skills/tdtb-bridger-vault/SKILL.md` | Codex/OpenCode-compatible skill surface |
| Historical host plan | `Plans Link/2026-07-17-tdtb-cockpit-overhaul.md` (Claudius repo) | Host-owned historical evidence of the qualification, bake-in, and retirement gates; not a canonical plan |
| Historical material | `Projects/TDTB/archive/skill-era/` | Preserved pre-cockpit design and skill-era references; not authoritative |

Plans and acceptance evidence for this repo live under `docs/plan/`. The
cockpit plan referenced above is host-owned history, preserved in the Claudius
repo's `Plans Link/`. Installed launchd files, restart links, and other
machine-local wiring are projections, not project source.

## Current boundary

Phase 1 establishes this project boundary and keeps the existing app in place.
Phase 2 may move the app only after the historical host plan's gates are
evidenced (T29's attended real-sequence review, T15 wizard retirement, T16
governing-document reconciliation), a clean current test baseline, and no
overlapping TDTB claim. The other seat remains pending until it independently
verifies its own dependency, bootstrap, restart, and read-only state.

The old root reference at `References/tdtb-master-reference.md` is now a
redirect to live sources; its snapshot is preserved in the archive.
