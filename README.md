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

After a successful run, one symlink is installed in `~/.local/bin/`:
- **`tdtb-bootstrap`** → `bootstrap-seat.sh` (run from any directory)

The global **`tdtb-restart`** launcher is owned by HQ
(`Configurations/gpt-stack/seat-bootstrap.sh`), not by TDTB bootstrap.
TDTB owns the app-specific `restart-live.sh` (attended restart) and the
`launchd/com.walle.tdtb.plist` template. Installed launchd plists, restart
links, and other machine-local projections are not repo source.

You can run `tdtb-bootstrap` from anywhere after the first bootstrap completes.

| Flag | Behaviour |
|---|---|
| *(none)* | Validate paths; create `app/.venv` (uv + Python 3.12); `npm ci` in `frontend/`; symlink `~/.local/bin/tdtb-bootstrap` → `bootstrap-seat.sh`. Dependency freshness is tracked by SHA-256 marker files (`.tdtb-req-hash`, `.tdtb-lock-hash`); stale or missing markers trigger reinstallation. |
| `--launchd` | Also stage `~/Library/LaunchAgents/com.walle.tdtb.plist` from the canonical launchd template. Substitutes `__WALLE_HOME__` and `__TDTB_REPO__`. Does **not** activate launchd or restart `:8746`. |
| `--dry-run` | Read-only: report what would change without creating venvs, node_modules, symlinks, plist files, or hash markers. |

**Per-seat local dependencies** (not installed by bootstrap):
- **uv** (any version supporting `--python 3.12`)
- **Node.js** + **npm** (used by `npm ci`)
- **plutil** (macOS built-in; needed by `--launchd`)
- EventKit permission, Todoist credentials, and the OpenRouter provider
  credentials needed for live calls. Provider credentials are separate from
  the non-secret judgment-model selector (`judgment-model.txt`).

**Single-writer constraint:** Only one seat may boot the live `:8746` service.
`bootstrap-seat.sh` does not activate or restart the service; use
`restart-live.sh` (or the `tdtb-restart` symlink) for that, and confirm port
availability before doing so. Run `tdtb-bootstrap` from any directory to
re-validate and update seat projections after a repo checkout or worktree
switch.

**Tests:** `app/tests/test_bootstrap_seat.py` — fixture-based, fakes uv/npm,
uses a temporary HOME/repo, never touches real infrastructure.

## Judgment model selector

The judgment model is chosen by one plain, non-secret file at the repo root:
`judgment-model.txt`. Its **first active line** holds the OpenRouter model id,
for example `deepseek/deepseek-v4.1-flash`.

Blank lines and lines starting with `#` are ignored. Any line after the model
id is an optional parameter in `key: value` (or `key=value`) form. The original
single-line file remains valid and means "built-in defaults".

```
deepseek/deepseek-v4.1-flash
# reasoning_effort: low
# max_tokens: 8192
```

**To change model or parameters (operator, attended):**

1. Edit and save `judgment-model.txt`.
2. Run `tdtb-restart`.
3. `GET http://127.0.0.1:8746/health` — `judgment_model` shows the live model
   and `judgment_params` shows the active (non-secret) parameter set.

Model precedence, highest first:

1. a nonempty `TDTB_JUDGMENT_MODEL` environment variable, returned verbatim
   (explicit operator override, unchanged legacy behavior; whitespace-only
   counts as nonempty and does not fall through);
2. the repo-root `judgment-model.txt`;
3. the built-in default (`deepseek/deepseek-v4.1-flash`) when the file is
   absent (legacy/synthetic copies).

Parameters follow the **active** model, so they also apply when
`TDTB_JUDGMENT_MODEL` overrides the model.

### Parameters

Each key maps to one OpenRouter request field. **An unset key means no change
from today's payload** — the built-in defaults (including `temperature: 0.2`
for non-`openai/*` models and reasoning-off for `minimax/minimax-m3`) are
untouched. An explicitly set key wins, including where it overrides a built-in
default or the `openai/*` temperature omission — the operator owns that risk.

| Key | Range / meaning | Sent as |
|---|---|---|
| `reasoning_effort` | one of `none`, `minimal`, `low`, `medium`, `high`, `xhigh` | `reasoning: {effort: …}` |
| `max_tokens` | integer `1`–`131072` | `max_tokens` |
| `temperature` | float `0`–`2` | `temperature` |
| `top_p` | float greater than `0`, at most `1` | `top_p` |
| `seed` | integer | `seed` |
| `stream` | `true` / `false` (default `false`) | `stream` |

Notes:

- `max_tokens` too low truncates the strict JSON response, so a judgment call
  can fail validation; only narrow it deliberately.
- `reasoning_effort` support depends on the model and the selected provider —
  verify it on that model's OpenRouter API page. This file accepts the syntax;
  it does not qualify the model, and a rejecting provider fails the call.
- The selector is read once at server startup: an edit needs a restart, and
  changing it makes no call and does not touch credentials or the billed-call
  ledger.
- These parameters do **not** fix, and do not claim to fix, the known SEQUENCE
  timeout; `TDTB_SEQUENCE_TIMEOUT_S` governs that ceiling separately.

Validation is syntax only: an accepted id is **not** a claim that the model
is available, affordable, or correct. Pick an OpenRouter model that supports
strict `response_format` JSON-schema output — the payload pins
`provider.require_parameters` and a strict schema, so an unsupported model or
provider fails at call time rather than degrading silently. The current
default (DeepSeek V4.1 Flash) is unchanged by this mechanism.

The file is read at startup, but note the first attended `tdtb-restart` after
this change re-renders the installed
`~/Library/LaunchAgents/com.walle.tdtb.plist` from the canonical template,
which no longer pins a model. Until that restart the installed plist's old
`TDTB_JUDGMENT_MODEL` pin still overrides the selector file; the agent does not
perform this restart.
