# TDTB — Design Document

> Running record of architecture decisions, known issues, and version history.
> This is the "why" layer. Behavioral corrections go in `~/claude-corrections/tdtb-bridger.md`.
> Per-day run logs go in `Tasks/tdtb-YYYY-MM-DD.md`.

---

## Pipeline Architecture

### v6.0 — Interactive Day Design (current)

```
User invokes tdtb-bridger-vault
  → Phase 0: Gather (silent — BusyCal, Todoist, Obsidian)
  → Phase 1: Configure Defaults (replaces Coda "Ye ol" dashboard)
  → Phase 2: Digest Artifact (rendered HTML — calendar + tasks + vault context)
  → Phase 3: Curation Loop (conversational selection with iterative re-renders)
  → Phase 4: Sequence (auto-sequence into windows, adjustable)
  → Phase 5: Commit (Todoist due_string + vault ## Plan)
  → BusyCal renders timed Todoist tasks as calendar blocks
```

**Retired in v6.0:** Coda staging canvas, Apple Shortcut TDTB trigger,
Make.com PUSH scenario 4486190. The entire Coda → Shortcut → Make.com
middle layer is replaced by Phases 1–3 (configure defaults, rendered
digest, conversational curation).

### Key invariants

- Todoist is the source of truth for schedulable blocks.
- BusyCal renders from Todoist times — no direct calendar writes.
- Skill writes `due_string` only. Never touches name, priority, labels, project.
- Caps (deep/mixed) are enforced conversationally during Phase 3 curation. Advisory, not blocking — user has final say.
- Obsidian vault efforts are loosely linked to Todoist — no Efforts → Tasks pipeline. Vault provides context for selection, not schedulable items.

### Pre-v6.0 pipeline (retired)

```
0. Kickoff (tdtb-bridger skill) — read-only brief
1. TDTB Shortcut (Apple Shortcut) → Coda staging → Make.com PUSH
2. Sequence (tdtb-bridger skill) — re-times pushed tasks
3. BusyCal — renders timed tasks
```

---

## Block Budget Caps (v5.0)

| Type | Cap | Field in Coda |
|------|-----|---------------|
| Efforts (Deep) | 4 | `deepCap` |
| Tasks (Mixed) | 3 | `mixedCap` |

Cap counters (`deepUsed`, `mixedUsed`) are live fields on the Backend canvas. Stage button disables when cap is hit — though button-disable enforcement is partial (some Stage actions can bypass it).

**Pending:** Stage button `Disable when` formula needs to include cap enforcement:

```
Or(
  Staged = true,
  [Assigned blocks] <= 0,
  AND(Type = "Efforts", deepUsed >= deepCap),
  AND(Type = "Tasks", mixedUsed >= mixedCap)
)
```

---

## Known Issues & Status

### Stale PUSH times on bumped/all-day tasks
**Observed:** 2026-04-29 run. Holly crate (bumped) and Meditation, Pondering (all-day) showed as timed blocks in BusyCal because Make.com PUSH had already written a default time. The skill's "no write for bumped/all-day" policy leaves those stale times in place, creating phantom calendar blocks.

**Fix (2026-04-29):** Phase 4 now writes `due_string: "today"` (no time) for any
bumped or all-day task where `due.datetime` is present (detected during Phase 2
partitioning via `has_stale_time: true`). Logged as `stale_time_cleared` in the
run log. Completion summary updated to include stale-clear count.

**Status:** Resolved.

---

### API sync lag on completed tasks
**Observed:** 2026-04-29 run. QT returned as `checked: false` from Todoist API but had already been completed by the user.

**Current workaround:** Inline completion at confirm gate — respond `yes, QT is done` to strip it from the write list without calling `complete-tasks`. User handles Todoist completion themselves.

**Status:** Workaround codified in skill (Phase 3 confirm prompt). Root cause (API sync lag) is not fixable at skill level.

---

### Calendar/Todoist task deduplication
**Observed pattern:** Tasks like "Press" or "Foods" may correspond to same-day calendar events. Without dedup, the skill assigns them window time that their calendar counterpart already covers.

**Current fix:** Covered-by-calendar dedup added in Phase 2 step 5a (v5.0). Fuzzy match on 2+ shared meaningful nouns. Matched tasks → `coveredTasks` bucket, no write.

**Status:** Implemented. Not yet observed in live run — will tune threshold if false positives appear.

---

## Version History

### v6.0 — 2026-05-08
- **Paradigm shift:** Replaced staged-batch pipeline (Coda → Shortcut → Make.com → sequence) with interactive conversational day design
- Retired: Coda staging canvas, Apple Shortcut TDTB trigger, Make.com scenario 4486190
- Single unified flow replaces three separate commands (kickoff, prep, orphan check)
- Added Phase 1: Configure Defaults — standing block toggles + capacity calculation (replaces Coda "Ye ol" Meta Settings)
- Digest rendered as HTML artifact with context layers: day shape, yesterday's outcome, vault context (summits, assigned, attention, intervals, orphans), Todoist pool
- Curation via conversational loop with iterative full-artifact re-renders
- Calendar sources expanded: ⬜ Blocks, 💡Thinkies, 🙋‍♂️ Personal, 🎂 Birthdays/Anniversaries, 🍯 A+M, 🟡 Trinoor, 🏠 Family, iCloud subscription
- Todoist filter broadened beyond due/overdue (exact filter TBD during first runs)
- Obsidian vault provides context (summits, assigned, attention, orphans) — loosely linked, no Efforts → Todoist pipeline
- Orphan check absorbed into digest artifact Layer 3 (no longer a standalone command)
- Vault effort assignment via `dates` frontmatter designed but deferred (build separately)

### v5.0 — 2026-04-29
- Bridger repositioned as post-PUSH sequencer (previously tried to do full scheduling from scratch)
- Block budget caps introduced: `deepCap=4`, `mixedCap=3`
- Over-cap bump policy: surplus tasks bumped by ascending priority
- `globalBufferPercentageNormal` raised 0.16 → 0.19
- `globalEnd` set to midnight (was 12:30 AM)
- Effort floor: minimum 2 blocks for `Type=Efforts` (soft)
- Make.com STATICS branch re-enabled
- Cap counter widget added to Ye ol dashboard
- Kickoff command added: 7-day Markdown brief, read-only, runs before Shortcut
- Kickoff changed from HTML artifact to structured Markdown output
- Pipeline Position section updated: Coda + Make.com collapsed under "TDTB Shortcut (Apple Shortcut)"
- Covered-by-calendar dedup added (Phase 2, step 5a)
- Inline completions added to confirm gate ("yes, QT done")
- Corrections-check block added (reads `~/claude-corrections/tdtb-bridger.md`)

### Pre-v5.0 (legacy)
- Skill tried to assign times to all tasks from scratch rather than re-timing PUSH output
- BusyCal Blocks calendar (⬜ Blocks) used for calendar writes — retired in v5.0
- Tana was primary source for schedulable items — retired, now context-only
- `due_datetime` used for writes — silently no-ops via MCP wrapper; replaced with `due_string`

---

## Schema Reference

### Make.com PUSH
- Scenario ID: `4486190`
- Default time written: today 2:00 PM (overridable via `start pull` field)

### Todoist
- Inbox project ID: `6M92PWG3HHJgQvfp`
- Duration emoji labels: 🚀10min · 🍅30min · 🏃‍♂️60min · 🐢90min · 🪨120min
- Lock label: `🚙Humming` — task is a fixed commitment, immovable

### Google Calendar
| Calendar | Purpose |
|----------|---------|
| 🟡 Mint / Trinoor | Work meetings (import) |
| 🍯 A + M Busy Bees! | Shared with Megan |
| 🙋‍♂️ Personal | Personal events |

### Tana
| Entity | ID |
|--------|-----|
| Workspace | `pr3I62E4Gd1w` |
| `#efforts` tag | `lS0GKXZ7JxTa` |
| `#todo` tag | `g8XxDrKf-ERe` |

---

## Skill File Locations

> **Note (2026-06-06):** As of 2026-06-05 the local CLI path is symlinked to the
> repo — `~/.claude/skills/<slug>` → `Skills/user/<slug>/` — so there is no
> separate manual deploy step for CLI sessions, and the live skill slug is
> `tdtb-bridger-vault`. The table below reflects the older three-surface model
> and is retained for historical context.

| Copy | Path |
|------|------|
| Source of truth (repo) | `~/Development/Claudius/Skills/user/tdtb-bridger/SKILL.md` |
| Corrections | `~/claude-corrections/tdtb-bridger.md` |
| Per-day run logs | `~/Development/Claudius/Tasks/tdtb-YYYY-MM-DD.md` |
