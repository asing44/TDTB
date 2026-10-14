# 2026-10-13 — TDTB feedback regressions (R20b, daily allowance, recurring-due parser)

User-directed repair of three feedback regressions. Small, restartable, and
evidence-backed; no change to backend caps/reset, Mint work minutes, or task
room. Source remains uncommitted; independent review found no blocking defects.

## R20b — Plan-only `Copy prompt` restored (supersedes U5 retirement)

The U5 retirement of the manual copy-prompt bypass is superseded for the Plan
screen. `frontend/src/ui/ActionDock.tsx` again renders a secondary
`Copy prompt` button (aria-label `Copy plan prompt for an external LLM`) whose
click serializes the whole current plan with the intact
`buildDayPrompt(s)` from `frontend/src/store/exportPrompt.ts` and writes it to
the clipboard.

- Available with inputs in healthy, degraded-source, and spent-budget states
  (it is never disabled — it exists for the blocked states).
- Pure local: no network, no controller/adapter call, no billing, no source
  write. It never bypasses the billed/commit gates.
- Clipboard unavailable or rejected: a selectable read-only textarea shows the
  already-built complete prompt (existing `.copy-prompt-fallback` styles) —
  never an unhandled rejection and never a success lie.
- Copy success renders a concise `role="status"` message.
- The prompt note and the button sub-label keep copying plan context distinct
  from the opt-in Todoist prompt-task export, which stays Commit-only.
- No copy control on Set up day or Connections; stale "no copy anywhere"
  comments now read "Plan-only". The source-health alert stays truthful and
  does not advertise the fallback.

## Daily LLM call allowance visible outside collapsed Details

`Footer.tsx` gains `DayBudgetChip` (uses only the existing `s.ledger`) rendered
in all three footer variants — Plan, Set up day, Connections. It reads
`Day budget: {remaining}/{cap} calls left`, shows `Day budget: 0/4 calls left`
in the warn style when exhausted, and `Day budget: unavailable` when no ledger
has loaded (no fabricated count). No backend cap/reset change, no extra read,
no billing; the backend `MAX_CALLS_PER_RUN=4` and `/billed-ledger` are intact.

## Recurring-due parser repair (timed value under `date`)

A valid timed due such as `date='2026-10-13T22:00:00'` with no `datetime` key
entered the date-only `strptime` branch and raised. Regression tests were added
first in `app/tests/test_runtime_actions.py` (`TestRecurringUnassignDueShapes`);
root then applied the one fully-located `app/runtime_actions.py` fix. The
selected due value is now classified by its time-bearing shape: timed values
parse and advance as datetimes (local wall time preserved), forward dispatch
chooses `reschedule_task_datetime`, and undo restores the original timed shape
via the datetime API. Date-only values are unchanged; malformed/unsupported
values still fail closed with no write and never close, clear, or complete a
recurring task.

## Verification actually run

- Focused frontend (this work): `npx vitest run` on `footer.test.tsx`,
  `components.test.tsx`, `compact-cockpit.test.tsx`, `day-setup-screen.test.tsx`
  — 4 files / 120 tests passed, after an intentional red run.
- Full frontend (this work): `npm test` — 91 files passed, 1 skipped;
  1398 passed, 6 skipped. `npm run typecheck` exit 0. `npm run build:mockup`
  and `npm run build:prod` succeeded; generated `index.html` and JS/CSS bundles
  were read back and contain the new strings.
- Python recurring fix (root, not this work): root ran
  `.venv/bin/python -m pytest tests/test_runtime_actions.py
  tests/test_staging_actions.py -q` in `app` — 77 passed, 1 warning, exit 0.
- Full backend (root): `app/.venv/bin/python -m pytest app/tests -q` from
  the repository root — 3066 passed, 1 warning, exit 0, including bootstrap tests.
- Independent review reproduced the 77-test backend check, the 1398-test
  frontend suite (6 skipped), and typecheck; no blocking or deferred defects.
- Fixture/scratch visual walk: static `mockups/cockpit` served locally; the
  page requested only its own JS/CSS (+favicon 404), i.e. zero billed/real
  source calls. Screenshots at 1500x1000 and 900x1000 for healthy, degraded,
  and exhausted states show the copy button and budget chip with no overlap.
  Degraded/exhausted captures use mockup debug-state overrides rather than
  named fixture scenarios; root viewed all six captures.

## Gaps / not verified here

- Live Todoist floating-time semantics were not exercised; apply/undo behavior
  was verified through synthetic clients only.
- The screenshots are fixture/mockup states, not the live `:8746` runtime.
- No commit, staging, restart, or live action was performed.
