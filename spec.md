# TDTB App Pilot — Standalone Local Web App

**Status:** SPEC — approved direction 2026-07-12, build is a follow-on session.
**Origin:** cloud-Cowork incompatibility diagnosis (2026-07-12): `tdtb-bridger-vault`'s
deterministic pipeline needs Python/shell the cloud surface lacks, forcing token-expensive
model hand-execution. Decision: promote TDTB to a standalone local app; skill retires to a
thin fallback after bake-in. Both skills carry desktop/CLI-preferred markings in the interim.
**Background (not this spec):** `Tasks/tdtb-bridger-redesign/spec.md` (in-skill redesign),
`Plans Link/2026-07-01-universal-precompute.md` (cache contract), skill source at
`Skills/user/tdtb-bridger-vault/SKILL.md` (2,600+ lines — the behavioral contract this app
implements).

---

## 1. Shape

Single local process on the Mac (either machine — resolve vault path from config, never
hardcode `/Users/adam`): **Python 3.12 + FastAPI** backend serving a **single-page browser
UI**, with **Claude Agent SDK** calls for the judgment phases. Launched on demand
(`tdtb` shell alias or launchd on-demand socket); binds `localhost` only.

| Layer | Tech | Role |
|---|---|---|
| Backend | FastAPI, one process | vault I/O off disk, Todoist REST, calendar writes, cache maintenance, run-state |
| Gather module | absorbed `tdtb_gather.py` (805 lines) | pool/assigned build, `.base` predicate eval, priority scoring, inventory cache |
| Judgment | Claude Agent SDK (Python), API-key billed | audit report, digest suggestions, free-text adjustments, sequencing proposal |
| Frontend | vanilla JS/HTML served by FastAPI; port of `artifact-phase1.html` + new views | config, digest, adjustments, timeline, commit review |

**Cost note:** SDK calls bill API tokens (new spend), replacing plan-usage tokens the skill
burns today. Judgment calls are bounded (§ 5 acceptance: ≤4 per run) on a Sonnet-tier default
model (`claude-sonnet-5`), configurable. Estimated per-run cost: cents, vs the skill's
5-figure-token Cowork runs against the 5-hour cap.

## 2. Pipeline mapping (skill phase → app component)

The skill's phase semantics are the contract; the app reimplements the same invariants.

| Skill phase | App owner | Invariants preserved |
|---|---|---|
| 0 Gather | Backend gather module | absorbs `tdtb_gather.py` verbatim logic (`passes_base_filter`, `is_assigned`, `compute_priority_score`, `build_cache`, `walk_vault`); existing TDD tests move with it and must pass |
| 0b Non-vault reads *(added 2026-07-14 — gather-parity realign; the original table silently left these unowned and the pilot shipped vault-only)* | `external_sources.py` + `/plan-inputs` merge | skill Phase 0 Batch A parity: Todoist assigned/quick pulls (v1 filter *queries*, not saved-filter IDs — those stay write-path), calendar busy blocks via EventKit read (own zone-write calendars excluded), habits capacity summary (never digest items, per skill SOT). Every source degrades to empty + a `source_warnings` entry rendered loudly — never silent vault-only. Daily-note / recent-selections context reads land during bake-in (plan H2) |
| 0.5 Pool/assigned build | Backend | `.base` predicate eval verbatim from `assignment-pipeline.base#assignables` + `daily-assigned.base` — never a parallel hand-rolled filter |
| 0.9 Pipeline audit | Agent SDK call #1 | non-blocking report card in UI |
| 1 Configure defaults | Frontend config view | direct port of `artifact-phase1.html` (toggles, steppers, capacity bars); presets from vault config `## Presets` |
| 1 Day Setup *(added 2026-07-14 — ui-parity T4/T6)* | `index.html` Day Setup view + `POST /day-setup` + `time_engine.py`/`capacity.py` via `/plan-inputs` | anchor = live clock rounded UP to `anchor.round_to_minutes` (override wins); effective-EOD hard-stop scan (2h window); canonical 6-segment capacity math (signed Free, OVERASSIGNED advisory); anchored past-window default-skip with operable re-include (`re_included` derived server-side once); state is runstate-backed, session/day-scoped — never written to vault config |
| 1b Schedulable injection *(added 2026-07-14 — ui-parity T5)* | `external_sources.build_schedulable_blocks` + `/sequence` server-side injection | Mint sessions are stable 30-minute choices inside configured Trinoor windows and Day Setup selects the date's sessions; selected Mint rows stay inside their windows; QT absorbs `@🚀10min` into `qt_contents` (contents never individually timed); Shivery default Off; 🟡 Trinoor zone rows are permeable backdrop (Step D′) — never subtractive, exempt from validation |
| 2 Digest + suggest | deterministic digest (backend) + Agent SDK call #2 for ≤5 suggestions | tiered pool ranking stays deterministic backend code |
| 3 Adjustments | Frontend row actions (complete/deassign/remove as buttons); Agent SDK call #3 only for free-text asks | real UI state replaces chat-command parsing |
| 4 Sequence | Agent SDK call #4 proposes; frontend timeline view with drag-adjust | zone/`latest_start` constraints validated client- AND server-side; metadata-derived parent/child overlap, same-start `#systems` grouping, unique same-activity calendar spans, and exact overlap grants are enforced by the sequencing prompt and server validation; **no morning workout blocks** enforced in both the sequencing prompt and server validation (standing rule; Press `before_work` exception only via TDTB config) |
| 5 Commit | Backend writers A–E from `plan_manifest` | strict UPDATE/CREATE partition invariant; pre-write calendar-ID assertion; parallel dispatch |

## 3. External surfaces

### 3.1 Vault (WALL·E-THNK, direct disk I/O)
Same paths as the skill: `50 - Operations/{Projects,Intervals,Pursuits,Adventures}`,
`30 - Daily/{MMM DD, YYYY}.md` (`# TDTB Plan` section patch — write-only), `00 - META/Habituals/`,
`00 - META/Skill-Configs/tdtb-bridger.md`. Frontmatter writes (`assigned: true`) preserve
key order and body bytes — reuse the write pattern from `tdtb_gather.py`'s cache writer, or
`python-frontmatter` with round-trip checks.

### 3.2 Todoist — REST API v2 direct
No MCP. Filters ⭐Today `2368117560`, ⚡Quick Tasks `2365541130`, 🥇First `2360031067`
(lazy 🥈Next `2360031248` / 🥉Then `2360031650`); PHEP project `6fgXPMw28j7cRFMH`.
**All vault efforts round-trip through PHEP** — hard constraint carried over. IDs read from
vault config, not hardcoded (they rotate; the skill's `id_rotation` correction machinery
exists because of this). Token from macOS Keychain or `~/.config/tdtb/env` (chmod 600) —
never in the repo.

### 3.3 Calendar — EventKit, not BusyCal-specific
BusyCal has no API; it reads the system calendar store. Use **EventKit via pyobjc**
(`EKEventStore`) to create/query events on the same calendars (`calendar_ids` map from vault
config). This is the chosen bridge — AppleScript/`osascript` rejected (fragile, slower, needs
BusyCal running). First run triggers macOS calendar-access consent for the Python binary.
Preserve the skill's guards: runtime `calendar_ids` resolution + pre-write ID assertion;
QT routes via Todoist, never calendar.

### 3.4 Config
Single source stays `00 - META/Skill-Configs/tdtb-bridger.md` (Presets, Schema Notes,
`calendar_ids`, `habits.source_directory`). App parses the same key/value tables with the
same inline fallbacks and the same bootstrap-if-missing behavior. **No new config surface**
except the local env file for secrets (Todoist token, Anthropic API key).

## 4. Shared contracts + drift guards

Per CLAUDE.md "every derived surface ships with a guard" — the app is a new derived surface
in three places:

1. **`00 - META/Cache/active-inventory.md` (v2)** — currently a 3-file contract (nightly
   scheduled task produces; inbox-parser + tdtb skill consume). App becomes the 4th party:
   it both consumes and (post-gather) rewrites it, exactly as `tdtb_gather.py` does today.
   Guard: hard `schema_version` check on read — mismatch = refuse + surface, never guess.
   Any shape change still lands across all four surfaces in one change.
2. **Vault-schema logic embedded in the app** (`.base` predicates, status enums, capacity
   vocab). Guard: app stores the sha of each `.base` source it compiled its predicate from;
   a `--check-drift` startup step (and a `vault-lint.py`-callable entry point) compares
   stored sha vs live file and warns on mismatch. Register the app in `Scripts/README.md`
   § Derived-Surfaces Registry in the same change that lands the app.
3. **Run-state compatibility.** App keeps writing `00 - META/Cache/tdtb-runstate-<today>.md`
   (incl. `plan_manifest`) and `tdtb-recent-selections.md` in the skill's format, so vault
   surfaces, the nightly task, and any fallback skill run stay interoperable during and after
   bake-in.

## 5. Acceptance criteria

- Full pipeline end-to-end on real vault data; commit writes verified identical-class to a
  skill run: Todoist retimes/creates, `# TDTB Plan` daily-note patch, `assigned: true`
  frontmatter flips, calendar events on the correct calendars (Minting 🟡 / Shivery ⬜ /
  Trinoor zones / Anchored blocks).
- Deterministic steps consume **0 model tokens**; ≤4 Agent SDK invocations per run.
- Morning-workout exclusion enforced (prompt + server validation).
- Absorbed gather module passes the existing `tdtb_gather.py` test suite unmodified.
- Wall-clock: gather < 5 s on full vault; whole ritual faster than a desktop-Cowork skill run.

## 6. Rollout / skill retirement

1. **Bake-in ≥1 week:** app runs alongside the skill; skill untouched. Compare commit
   outputs when both run on the same day.
2. **Acceptance** → shrink `tdtb-bridger-vault` to a thin launcher/fallback: "open the app;
   if unavailable, degraded conversational run." Full quality gates on the shrink
   (skill-validator, skill-reviewer + cowork-compat-reviewer, review-drift mark, cloud
   re-upload).
3. **Corrections triage:** carry still-relevant `Skills/corrections/tdtb-bridger-vault.md`
   entries into the app's issue tracking or the shrunken skill; the `id_rotation`
   auto-apply machinery becomes an app-side config validation.
4. **Cross-ref sweep** (`cross-ref-auditor`): weekplan-vault, shutdown-vault, the corrections
   vault mirror, and the nightly cache task all reference TDTB — update after the shrink.

## 7. Non-goals (pilot)

- inbox-parser app (follow-on if this lands; the Obsidian-plugin idea for it stays parked).
- Phone/remote access, multi-user, auth, packaging/signing/notarization.
- Replacing weekplan-vault or shutdown-vault.
- Any change to the nightly cache task or the active-inventory v2 shape.

## 8. Repo placement

App source: `Tasks/tdtb-app-pilot/app/` during pilot (promote to a proper home — likely its
own repo or `Projects/` — only after acceptance). Python venv local to that dir per repo
toolchain convention.
