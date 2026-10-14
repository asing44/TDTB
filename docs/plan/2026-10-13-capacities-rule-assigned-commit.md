# 2026-10-13 — Admit rule-assigned Capacities rows at commit

User-approved, bounded backend fix. Build preview (`POST /commit?mode=shadow`)
and Live commit returned `422`:

```
commit refused: Capacities rows not admitted: 'Cleanups' is not selected
(never selected, or withdrawn since it was indexed) ...
```

for Capacities rows the plan showed as **Assigned**.

## Root cause (from source)

- `app/capacities_adapter.py` (`_project_object`, ~1455) auto-assigns a row
  when an ACTIVE stored rule evaluates to `MATCH` and an assignment mapping
  exists: `assigned = rule_state is MATCH and has_assignment_mapping and ...`.
  No selection record is involved.
- `app/main.py` `_capacities_commit_refusals` (~575) required **every**
  authorized Capacities assigned row to have a CURRENT
  `capacities-selections.json` record. `~/.config/tdtb/state/capacities-selections.json`
  does not exist, so `capacities_selections.load_selections` returns the empty
  default and every rule-assigned row was refused.
- This requirement predates rule-assigned rows (S6 commit `554e176`,
  2026-10-10) and was written when the only path onto the assigned surface was
  an explicit selection.

## Contract change

`_capacities_commit_refusals` now admits a Capacities row whose **server index
entry** carries `capacities_assignment_basis == "rule"` without a selection
record. This **supersedes** the S6 statement "selection required for every
Capacities row"; selection is required only for selection-basis rows.

Preserved, unchanged gates (all still enforced at commit):

- complete stable identity (`_capacities_index_complete`);
- identity match against the server index only (`_server_match` +
  `_capacities_identity_matches`); the browser's `identity`/`path` is the only
  browser input that reaches the gate and it must equal the server row;
- tag / hard-exclusion re-check against the CURRENT policy;
- the Drop-from-plan list;
- review reasons still need an acknowledging selection record;
- completion/status unknown never lands on the assigned surface (adapter-side),
  so it stays unassigned.

## Mechanism (server-owned basis marker)

1. `_promote_selected_capacities` stamps
   `capacities_assignment_basis = "selection"` on each promoted shallow copy.
2. `build_digest_index` stamps the marker for Capacities rows:
   - a row already carrying `"selection"` keeps it;
   - otherwise a row with `assigned is True` (server rule/source assignment)
     gets `"rule"`;
   - an unmarked pool row (not commit-eligible) gets nothing.
3. `_capacities_commit_refusals` reads the basis **only from the server index
   row** (`candidate`), never from the submission:
   - `"rule"` → skip the selection-record requirement; still require
     acknowledgement when review reasons are present;
   - `"selection"` or **missing** (legacy index) → keep the existing selection
     requirement (fail closed).

No frontend, `/sequence`, budget, provider, rules/selection store, or runtime
state is touched. The adapter is unchanged; the marker is derived at index
build from the already server-owned `assigned` flag and the promotion stamp.

## Legacy fail-closed and activation

The marker cannot be safely recomputed at commit time: doing so would require
re-running the adapter's provider evaluation. It **is** recomputed on the next
`GET /plan-inputs`, which rewrites the dated digest index on every load
(`app/main.py` ~4178 `runstate.write_digest_index`). Therefore the live
activation step is:

1. restart the app so the new code is loaded;
2. reload the cockpit (a `GET /plan-inputs`) so the index is rewritten with
   `capacities_assignment_basis`.

Already-written legacy index rows **without** the marker continue to fail
closed (selection still required) until that reload. No runtime state was
rewritten by this change.

## Verification actually run

- TDD red (`app/.venv/bin/python -m pytest
  app/tests/test_capacities_source_route.py -q`): **2 failed, 70 passed** —
  `test_rule_assigned_row_is_admitted_without_a_selection` and
  `test_rule_assigned_row_with_review_reasons_without_acknowledgement_is_refused`
  (both refused with "is not selected").
- Green focused: same file — **72 passed**.
- Adjacent focused: `test_main_api.py test_commit_shadow.py
  test_plan_inputs_sources.py test_staging_actions.py
  test_capacities_selections.py` — **337 passed**.
- Full backend: `app/.venv/bin/python -m pytest app/tests -q` — **3092 passed**
  (baseline before this change: **3085 passed**; +7 net new tests).

## What is NOT verified

- No live server restart, page reload, budget change, or live commit was run;
  the activation step above is a documented procedure, not an executed one.
- The positive admission is proven only through the route tests with the index
  built by `build_digest_index`; the live Capacities provider path was not
  exercised (fixtures/mocks only).
- A genuinely rule-assigned row carries no review reasons (the adapter only
  assigns when status/completion/rule state are known), so the
  rule-basis-plus-review-reasons case is covered by a synthetic indexed row.
