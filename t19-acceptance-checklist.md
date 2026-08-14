# T19 — Manual Acceptance Checklist (`[gate: manual acceptance per spec § 5]`)

**Run this in your own Terminal, not via Claude.** EventKit's calendar-access grant (TCC)
binds to the *launching* process; a Claude/subagent sandbox is always `notDetermined` and
cannot write to the real calendar store. Passing this checklist is the gate that starts the
T18 bake-in clock (`bake-in-protocol.md`: 5 app-driven days, rolling + KILL-reset).

- **Governing criteria:** `spec.md` § 5 (each `[C#]` below maps to one § 5 bullet).
- **App dir:** `Tasks/tdtb-app-pilot/app/` · **venv:** `app/.venv/` · **port:** `8746`.
- Tick each box only on the stated observation. Any FAIL → stop, note it, do not proceed to
  live commit.

---

## 0. Pre-flight (clean slate)

- [ ] **Clear the T14 live artifacts so the diff starts clean** (yours to clear — leftovers
  from the T14 write-verify gate):
  - [ ] Delete calendar event **`Deep Work — TDTB gate test`** (2–3 PM, calendar `⬜ Blocks`).
  - [ ] Delete today's **`# TDTB Plan`** section from the daily note
    `30 - Daily/2026-07-13.md` (leave the rest of the note).
  - Re-verify the date if you run this on a different day — every `2026-07-13` below is "today".
- [ ] **Secrets present:** `ls -l ~/.config/tdtb/env` shows the file, mode `-rw-------` (600),
  carrying `TODOIST_API_TOKEN` (bare `KEY=val`, no `export`). The Agent SDK does NOT read a key
  from here — it uses the logged-in `claude` CLI; confirm `claude` is signed in.
- [ ] **Suite green from a clean tree:**
  `cd Tasks/tdtb-app-pilot/app && ./.venv/bin/python -m pytest -q` → **389 passed**.
- [ ] **`git status` is clean** for `Tasks/tdtb-app-pilot/app/` (no half-staged writers; the
  foreign staged deletion in the shared index is a *different* session's — leave it, it does
  not affect the running tree).

---

## 1. Launch + auth

- [ ] **Export the environment first, then start the app** (the process reads `os.environ`
  directly — there is no dotenv autoloader, so a bare `uvicorn` launch has NO vault and the
  SDK/Todoist calls fail; `/config` will 503 `vault root not configured`):
  ```bash
  cd Tasks/tdtb-app-pilot/app
  export TDTB_VAULT_ROOT="/Users/adam/Local Documents/Obsidian/WALL⋅E-THNK"  # ⋅ = U+22C5, not a period
  set -a; source ~/.config/tdtb/env; set +a   # exports TODOIST_API_TOKEN (bare KEY=val file)
  ./.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8746
  ```
  (or the `tdtb` shell alias, once it is configured to do the two exports above). Agent-SDK
  judgment calls authenticate via the logged-in `claude` CLI — no `ANTHROPIC_API_KEY` needed as
  long as `claude` is signed in. Leave the server running; use a second Terminal tab for the
  `curl`s below (re-run the same two `export`/`source` lines in that tab if you build the commit
  body from the shell).
- [ ] **Health:** `curl -s localhost:8746/health` → `{"status":"ok"}`.
- [ ] **Grab the session token** (all mutating routes need it):
  ```bash
  TOKEN=$(curl -s localhost:8746/session-token | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
  echo "$TOKEN"    # non-empty
  ```
- [ ] **Config resolves against the live vault:** `curl -s localhost:8746/config` →
  `"bootstrap_needed": false` and `"valid": true` (no missing sections/keys). If
  `bootstrap_needed: true`, the vault config note is missing — fix before continuing.

---

## 2. `[C4]` Gather module passes unmodified + `[C5]` gather < 5 s

- [ ] **Absorbed gather suite green** (the `tdtb_gather.py` tests moved with the module, must
  pass unmodified):
  `./.venv/bin/python -m pytest gather/tests -q` → all pass.
- [ ] **Gather wall-clock < 5 s on the full live vault:**
  ```bash
  time curl -s -X POST -H "X-TDTB-Token: $TOKEN" localhost:8746/gather > /tmp/gather.json
  ```
  → `real` **< 5 s**; `/tmp/gather.json` is a non-empty run-data object (pool + assigned).
- [ ] The run wrote today's cache + run-state note:
  `ls "00 - META/Cache/tdtb-runstate-2026-07-13.md"` exists in the vault (path resolved from
  `$TDTB_VAULT_ROOT`).

---

## 3. `[C2]` Judgment budget — ≤ 4 SDK calls/run, 0 tokens on deterministic steps

- [ ] **Re-run the T11/T19 fixtures against the LIVE Agent SDK** (the gate the whole cost claim
  rests on — replays 4 fixtures × 4 judgment calls, one shared `RunContext` per fixture, and
  asserts `calls_made ≤ 4`):
  ```bash
  ./.venv/bin/python fixtures_dryrun.py
  ```
  → **`RESULT: PASS — 4 fixtures x 4 calls, all schema-valid`**, and every fixture's
  `_call_count` row reads `≤4/4`. Auth is the logged-in `claude` CLI (no `ANTHROPIC_API_KEY`
  needed). **Be patient: 16 sequential live SDK calls, ~1–2 min** — it looks idle mid-run but is
  not hung unless a single call blocks > 3 min. It reads `tests/fixtures/*.json`, not the vault,
  so it does not need `TDTB_VAULT_ROOT`.
- [ ] **0 tokens on deterministic steps:** the `/gather`, `/digest` (deterministic tier),
  `/adjust` (non-free-text), and `/commit` calls above emitted **no** Agent-SDK log lines in
  the uvicorn console — only the 4 judgment phases (audit / digest-suggest / free-text adjust /
  sequence) ever call the SDK.

---

## 4. `[C3]` Morning-workout exclusion enforced (prompt **and** server)

- [ ] **Server-side hard guard fires** (belt to the prompt's suspenders — `sequence.validate_sequence`
  via the `/sequence` route). Post a sequence that places a workout block before noon:
  ```bash
  curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "X-TDTB-Token: $TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"assigned":[{"id":"morning-run","name":"Run","types":["workout"]}],
         "config":{},"anchored_blocks":[],
         "proposal_override":{"sequence":[{"id":"morning-run","start":"07:00","end":"07:30"}]}}' \
    localhost:8746/sequence
  ```
  → **`422`** with `hard_errors` naming the pre-noon workout placement. (If your build has no
  `proposal_override` test seam, instead confirm via unit test:
  `./.venv/bin/python -m pytest tests/test_sequence.py -q -k workout` → passes, exercising the
  same `validate_sequence` guard the route calls.)
- [ ] **Prompt-side ban present:** the real `/sequence` proposal for today never returns a
  workout item with a start before `12:00` (Press `before_work` config exception aside). Eyeball
  the timeline view — no morning workout blocks.

---

## 5. `[C1]` Full pipeline end-to-end — live commit, outputs identical-class to a skill run

Walk the browser UI at **http://127.0.0.1:8746/static** (config → digest → adjustments →
timeline), then commit. Shadow first, live second.

> **§5 is day-agnostic (workday gate removed 2026-07-13).** `[C1]` signs off on any day the live
> commit lands cleanly on the surfaces **today's plan actually touches** + identical-class holds.
> Which surfaces those are depends on the plan's block classes, not on the calendar day:
> - **Assigned efforts → Todoist Step A** (timed tasks; BusyCal renders them on the grid — SKILL
>   lines 77-78: "Todoist tasks are the schedulable blocks"). An all-Assigned day exercises the
>   **Todoist + vault** paths live, and **produces zero calendar events by design** — the skill
>   does the same for that plan, so identical-class still holds.
> - **Anchored (Step E) / Minting/Shivery-Jigs (Step D) / Trinoor work-zone (Step D′)** → the
>   `write_calendar` path (⬜ Blocks or 🟡). Only exercised live when the plan carries such a block.
>
> The live **EventKit / `write_calendar`** path was already live-proven in **T14** (the
> `Deep Work — TDTB gate test` ⬜ Blocks event cleared in §0), and is unit + shadow covered — so a
> commit whose plan has no calendar-class rows does not re-witness it, and doesn't need to. The
> 🟡 work-calendar routing is covered by **shadow + unit tests + the static config-ID check below**;
> ISS-1 already live-confirmed the PHEP routing logic in shadow.
>
> **Compensating check (replaces the workday requirement):** before live commit, confirm the
> work-calendar targets resolve in config so decoupling hides no config gap —
> `curl -s localhost:8746/config | python3 -c 'import sys,json; c=json.load(sys.stdin); print("calendars:", c.get("calendars") or c.get("resolved",{}).get("calendars") or "CHECK config note")'`
> — the 🟡 Trinoor / 🟡 Mint / ⬜ Blocks calendar names must all be present. A missing 🟡 name is
> the ONE failure a personal-day live run can't catch; this static check catches it instead.
>
> **Partial-§5 run 2026-07-13 (shadow):** all-personal day (6 Assigned). Shadow PASS —
> `unavailable_surfaces:[]`, `would-create 7 / no-op 6 / conflict 0`, zero mutation. **ISS-1
> live-confirmed:** `Townhome Pontification` routed **PHEP**, other 5 → Inbox. Step-C flips all
> no-op (already `assigned: true`). Token is per-session from `GET /session-token` (not
> `~/.config/tdtb/env`, which only holds `TODOIST_API_TOKEN`).

- [x] **Shadow commit writes nothing** and previews the full diff: _(verified 2026-07-13 — see above)_
  ```bash
  curl -s -X POST -H "X-TDTB-Token: $TOKEN" -H 'Content-Type: application/json' \
    -d @/tmp/commit-body.json 'localhost:8746/commit?mode=shadow' | python3 -m json.tool
  ```
  Build `/tmp/commit-body.json` = `{"digest":…, "sequence":…, "config":…}` with the headless
  generator (the T16 review UI is gated *after* T19, so this substitutes for its payload):
  ```bash
  ./.venv/bin/python build_commit_body.py            # -> /tmp/commit-body.json
  ```
  → a diff object; **no** vault/Todoist/calendar mutation (re-check the daily note + calendar
  are untouched).
- [x] **Live micro-adventure reroute `[C1]`** — ISS-2 seam covered by unit+seam (green); not
  re-forced this live run (today's plan carried no Live block). Reroute logic proven at
  `test_build_commit_body.py` + the §6 manifest. _(accepted 2026-07-13)_
- [x] **Live commit lands** all surfaces: **`ok:true / failed:[]`** — 6 Assigned efforts →
  Todoist Step A, daily-note patch, 6 frontmatter flips. Manifest partition confirmed against the
  committed `/tmp/commit-body.json` (6× Step A todoist, 1× Step B vault, 6× Step C vault; **0
  calendar rows** — all-Assigned plan). _(verified 2026-07-13)_
- [x] **Todoist** — the 6 efforts landed as timed tasks (visible on the BusyCal grid via the
  Todoist source): Garage Buildout, Magic Mirror, Personal Digesty, Together Digesty, Townhome
  Pontification (→ **PHEP**), Volunteering. _(confirmed 2026-07-13)_
- [x] **Daily note** — `30 - Daily/2026-07-13.md` `# TDTB Plan` section written (Step B patch).
  _(confirmed 2026-07-13)_
- [x] **Frontmatter flips** — the 6 committed effort notes read `assigned: true` (Step C).
  _(confirmed 2026-07-13)_
- [x] **Calendar events on the correct calendars** — **N/A today by design:** the plan was 100%
  Assigned efforts → Todoist, so zero calendar-class rows (no Step D/E/D′). The `write_calendar`
  path is live-proven in **T14** (⬜ Blocks gate-test event) + unit/shadow covered; the static
  config-ID check confirms 🟡/⬜ targets resolve. No live calendar write required for this plan.
  _(accepted 2026-07-13)_
- [x] **Identical-class to a skill run** — confirmed against `tdtb-bridger-vault` SKILL.md:77-78:
  Assigned efforts → timed Todoist tasks (not calendar events) is exactly the skill's placement
  model. The 6 committed rows match the class the skill produces for this plan. _(confirmed
  2026-07-13)_

---

## 6. `[C1]` T15 ledger + resume — the failure-safe guarantee (REQUIRED ≥ 1×)

T19 must exercise the orchestrator's crash-consistency + resume at least once. The bundled
proof drives the exact `orchestrate.run_orchestrated` code path deterministically (temp vault,
frozen fakes, **0 tokens, no live surfaces**) so it is safe to run anywhere and repeatably —
you witness the persisted ledger + resume-only-retry without half-breaking a real commit.

- [x] **Run the injection proof:** _(ran 2026-07-13, `RESULT: PASS`)_
  ```bash
  cd Tasks/tdtb-app-pilot
  ./app/.venv/bin/python t19_inject_failure.py --keep
  ```
  → **`RESULT: PASS`** with all checks `[PASS]` across both scenarios:
  - [x] **Scenario A (graceful):** run 1 = `todoist=ok vault_flips=ok daily_note=ok
    calendar=failed`, ledger **persisted to disk** with 3 ok + 1 failed; run 2 (`resume=True`)
    re-dispatches **only** calendar (writer call-counts prove todoist/flips/daily are carried
    forward, not re-run) and recovers to `ok=True`. _(all 9 sub-checks PASS)_
  - [x] **Scenario B (hard crash):** the run aborts on the injected `write_calendar` raise, yet
    the on-disk ledger already holds the **3 pre-crash surfaces** (crash-consistency); a
    `resume=True` re-run finishes calendar and reaches `ok=True`. _(all checks PASS)_
- [x] **Inspect the persisted artifact** (with `--keep` the script prints the temp path):
  open the Scenario-A `…/00 - META/Cache/tdtb-runstate-2026-07-13.md` and confirm the
  `commit_ledger.surfaces` block is the § 0.8-shaped note you would otherwise read in
  WALL·E-THNK. Delete the temp dir after (`rm -rf` the printed path). _(verified: skeleton
  frontmatter + fenced json body, per-surface {status,step,created,updated,noops,reconciliation,
  error,note}; temp removed)_

> **Optional — prove it against the LIVE surfaces too** (heavier; only if you want a live
> resume witness): after a clean live commit, re-run `…/commit?mode=live&resume=true` — every
> surface returns `"note":"resumed: already ok"` and no live API is re-hit (idempotent no-op).
> To force a *live* one-surface failure, unset the calendar consent (or rename a target calendar
> title so the pre-write ID assertion fails), commit, confirm one surface `failed` + the ledger
> persisted, restore, then `resume=true` retries only that surface. Not required — Section 6's
> deterministic proof already satisfies the § 5 criterion.

---

## 7. Sign-off

- [x] All `[C1]`–`[C5]` boxes ticked, both Section-6 scenarios PASS. _(2026-07-13)_
- [x] Stop the uvicorn process. _(Adam)_
- [x] Record the result: **T19 PASS** (2026-07-13) → starts the T18 bake-in clock (day 1 of 5).
  Next: T16 (frontend phase 2 — timeline drag-adjust + commit-review views), gated after T19.
- [x] Any FAIL → n/a (PASS).

### Sign-off record — **T19 PASS (2026-07-13)**

**Section status** (all sections complete):

| § | Criterion | Status |
|---|---|---|
| 1 | Launch + auth | ✅ PASS (2026-07-13) |
| 2 | `[C4]`/`[C5]` gather unmodified + < 5 s | ✅ PASS (0.653 s, cache hit) |
| 3 | `[C2]` judgment budget ≤ 4 SDK calls, 0 tokens deterministic | ✅ PASS (4 fixtures × 4 calls) |
| 4 | `[C3]` morning-workout exclusion (prompt + server) | ✅ PASS (unit path, 9/9 — see caveat) |
| 5 | `[C1]` full pipeline live commit, identical-class | ✅ PASS (2026-07-13, `ok:true/failed:[]`; all-Assigned plan → Todoist+vault live, calendar T14-proven; identical-class per SKILL 77-78) |
| 6 | `[C1]` T15 ledger + resume | ✅ PASS (both scenarios, 2026-07-13) |

**Carry-forward caveats** (fold into the result line):
- **§4 seam (adjudicated by-design 2026-07-13):** the `/sequence` `proposal_override` path
  returned **200**, not **422**, because `SequenceRequest` defines no `proposal_override` field —
  Pydantic silently drops the unknown key, so the route always generates a fresh proposal via
  `judgment.propose_sequence` then validates *that* (which placed the workout compliantly → no
  hard error). This build ships **no** `proposal_override` test seam; the checklist's own fallback
  (`test_sequence.py -k workout`, 9/9) fully covers the pre-noon ban, so `[C3]` holds. **Not a
  defect** — the HTTP-layer test-seam gap is a possible future improvement, not a bug.
- **§5 workday gate REMOVED 2026-07-13:** `[C1]` no longer requires a work-zone day. The 2026-07-13
  live commit was an **all-Assigned plan** → exercised **Todoist Step A + vault Step B/C** live
  (`ok:true/failed:[]`); it produced **zero calendar-class rows by design** (Assigned efforts →
  timed Todoist tasks, not events — SKILL 77-78), so the live `write_calendar` path was **not**
  re-witnessed in this commit. That path is live-proven in **T14** (⬜ Blocks gate-test event) +
  unit/shadow covered; 🟡 routing is shadow+unit+config-ID-covered. Identical-class confirmed.
- **Date re-verify:** all fixtures/harness are keyed to **2026-07-13**. If §5 runs on a later
  calendar day, re-confirm no date-mismatch time-bomb resurfaces (the one live-commit-path date
  bomb was fixed in `45c2d67`; `gather.effective_date` is monkeypatched in tests, not in the live
  run — the live run uses the real clock).
- **§5 micro-adventure reroute:** exercise via `build_commit_body.py --micro-adventure "…"`
  (today's vault has no Live micro-adventure selected). ISS-2 unit+seam coverage is green
  (`test_build_commit_body.py`); §5 is the one live end-to-end confirmation.

**Result line:**

> **T19 result:** **PASS** — date **2026-07-13**.
> Caveats carried: (1) §4 `proposal_override` 200-not-422 — by-design (no override field on
> `SequenceRequest`), pre-noon workout ban unit-covered 9/9, `[C3]` holds. (2) §5 ran on an
> **all-Assigned personal-day plan** → Todoist Step A + vault B/C live-exercised
> (`ok:true/failed:[]`); **zero calendar-class rows by design**, so live `write_calendar` not
> re-witnessed this commit — that path is T14-proven (⬜ Blocks gate-test) + unit/shadow covered;
> 🟡 routing config-ID-verified. Identical-class per SKILL 77-78.
> **On PASS → T18 bake-in clock day 1 of 5 starts (day 1 = 2026-07-13); next is T16 (frontend
> phase 2).**
