# TDTB — Master Reference

> **Generated:** 2026-05-08
> Consolidated from 8 source files in the Claudius repo. Each section cites
> its sources by number. This document is a snapshot — check living sources
> for current state.

## Source Files

| # | Path | Role |
|---|------|------|
| 1 | `References/tdtb-bridger-design-reference-2026-04-15.md` | April 15 design session compaction — origin story |
| 2 | `Projects/Tune/Efforts/TDTB/DESIGN.md` | Running design doc — architecture, version history, known issues |
| 3 | `Projects/Tune/Efforts/TDTB/tdtb-bridger-v5.1.md` | Retired v5.1 skill spec (Cowork variant) |
| 4 | `Configurations/tdtb-push-quarantine-gate.md` | QW1 quarantine gate spec for Coda → Todoist push |
| 5 | `Skills/corrections/tdtb-bridger-vault.md` | Behavioral corrections (living doc) |
| 6 | `Skills/observations/tdtb-bridger-vault-2026-05-08.md` | First live run observations |
| 7 | `handoff-tdtb-rewrite-2026-05-08.md` | Session handoff from v6.0 rewrite |
| 8 | `Skills/user/tdtb-bridger-vault/SKILL.md` | Current live skill spec — v6.0 interactive day design |

---

## 1. Origin & Problem Statement

*Sources: [1]*

The TDTB (Today's Daily Task Board) scheduling workflow spanned **7 steps
across 4 tools** (Tana → Apple Shortcuts → Coda → Make.com → Google Calendar
→ BusyCal) and took 30–45 minutes daily.

Three manual steps caused most of the friction:

1. **Tana → Coda bridge:** Copy-paste through Apple Shortcuts modal + AI
   title extraction [1]
2. **Block assignment in Coda:** Slider-per-item UI with no calendar
   awareness — you assign durations but get no guidance on placement [1]
3. **BusyCal rearrangement:** Dragging calendar blocks post-push with no
   sequencing logic [1]

The deeper structural problem: the system operates at the **effort level**
but daily work happens at the **todo level**. Calendar blocks say
"Enhancement" when the actual task is "Write test scripts for BP punchout
module." [1]

---

## 2. Architecture Evolution

### Initial Architecture — April 2026

*Sources: [1]*

**Chat skill over coded tool.** All MCP connections already wired in Chat
(Tana, Coda, GCal, BusyCal, Todoist, Make). Core value is intelligent
synthesis (fitting ranked items into calendar gaps), not automation. A
dashboard/artifact would duplicate Coda's formula engine. [1]

**Pipeline trim, not replace.** The skill was designed to eliminate manual
glue without replacing Coda or Make:
- Coda remains calculation engine (block math, 27 presets, staging) [1]
- Make remains push mechanism (PULL: 4456141, PUSH: 4486190) [1]
- Skill handles orchestration between them [1]
- Direct-to-Calendar write is opt-in; Coda is default write target [1]

**Three commands:**
- `prep my TDTB` — full planning pass: gather → synthesize → present → write [1]
- `sequence my blocks` — lighter: reads staged Coda items + calendar, proposes time order [1]
- `orphan check` — reads Attention node, surfaces items not in Assigned set [1]

**Tana as primary data source.** Assignment pipeline used search nodes on the
daily page: 🔥 First (`YH4ZTsWPTizE`), 2️⃣ Next (`uHo--wSffK6Y`), ⏳ Then
(`nX6kd1r5kbJL`), 🌀 Intervals (`TFZh24F1o-RE`), ✅ Push (`PRKxsrCjFpSO`). [1]

**Tier logic:**
- **First:** Active OR P4 OR due intervals. Excludes done/assigned/Summits/todo/inbox/shop. [1]
- **Next:** P3 OR (urgency+R3) OR stale Maintenance. Excludes ChildOf:First. [1]
- **Then:** Same as Next, lower tier. [1]
- **Push:** Assigned=Yes OR scheduled due intervals. Mixed types. [1]
- **Attention:** Overdue intervals + dates within 4d + P4 within 14d + active efforts + P4 inbox. [1]

**Critical distinction:** Source of truth is the **Assigned live query on the
daily page**, NOT Push. Push filters out completed items; Assigned retains the
full committed set. [1]

### v5.0–v5.1: Post-PUSH Sequencer — April 29–May 2

*Sources: [2], [3]*

Bridger repositioned as **post-PUSH sequencer** — it no longer tried to
schedule from scratch but instead re-timed tasks that Coda + Make.com had
already pushed to Todoist. [2]

Key changes:
- Block budget caps introduced: `deepCap=4` (efforts), `mixedCap=3` (tasks) [2]
- Over-cap bump policy: surplus bumped by ascending priority [2]
- `globalBufferPercentageNormal` raised 0.16 → 0.19 [2]
- `globalEnd` set to midnight [2]
- Effort floor: minimum 2 blocks for `Type=Efforts` (soft) [2]
- Kickoff command added: 7-day Markdown brief, read-only [2]
- Alignment check added: classify tasks by PUSH state (pushed / default-timed
  / unpushed). Replaces manual Coda alignment dashboard review. [3]
- HTML day-timeline artifact for plan visualization [3]
- Corrections check block loads `Skills/corrections/tdtb-bridger-vault.md` (originally `~/claude-corrections/tdtb-bridger.md`, path retired) [2]

**v5.1 pipeline position (4 steps):**
```
0. Kickoff (skill) — read-only 7-day brief
1. TDTB Shortcut (Apple Shortcut) → Coda staging → Make.com PUSH
2. Sequence (skill) — re-times pushed tasks
3. BusyCal — renders timed tasks
```
[3]

### v6.0: Interactive Day Design — May 8 (current)

*Sources: [2], [7], [8]*

**Paradigm shift.** Replaced the entire staged-batch pipeline (Coda →
Shortcut → Make.com → sequence) with a single interactive conversational
flow. [2][7]

**Retired:** Coda staging canvas, Apple Shortcut TDTB trigger, Make.com
scenario 4486190 (PUSH). The entire Coda → Shortcut → Make.com middle layer
is replaced by Phases 1–3. [2][7][8]

**Key decisions:**
- Single command, not three. Old kickoff/prep/orphan-check merged into one
  flow. Orphan data surfaced in digest artifact. [7]
- Obsidian and Todoist are loosely linked. No Efforts → Todoist pipeline.
  Vault provides context for selection; Todoist holds schedulable tasks. [7][8]
- Vault effort `dates` write path designed but deferred. [7][8]
- Iterative re-renders, not conversational edits. Full artifact re-renders
  after each decision. [7]
- Caps are advisory — surface over-budget state but don't block. [7]

---

## 3. Coda's Role (Historical)

*Sources: [1], [2], [3], [4]*

**Status: RETIRED as of v6.0 (2026-05-08).** [2][7][8]

### What Coda Was

Coda served as the **calculation engine** for the TDTB pipeline: block math,
27 presets, staging logic. [1]

- **TDTB v4.0 doc:** `coda://docs/rNdKvDlv8H` [1][4]
- **Primary grid:** `_todoist tasks` (`grid-TRbHAR8AJp`) [4]
- **Staging flow:** User staged tasks via the Coda UI. Stage button applied
  caps (`deepCap=4`, `mixedCap=3`) — though button-disable enforcement was
  partial; some Stage actions could bypass the disabled-if formula. [2]
- **"Ye ol" Meta Settings dashboard:** Configured standing block defaults per
  day. Replaced by Phase 1 in v6.0. [2][8]

### Coda Schema (at retirement)

Columns on `_todoist tasks` grid (`grid-TRbHAR8AJp`): [4]

| Column | Purpose |
|--------|---------|
| `Task id` | Todoist task identifier |
| `Title` | Task name |
| `Desc` | Task description |
| `Project id` | Target Todoist project |
| `start push` | Due date/time for push — **ghost task source when blank** |
| `duration amount` | Block duration |
| `Staged` | Checkbox — triggers inclusion in PUSH |
| `f.all day` | All-day flag |

Cap counters (`deepUsed`, `mixedUsed`) were live fields on the Backend
canvas. [2]

**Pending cap enforcement formula (never fully deployed):** [2]
```
Or(
  Staged = true,
  [Assigned blocks] <= 0,
  AND(Type = "Efforts", deepUsed >= deepCap),
  AND(Type = "Tasks", mixedUsed >= mixedCap)
)
```

### Make.com Integration

- **PULL scenario:** `4456141` — pulled data into Coda [1]
- **PUSH scenario:** `4486190` (team `91360`) — read staged rows from Coda,
  wrote `due_string` + duration to Todoist. Default time: today 2:00 PM
  (or `start pull` if present). [1][2][4]
- STATICS branch re-enabled in v5.0. [2]

### Ghost Task Bug

When `start push` was blank in Coda, Todoist's date parser emitted the
literal string "Unable to process input" — creating a malformed task that
surfaced in TDTB sequencing every run. [4]

The PUSH scenario had **no error handler** on row-driven push paths. [4]

### Quarantine Gate (QW1) — designed but never built

A validation gate designed to insert between `coda:listRows` and the Staged
router in Make.com scenario 4486190. Would route invalid rows to a
Quarantine view instead of pushing garbage to Todoist. [4]

Proposed Coda schema additions: `Quarantine` (checkbox), `Quarantine reason`
(text), `Quarantine at` (datetime). [4]

Companion daily alert scenario (`TDTB : Quarantine Alert`) would poll
quarantined rows at 07:00 daily and create a Todoist Inbox task if
non-empty. [4]

**Status:** Spec only. Never implemented. Moot after Coda retirement. [4]

### Why Coda Was Retired

The v6.0 rewrite replaced Coda's role entirely: [2][7]
- Phase 1 (Configure Defaults) replaces the "Ye ol" Meta Settings dashboard
- Phase 3 (Curation Loop) replaces Coda staging
- Todoist writes are direct from the skill — no Make.com middleman
- Open design question from [1]: "if unused after 2 weeks of bridger runs,
  simplify to direct Calendar writes" — this is what happened

---

## 4. Current Pipeline (v6.0)

*Source: [8]*

```
User invokes tdtb-bridger-vault
  → Phase 0: Gather (silent — BusyCal, Todoist, Obsidian)
  → Phase 1: Configure Defaults
  → Phase 2: Digest Artifact
  → Phase 3: Curation Loop
  → Phase 4: Sequence
  → Phase 5: Commit
```

### Key Invariants

- Todoist tasks are the source of truth for schedulable blocks. [2][8]
- BusyCal renders timed Todoist tasks as calendar blocks. [2][8]
- Skill writes `due_string` only. Never touches name, priority, labels,
  project. [2][8]
- Caps (deep/mixed) are advisory — enforced conversationally during Phase 3
  curation. [2][8]
- Obsidian vault efforts are loosely linked to Todoist — vault provides
  context, not schedulable items. [7][8]

### Phase 0 — Gather

Data sources pulled in parallel: [8]

| Source | Method | Purpose |
|--------|--------|---------|
| BusyCal | `query_events` across 7+ calendars | Fixed commitments, work windows, context |
| Todoist | ⭐ Today filter (`2368117560`) + ⚡ Quick Tasks filter (`2365541130`) | Schedulable tasks |
| Obsidian | `read_note` on efforts, summits, intervals, yesterday's plan, energy | Vault context |

**Calendar sources:** ⬜ Blocks, 💡 Thinkies, 🙋‍♂️ Personal, 🎂 Birthdays/
Anniversaries, 🍯 A + M Busy Bees!, 🟡 Trinoor, 🏠 Family, iCloud
subscription. [8]

**Megan-only filter (🍯 calendar):** Treat event as Megan's (context, not
fixed block) when title references Megan as actor, Adam not attendee/
organizer, or `transparency: transparent`. [5][8]

**Blocks calendar event types:** [5]
1. Large reference blocks — visual only (total work hours)
2. 🟡 Block 1/2/3 — pre-calculated work windows (available slots for sequencing)
3. ⬜ prefix events (Sudsing, Foods, Wind down, Full Stop) — soft lifestyle
   markers; don't block windows, flag overlaps

**Todoist filter queries:** [6][8]
- ⭐ Today: `(today | overdue | deadline before: in 14 days) & !(@◻️TDTB)`
- ⚡ Quick Tasks: `((@🚀10min | @📘Obsidian | @🔀Coda | @📝Tana) & (today|overdue)) | ((@🚀10min | @📘Obsidian | @🔀Coda | @📝Tana) & no date)`

**Vault queries:** [8]
- Assigned efforts: `50 - Operations/Tracking/Efforts/` where `status: active`
  AND `dates` contains today
- Summits: active, sorted by urgency DESC, deadline ASC
- Attention: `deadline < today`
- Intervals: active items from `50 - Operations/Tracking/Intervals/`
- Yesterday's plan: `30 - Daily/[yesterday].md` → `## Plan`
- Energy: today's daily note frontmatter `energy` field

**Daily note filename format:** `MMM DD, YYYY.md` (e.g., `May 08, 2026.md`). [8]

### Phase 1 — Configure Defaults

Replaces Coda's "Ye ol" Meta Settings dashboard. [2][8]

| Block | Default | Duration | Notes |
|-------|---------|----------|-------|
| Buffering | On (Minimal) | Variable | Standard / Minimal / Off |
| Minting | On | 2 blocks (1 hr) | Trinoor work blocks |
| Quick Tasks | On | 1 block (30 min) | Short task allocation |
| Live | On | Variable | Micro-adventure |
| Sudsing | On | 1 block (30 min) | Showering |
| Foods | On | 2 blocks (1 hr) | Meal prep/eating |
| Shivery Jigs | Off | Configurable | Friday night with Megan |
| Wind down | On | Timeless | End-of-day decompression |

**Habits special case:** 45 min flat estimate (sourced from `00 - META/
Habituals/` directory). Capacity deduction only — never written to Todoist or
calendar. [8]

Default block settings persist via **Claude memory**. [8]

### Phase 2 — Digest Artifact

HTML artifact with four context layers: [8]
1. **Day Shape** — anchor time, fixed blocks, available windows, capacity,
   energy
2. **Yesterday's Outcome** — prior plan items, carryover, overdue
3. **Obsidian Context** — summits, assigned, attention, intervals
4. **Todoist Pool** — tasks grouped by type/project, priority, duration

### Phase 3 — Curation Loop

Conversational selection with iterative full-artifact re-renders. [8]
- Toggleable rows: checkbox + name + priority + duration + source
- Live capacity bar (used / remaining blocks)
- Cap counters (deep: X/4, mixed: Y/3)
- Confirm button wired to `sendPrompt("curation confirmed")`

Cap enforcement is advisory. [8]

### Phase 4 — Sequence

Partition into locked / impromptu-locked / timed candidates / all-day. [8]
- Use 🟡 Block 1/2/3 events as available windows; fall back to raw gaps [8]
- Rank: priority (P1→P4), overdue > started > alphabetical [8]
- Place back-to-back; heavy blocks to largest windows [8]
- Won't fit → bumped with reason [8]
- Round anchor time UP to next 5-minute boundary [5][8]

### Phase 5 — Commit

Four write targets: [8]

**A — Todoist writes:** `due_string: "today at H:MMam/pm"` for each
scheduled task. [8]
- **NEVER** write `due_string: "today"` (no time) — converts timed tasks to
  all-day, BusyCal drops the block. [5]
- Bumped / unsequenced / all-day: NO writes. [5][8]
- New tasks from vault sources → PHEP project (`6fgXPMw28j7cRFMH`), apply
  `◻️TDTB` label. [6][8]

**B — Vault daily note:** Write `## Plan` section to `30 - Daily/MMM DD,
YYYY.md`. Never touch frontmatter, Captures, Journaling. [8]

**C — Vault effort assignment (designed, deferred):** Write today's date to
effort's `dates` frontmatter. Not yet built. [7][8]

**D — Default block calendar writes:** Create BusyCal events on ⬜ Blocks
calendar for each active default block except Habits. Title: `⬜ {block_name}`.
[6][8]

---

## 5. Schema Reference

### Todoist (current)

*Sources: [6], [8]*

| Entity | ID |
|--------|-----|
| PHEP project | `6fgXPMw28j7cRFMH` |
| Inbox project | `6M92PWG3HHJgQvfp` |
| ⭐ Today filter | `2368117560` |
| ⚡ Quick Tasks filter | `2365541130` |

**Duration labels:** 🚀10min · 🍅30min · 🏃‍♂️60min · 🐢90min · 🪨120min [3][8]
**Lock label:** `🚙Humming` — immovable task [3][8]
**TDTB label:** `◻️TDTB` — applied to tasks created by this skill [8]

### BusyCal Calendars (current)

*Source: [8]*

| Calendar | Calendar ID |
|----------|------------|
| ⬜ Blocks | `F188F3A2-7FD0-407F-B446-16593CD8DA92/65B2667F-39E9-4DDB-8369-218068A883D4` |
| 💡 Thinkies | `537BE631-2060-4E9A-8006-1C964F2B7606/65B2667F-39E9-4DDB-8369-218068A883D4` |
| 🙋‍♂️ Personal | `2BD66B44-5B6B-4EED-BB40-BE6BDBEFC556/01744EBB-98C5-4293-99EC-3B74FC3F824B` |
| 🎂 Birthdays / Anniversaries | `49B34679-DB11-494A-8B75-EB9BC49143F1/65B2667F-39E9-4DDB-8369-218068A883D4` |
| 🍯 A + M Busy Bees! | `ADC84BF5-63EF-446C-A783-12E69A121019/65B2667F-39E9-4DDB-8369-218068A883D4` |
| 🟡 Trinoor | `6060DD94-6007-4961-A731-185FB877C92C/65B2667F-39E9-4DDB-8369-218068A883D4` |
| 🏠 Family | `677FECFD-5213-4D56-A344-8762C20D7E86/65B2667F-39E9-4DDB-8369-218068A883D4` |

**Gotcha:** 🏠 Family and 🎂 Birthdays/Anniversaries may share the same
calendar ID — verify with `list_calendars`. [7]

### Tana (historical — retired from active pipeline)

*Sources: [1], [3]*

| Entity | ID |
|--------|-----|
| Primary workspace (WALL·E-TANA) | `pr3I62E4Gd1w` |
| `#efforts` tag | `lS0GKXZ7JxTa` |
| `#todo` tag | `g8XxDrKf-ERe` |
| `#inbox` tag | `bcy0KvnupfDW` |

**Search node IDs (Assignment page `0831VAJoykJ2`):** [1]

| Node | ID |
|------|-----|
| 🔥 First | `YH4ZTsWPTizE` |
| 2️⃣ Next | `uHo--wSffK6Y` |
| ⏳ Then | `nX6kd1r5kbJL` |
| 🌀 Intervals | `TFZh24F1o-RE` |
| ✅ Push | `PRKxsrCjFpSO` |

**Schema fields:** [1]

| Field | ID | On | Notes |
|-------|-----|-----|-------|
| Assigned | `AvWtw567117k` | _assigned base tag | Checkbox — surfaces item in TDTB |
| Part of | `oFJM-sfoHPBD` | #todo | Routes to parent effort/pursuit |
| In progress | `lpap26XrwDht` | #todo | Priority boost signal |
| Urgency | `Z0g4wjJdPkTx` | #efforts, #todo | P4–P1 |
| Return | `zEp_FvSoa99S` | #efforts | R4–R1 |
| Status | `STu5kqlYYmxr` | #efforts | Active, Queued, Dormant, Finished, Archived, Maintenance |
| Dates | `mOhz1bY8PsYq` | #efforts, #todo | Shared field ID |
| Deadline | `5yGPI03iuu5g` | #efforts, #todo | Shared field ID |

**MCP constraints discovered:** [1]
1. `supertag:tana_query` returns empty field values — field reads require
   individual `tana:read_node` calls
2. Orphan scan via MCP queries not viable — replaced with Attention node read
3. Todo discovery uses positional nesting (depth 2), not Part of queries
4. Legacy `Subitem of` field on some nodes — dead weight

### Coda (historical — retired)

*Sources: [1], [4]*

| Entity | Identifier |
|--------|-----------|
| TDTB v4.0 doc | `coda://docs/rNdKvDlv8H` |
| `_todoist tasks` grid | `grid-TRbHAR8AJp` |

### Make.com (historical — retired)

*Sources: [1], [2], [4]*

| Scenario | ID | Team | Purpose |
|----------|-----|------|---------|
| PULL | `4456141` | — | Pulled data into Coda |
| PUSH | `4486190` | `91360` | Pushed staged Coda rows to Todoist |

---

## 6. Known Issues & Corrections

### Active Corrections

*Source: [5]*

1. **🍯 A + M Busy Bees! shared calendar (2026-05-05):** Check whether events
   are Adam's or Megan's. Signals for Megan-only: title references Megan as
   actor, Adam not attendee/organizer, `transparency: transparent`. [5]

2. **CRITICAL — never clear PUSH-default times to all-day (2026-05-06):**
   Writing `due_string: "today"` (no time) converts timed tasks to all-day.
   BusyCal renders timed tasks as calendar blocks — removing the time makes
   them vanish entirely. The only valid write is `due_string: "today at
   H:MMam/pm"`. Bumped/unsequenced tasks: no write. [5]

3. **Round to 5-minute intervals (2026-05-05):** Anchor time not on boundary →
   round UP. Durations are always multiples of 5. [5]

4. **Blocks calendar event taxonomy (still valid):** 🟡 Block 1/2/3 = work
   windows; 🟡 Minting (long) = reference block only; ⬜ prefix = soft lifestyle
   markers. [5]

### Bugs from First Live Run (v6.0)

*Source: [6]*

| # | Bug | Status |
|---|-----|--------|
| 1 | Vault-sourced tasks written to Inbox instead of PHEP | Fixed in skill spec |
| 2 | Default blocks never written to calendar | Fixed — Phase 5 Step D added |
| 3 | Missed tasks from Today filter (used `find-tasks-by-date` instead of saved filter) | Fixed — Phase 0 uses ⭐ Today filter |
| 4 | Curation phase was chat-based, not interactive | Known gap — interactive artifact needed |
| 5 | Orphan detection logic was wrong (flagged expected state as orphans) | Fixed — orphan concept removed |
| 6 | No duration history store | Open — future work |
| 7 | Sequence rendering was chat-based | Known gap — timeline artifact future work |
| 8 | Habits block not sourced from vault (no per-habit duration data) | Accepted — 45min flat estimate |

### Resolved Issues

*Source: [2]*

- **Stale PUSH times on bumped/all-day tasks (2026-04-29):** Resolved. Phase 4
  now writes `due_string: "today"` for stale-timed bumped tasks. **Note:**
  later superseded by correction [5] #2 — this write type was itself wrong and
  removed.
- **API sync lag on completed tasks (2026-04-29):** Workaround codified —
  inline completion at confirm gate. Not fixable at skill level.
- **Calendar/Todoist task deduplication (v5.0):** Covered-by-calendar dedup
  added in Phase 2. Fuzzy match on 2+ shared meaningful nouns. Not yet
  observed in live run.

---

## 7. Open Questions & Future Work

| Item | Source | Notes |
|------|--------|-------|
| Duration history store | [6] | Record `effort_name → typical_duration` from past sessions. Auto-suggest on future runs. |
| Vault effort `dates` write path | [7][8] | Phase 5 Step C designed but unbuilt. Open: is `dates` an array or single value? Do intervals use the same pattern? |
| Interactive Phase 3 curation artifact | [6] | Toggleable pool items, live capacity bar, cap counters. Phase 1 widget confirmed the right UX pattern. |
| Interactive Phase 4 timeline artifact | [6] | Drag-to-reorder, extend controls. Text table acceptable for now. |
| Calendar ID hardcoding | [7] | BusyCal IDs are hardcoded in skill. If account re-auth changes them, skill breaks silently. Consider query by name. |
| Block duration intelligence | [1] | No estimated-duration field on #todo yet (Tana era). Now moot — Todoist duration labels and native field cover this. |
| Pursuit child-todo scale | [1] | Tune had 231 children in Tana. Heuristic needed for "which are relevant today." Less relevant post-migration. |
| `.base` file access | [7] | Obsidian MCP can't read `.base` extension files. Use `Read` tool on full filesystem path if needed. |

---

## 8. File Locations

### Current (v6.0)

| Artifact | Path |
|----------|------|
| Repo edit source | `Skills/user/tdtb-bridger-vault/SKILL.md` |
| Live source of truth | Cloud — claude.ai, Customize > Skills (uploaded from the repo copy; the old `~/.claude/skills/` CLI path is dead) |
| Design doc | `Projects/Tune/Efforts/TDTB/DESIGN.md` |
| Behavioral corrections | `Skills/corrections/tdtb-bridger-vault.md` |
| First run observations | `Skills/observations/tdtb-bridger-vault-2026-05-08.md` |
| Session handoff | `handoff-tdtb-rewrite-2026-05-08.md` |
| This reference | `References/tdtb-master-reference.md` |
| Vault daily notes | `30 - Daily/MMM DD, YYYY.md` (in WALL⋅E-THNK vault) |
| Vault efforts | `50 - Operations/Tracking/Efforts/` (in vault) |
| Vault intervals | `50 - Operations/Tracking/Intervals/` (in vault) |
| Habits directory | `00 - META/Habituals/` (in vault) |

### Historical (retired)

| Artifact | Path / ID |
|----------|----------|
| v5.1 skill spec (Cowork) | `Projects/Tune/Efforts/TDTB/tdtb-bridger-v5.1.md` |
| Original design session | `References/tdtb-bridger-design-reference-2026-04-15.md` |
| Quarantine gate spec | `Configurations/tdtb-push-quarantine-gate.md` |
| Coda doc (TDTB v4.0) | `coda://docs/rNdKvDlv8H` |
| Make.com PULL scenario | `4456141` |
| Make.com PUSH scenario | `4486190` (team `91360`) |
| Corrections path | `Skills/corrections/tdtb-bridger-vault.md` (old `~/claude-corrections/` path retired, nonexistent) |

---

## 9. Glossary

*Sources: [3], [8]*

| Term | Meaning |
|------|---------|
| TDTB | Today's Daily Task Board — daily planning ritual |
| Sudsing | Showering |
| Live | Micro-adventure — embracing uncomfortable moments |
| Shivery Jigs | Friday night activities with Megan |
| Minting | Trinoor work time |
| Full Stop | Hard end-of-day cutoff |
| Wind down | End-of-day decompression |
| Putz | Auto-imported Pomodoro filler block |
| PHEP | Primary Todoist project for TDTB-created tasks |
| Bridger | The Claude skill that bridges data sources into a sequenced plan |

---

## Structural Debt Findings (from initial audit)

*Source: [1]*

Preserved for historical context — these findings drove early design decisions.

### Effort Lifecycle (from 100 sampled)

- ~37 completed, ~63 open (more beyond first page)
- Zombie found: "Lose Weight" — Finished status but checkbox unchecked
- Dormant candidates: Coda Transfer (5 mo), Holly Print (8 mo), Revisited
  Matrix Display Signage (7 mo, R1 Trivial)
- Status options beyond original schema: Dormant, Maintenance

### Relationship Gaps

- Part of generally reliable (4/4 spot-checked)
- Self-referencing Subitem of on 2 nodes (circular, dead weight)
- ~10 efforts physically in capture inbox but functionally routed via tags

### Cross-System Duplication

- "Post move" in both Todoist (overdue) and Tana. Decision: Tana owns Adam's
  copy.
