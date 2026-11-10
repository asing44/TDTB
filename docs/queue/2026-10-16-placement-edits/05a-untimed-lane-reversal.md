# 05a — Untimed lane: reversal of "no automatic date-only rule"

**LANDED, not published.** No GitHub issue was created. This file records a
reversal of the decision in
[`05-keep-all-day-action.md`](05-keep-all-day-action.md); it does not replace
that draft.

## Reversal

`05` settled "a per-row `Keep all-day` action in Plan tasks. Not a rule, not a
label." — and its Out of scope said "Any automatic date-only rule".

Owner decision **2026-11-09** (hand corrections on live rows) reverses the
"not a rule" half:

- the per-row `Keep all-day` action from `05` **stays** (issue #17);
- an **automatic rule** is added for quick check-ins only.

Reason: on 2026-11-09 four date-only Todoist tasks — "Meegy couch inspo texts"
(10 min), "Tirzepatide check if feeling shit" (15 min), "fu Indiria" (30 min),
"palce down more sticky traps" (15 min) — arrived in Plan tasks as timed
30-minute slots. The operator hand-made the first three all-day. A date-only
due is the operator's own signal that the item has no clock time; for a
check-in it is also the wrong shape to place.

## Rule

A row is untimed only when ALL hold (`app/untimed_lane.py`
`classify(item, config)`):

1. It is a native Todoist row with a date-only due — no `scheduled_start`.
   A due time is "specified" and stays timed.
2. It carries no explicit placement request: no `zone` other than `any`/none,
   no `latest_start`/`latest_end`, no `allow_time_adjustment: true`, and no
   per-item config override.
3. Its name matches a check-in token (`^fu\b`, `\btexts?\b`, `\bcheck\b`,
   `follow[- ]?up`, case-insensitive) **or** its native duration is at most
   10 minutes (1/3 of a 30-minute block).

The lane is the **existing all-day state** (`blocks == 0`), not a new lane
type: no `timed_auto_pins` entry (the row has no `scheduled_start`), no
placement duration (never re-seated), and `shadow.build_plan_manifest` emits
`schedule-all-day` with a date-only Todoist due.

**Escape hatch:** a client `blocks > 0` override always beats the automatic
rule — an operator-stated duration is never silently discarded.

## Config

`Defaults` → `untimed_lane` in `config/tdtb.json` (all keys optional; the code
defaults apply when absent):

```json
{
  "defaults": {
    "untimed_lane": {
      "enabled": true,
      "max_blocks": 0.3333,
      "tokens": ["^fu\\b", "\\btexts?\\b", "\\bcheck\\b", "follow[- ]?up"],
      "overrides": {"<row name>": false}
    }
  }
}
```

`enabled: false` restores the pre-2026-11-09 behavior with no code change.

## Where it lands

- `app/untimed_lane.py` — pure classifier (no I/O, no clock, no config read).
- `app/main.py` `_canonicalize_route_assigned` — server-side hook that sets
  `blocks = 0`, keeps `native_blocks`, and stamps `untimed_auto: true` plus
  `untimed_reason`. Client-supplied values for those two keys are stripped.
- `app/tests/test_untimed_lane.py` (table) and the
  `TestUntimedLaneCanonicalization` cases in `app/tests/test_main_api.py`.

## Out of scope (later units)

`planning.py` partition, `shadow.py`, the `judgment.py` prompt, the frontend,
and `sequence.py`. None was changed here.

## Note on the brief's field assumption

The plan assumed `native_blocks <= 1/3` expresses "10 minutes". It does not:
`main.resolve_assigned_blocks` quantises the resolved block count UP to whole
30-minute blocks, so a 10-minute Todoist task reaches the route as
`duration: 10, blocks: 1`. The classifier therefore reads exact minutes from
`duration_minutes`/`duration` first and only falls back to
`native_blocks`/`blocks` × 30. Without that, the 10-minute half of the rule
could never fire.
