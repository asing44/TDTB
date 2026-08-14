# TDTB App — Bake-In Protocol (T18)

> Governs the shadow-comparison bake-in that gates retiring `tdtb-bridger-vault`.
> Authored T18; the bake-in **clock does not start until T19 acceptance passes**
> (per plan `Plans Link/2026-07-12-tdtb-app-pilot-build.md`). This doc + the wired
> differ ARE T18's deliverable; the run itself is post-T19.
>
> **Status: APPROVED (2026-07-13).** Pass bar (§3) = 5 app-driven days, rolling
> + KILL-reset — approved by Adam. Gate
> `[protocol doc + differ wired; Adam approves the bar]` satisfied; T18 closed.
>
> **⚠ CLOCK RESET 2026-07-14 (gather-parity realign).** Day 1 (2026-07-13,
> PASS) is void: it validated a vault-only digest — the app was missing the
> Todoist / calendar / habits read sources the spec promises. The clock
> restarts at day 1 on the source-complete digest
> (`Plans Link/2026-07-14-tdtb-gather-parity.md`). Each day's log entry now
> also records the digest's source counts (`vault/todoist/calendar: N/N/N`
> from `/plan-inputs.source_counts`) and any `source_warnings` — **a day
> that ran with a non-empty source_warnings list cannot log PASS** (a
> silently degraded source is the failure mode that voided day 1).
>
> **D1 gate (ui-parity T9, 2026-07-14):** the live commit report now carries
> `verify_failures` — one entry per write whose post-commit read-back
> contradicted intent (datetime landed date-only, event on wrong calendar,
> frontmatter key missing, silent drop). **A day whose commit report has a
> non-empty `verify_failures` list cannot log PASS.** Without this, PASS
> meant "Adam manually inspected", not "the app wrote correctly".

---

## 0. Why this exists

The app and the skill (`tdtb-bridger-vault`) produce the *same* class of daily
plan. Before the skill is shrunk to a thin fallback (spec §6.2), the app must be
shown to agree with reality over a real bake-in window — not in a lab, on live
mornings. This protocol defines **how agreement is measured, what a passing
window looks like, and what pulls the app back to shadow if it misfires.**

Reconciliation note: spec §6.1 says "app runs alongside the skill … compare
commit outputs when both run on the same day." The council-revised plan (T18a)
tightened this to a **single-writer rule** — on any given day exactly ONE of
{skill, app} commits; the other runs shadow-only. The plan is the LOCKED
governing doc and supersedes the looser spec phrasing: two live writers on one
day would double-write Todoist tasks and calendar events (no dedup across the
two code paths). The comparison is still daily — it just compares the app's
*shadow* output against whatever actually got committed, never two live runs.

---

## 1. Single-writer rule (T18a)

Each bake-in day has exactly one **driver** and one **shadow**:

| Role | Runs | Writes? |
|---|---|---|
| **Driver** | full ritual through commit | YES — the day's real plan |
| **Shadow** | full ritual through `/commit?mode=shadow` | NO — manifest computed, nothing written |

- **Driver is the skill** on skill-days, **the app** on app-days.
- The **app always runs its shadow** — even on app-days (there, shadow is
  computed *before* the live commit, off the same digest+sequence, so the
  daily diff still has an app manifest to compare).
- Never two drivers. A day with no driver is a skipped day (logged, not a
  failure).

Rationale: kills the double-write hazard while preserving a daily agreement
signal. The app's manifest is free to compute every day; only the *write* is
gated to one owner.

---

## 2. Daily diff (T18b)

The differ (`bake_in_diff.py`, wired via `bake_in_run.py`) reuses T13's
`shadow.diff_against_live` — **do not reimplement diff logic.** The app's
shadow manifest is diffed against **post-commit** live state (the driver's
actual plan). It then **reinterprets** the resulting `ShadowDiff` through a
bake-in lens:

### 2.1 Classification (bake-in lens)

`shadow.diff_against_live` classes each manifest row as `no-op` / `would-create`
/ `would-update` / `conflict` / `unavailable`. The bake-in verdict remaps these:

| Shadow class | Surface / step | Bake-in verdict | Why |
|---|---|---|---|
| `no-op` | any | **AGREE** | app manifest matches what was committed |
| `would-update` | Step B (daily-note patch) | **AGREE** | `diff_against_live` *always* marks Step B non-no-op ("section exists, will be replaced", shadow.py:394); post-commit the section always exists, so this is expected agreement, **not** drift |
| `would-create` | any | **UNEXPLAINED** | app wants something the driver did not commit |
| `would-update` | Todoist / Step C flag | **UNEXPLAINED** | app wants a different time / flag state than what landed |
| `conflict` | any | **UNEXPLAINED** | live state contradicts the manifest |
| `unavailable` | any | **INCONCLUSIVE** | surface degraded in `gather_live_state`; day cannot be scored (see §3.3) |

> ⚠ The Step-B carve-out is the one non-obvious rule. Without it every day
> scores ≥1 diff and the bake-in can never pass. It is **narrow**: only Step B
> (`action == "patch"`), only the `would-update` "section exists" case. A Step B
> `conflict` ("daily note not found") stays UNEXPLAINED.

### 2.2 A day passes when

`UNEXPLAINED == 0` **and** the driver's commit reported **zero reconciliation
failures** (T14 post-write read-back) **and** **zero wrong-surface writes**
(T14 calendar-ID assertion). AGREE and INCONCLUSIVE rows do not fail a day;
INCONCLUSIVE downgrades it (§3.3).

### 2.3 Log line schema — `bake-in-log.md`

One row appended per bake-in day (the differ owns the write; append-only,
never rewrites prior rows):

```
| date | driver | agree | unexplained | inconclusive | recon_fail | verdict | notes |
|------|--------|-------|-------------|--------------|------------|---------|-------|
| 2026-07-20 | skill | 11 | 0 | 0 | 0 | PASS | — |
| 2026-07-21 | app   | 10 | 1 | 0 | 0 | DIFF | Step A "Guitar" created, not in committed plan |
```

- `verdict` ∈ {`PASS`, `DIFF`, `INCONCLUSIVE`, `SKIP`, `KILL`}.
- `DIFF` = ≥1 UNEXPLAINED. `KILL` = a recon failure or wrong-surface write fired
  (§4). `notes` names each UNEXPLAINED row (step + name + one-line reason) so a
  diff is adjudicable months later without re-running.
- The differ **never** self-marks a diff "explained" — that is a human call
  recorded by editing the row's notes, not by the differ.

---

## 3. Pass bar (T18c) — **APPROVED 2026-07-13**

Bar to declare acceptance and proceed to the skill shrink:

- **≥5 app-driven days** (not just app-shadow days) …
- … with **zero unexplained diffs** and **zero reconciliation failures** across
  the whole window …
- … within a **rolling window** — the 5 passing app-days need not be
  consecutive, but any `KILL` (§4) **resets the count to zero** (a wrong-surface
  write means the streak proved nothing).

### 3.1 Why app-driven, not shadow-only

Shadow-only days prove the app *computes* the same plan. Only an app-*driven*
day proves the app *writes* it correctly (idempotency, reconciliation, calendar
IDs) — the failure classes with prior form (2026-06-08 wrong-surface,
2026-06-23 silent drop) only manifest on a real write.

### 3.2 Minimum window

≥5 app-driven days inside a ≥1-week bake-in (spec §6.1). Skill-driven and
skipped days extend the calendar window but don't count toward the 5.

### 3.3 Inconclusive days

An INCONCLUSIVE day (a surface degraded) neither passes nor fails — it does
**not** advance the count and does **not** reset it. Repeated INCONCLUSIVE days
(≥2 in the window) surface as a *harness* problem to fix before trusting the
bar, not an app problem.

> **Resolved 2026-07-13:** rolling-with-KILL-reset at 5, per Adam. Consecutive
> was rejected as fragile to skill-days / travel / skips that aren't app
> failures; a real corruption (KILL) still resets the streak to zero.

---

## 4. Kill-switch (T18d)

Any of the following on an app-driven day flips `verdict = KILL`:

- a **reconciliation failure** (T14 read-back count/ID mismatch), or
- a **wrong-surface write** (T14 calendar-ID assertion tripped, or an event
  landed on a calendar the manifest didn't target).

On `KILL`:

1. **Next day's driver reverts to the skill.** The app drops to shadow-only.
2. The pass-bar count **resets to zero** (§3).
3. The app stays shadow-only until the cause is fixed *and* a fix-forward note
   is recorded in `bake-in-log.md` notes for the `KILL` row.
4. Re-entry to app-driving is a deliberate restart of the window, not automatic.

The kill-switch is intentionally blunt: no partial-credit, no "probably fine."
A wrong-surface write is exactly the silent-corruption class this whole pilot
was built to prevent — it ends the streak, no appeal.

---

## 5. Deliverable checklist (T18)

- [x] Protocol doc (this file).
- [x] `bake_in_diff.py` — pure `classify_bakein(ShadowDiff) -> BakeInVerdict`
      + `day_verdict(...)` implementing §2.1–2.3; **no diff logic reimplemented**
      (consumes `shadow.diff_against_live` output). Step-B carve-out kept narrow
      (`step=="B" and action=="patch"`). TDD — 25 tests.
- [x] `bake_in_run.py` — injectable `run(...)` wiring shadow manifest →
      `diff_against_live` → `classify_bakein` → `day_verdict` → append-only §2.3
      row in `bake-in-log.md`; `main()` mirrors `shadow_run.py`. Pipe-escaped
      notes; driver-string validation.
- [x] `bake-in-log.md` — created lazily with the §2.3 header on the differ's
      first real invocation (not seeded now — no empty artifact in the tree).
- [x] Adam approves the §3 pass bar (5 app-driven days, rolling + KILL-reset —
      approved 2026-07-13). T18 closed.

Gate: `[protocol doc + differ wired; Adam approves the bar]`. Bake-in clock
starts only after T19.
