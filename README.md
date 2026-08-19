# TDTB — Canonical Project Root

**Project-ID:** `TDTB`

This directory is the canonical working directory for TDTB Codex/OpenCode
tasks. Project rules live in [`AGENTS.md`](AGENTS.md); this README is the
source map and lifecycle boundary, not a second instruction file.

## Source map

| Surface | Canonical location | Role |
|---|---|---|
| Project policy | `Projects/TDTB/AGENTS.md` | GPT-first architecture, safety, testing, and completion rules |
| Canonical app (live) | Repository root (`app/`, `frontend/`, `launchd/`, `restart-live.sh`) | Current FastAPI + frontend implementation running the live `:8746` runtime |
| Seat bootstrap | `bootstrap-seat.sh` | Portable per-seat dependency and launchd-plist setup (see § Seat bootstrap below) |
| Rollback fallback (preserved) | Claudius `Tasks/tdtb-app-pilot/` | Preserved rollback source only; not part of normal operation |
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

## Seat bootstrap

`bootstrap-seat.sh` is the authoritative machine-local bootstrap for both
`/Users/adam` and `/Users/walle-mini`. It resolves repo paths from its own
directory (or `$TDTB_REPO`) and never hardcodes a user home.

| Flag | Behaviour |
|---|---|
| *(none)* | Validate paths; create `app/.venv` (uv + Python 3.12); `npm ci` in `frontend/`; symlink `~/.local/bin/tdtb-restart` → `restart-live.sh`. Dependency freshness is tracked by SHA-256 marker files (`.tdtb-req-hash`, `.tdtb-lock-hash`); stale or missing markers trigger reinstallation. |
| `--launchd` | Also stage `~/Library/LaunchAgents/com.walle.tdtb.plist` from the canonical launchd template. Substitutes `__WALLE_HOME__`, `__TDTB_REPO__`, and `__TDTB_VAULT_ROOT__`. Does **not** activate launchd or restart `:8746`. |
| `--dry-run` | Read-only: report what would change without creating venvs, node_modules, symlinks, plist files, or hash markers. |

**Per-seat local dependencies** (not installed by bootstrap):
- **uv** (any version supporting `--python 3.12`)
- **Node.js** + **npm** (used by `npm ci`)
- **plutil** (macOS built-in; needed by `--launchd`)
- EventKit permission, Todoist credentials, and an
  `ANTHROPIC_CLAUDE_CODE` / `TDTB_JUDGMENT_MODEL` token for live operation

**Single-writer constraint:** Only one seat may boot the live `:8746` service.
`bootstrap-seat.sh` does not activate or restart the service; use
`restart-live.sh` (or the `tdtb-restart` symlink) for that, and confirm port
availability before doing so.

**Tests:** `app/tests/test_bootstrap_seat.py` — fixture-based, fakes uv/npm,
uses a temporary HOME/repo/vault, never touches real infrastructure.
