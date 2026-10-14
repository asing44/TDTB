# 2026-10-13 — Clock-anchor staleness (successful plan immediately stale)

User-reported: a successful `/sequence` plan became stale on the next
`/validate-sequence`, forcing a billed re-sequence. Preview/revalidate was
unusable while the budget approached zero. Source-only repair; no live runtime
was started, billed, committed, or written. All changes remain uncommitted.

## Root cause

`app/planning.py::_time_frame_identity` recorded the live clock anchor in the
runstate snapshot (`time_frame.anchor`). A proposal at clock 15:15 stored
`anchor: "15:15"`; revalidation at 15:16 recomputed `anchor: "15:30"` and the
snapshot comparison failed with `planning snapshot is stale`. The anchor was
treated as identity even though it is a moving clock projection.

## Fix (source-only, `app/planning.py`)

New snapshots record anchor *intent*, never the moving clock anchor:

- `anchor_source`: `"clock"` or `"override"`.
- `anchor_override`: canonical `HH:MM` (`time_engine.to_hhmm`) or `null`.
- `effective_eod` / `config_eod`: the stable EOD bounds.

Intent is derived from the prepared `day_setup.anchor`, mirroring `main.py`'s
`anchor_override=time_engine.to_hhmm(day_setup.get("anchor"))` exactly, so a
junk/absent anchor means clock mode, as the live frame computes it.

`_snapshot_projection` now preserves the stored `time_frame` keys verbatim
(canonical time values only); it no longer fabricates an intent. Comparison:

- New-format snapshot (`anchor_source` present): exact comparison of
  `anchor_source`, `anchor_override`, `effective_eod`, `config_eod`. The
  moving clock anchor is never compared.
- Legacy snapshot (raw `anchor`): see below.

Deliberate override add/change/remove, effective/config EOD changes, calendar
boundary changes, pins, grants, and config fingerprints still make the
snapshot stale. The live frame is still fed into placement validation, so a
row before the advanced anchor keeps its soft `placement_past` acceptable-defect
warning and is never dropped; immutable rows and the commit gate are unchanged.

## Intentional safety preservation

`_time_frame_identity` is only reached for new snapshots. A clock-mode
proposal is now stable across the clock advancing, which is the reported
defect; nothing else about validation or the commit path changed.

## Legacy snapshots (already-paid plans)

A legacy snapshot written before this change has no `anchor_source`. It is
accepted only under the safe rules:

- Stable bounds: any `effective_eod`/`config_eod` the legacy snapshot recorded
  must still match; the stored anchor must be within the live effective EOD.
- Live explicit override present: the stored anchor must equal the live
  canonical override (same effective frame).
- No live override (clock): the stored anchor is trusted only when it is a
  valid canonical `HH:MM`, aligned to the live `anchor.round_to_minutes`, at
  or before the live clock anchor, and at or before the live effective EOD.
- Anything else — missing/malformed anchor, off-grid anchor, a future anchor,
  midnight wrap — fails closed as stale.

Root's read of today's runstate (`anchor: ""` top level, stored
`time_frame.anchor: "15:45"`, legacy keys `anchor`/`config_eod`/`effective_eod`)
corroborates clock mode because no in-app route writes an empty anchor:
`/day-setup` writes `anchor` only when truthy and `_day_setup_with_prompts`
drops `""`/`None`.

## Legacy limits (not claimed otherwise)

- An override that was removed **out of band** cannot be fully reconstructed
  from a legacy snapshot; the clock rule may accept it when the stored anchor
  happens to be a valid aligned past anchor. No in-app route clears the
  override, so this is a bounded, documented legacy limitation.
- Legacy snapshots that omit `effective_eod`/`config_eod` are compared only on
  the fields they recorded; the anchor plus the live EOD bound still gate
  acceptance.

## Characterization, not repaired

The default Mint fill is derived from the live anchor
(`external_sources.build_schedulable_blocks`). Once the clock passes the last
window session (16:30 for the standard windows) the aggregate `Minting` row is
no longer injected, and a stored plan that still contains it revalidates as a
foreign row (fails closed). This is characterized by a dedicated expected-
failure test; the Mint algorithm was deliberately left unchanged.

## Verification actually run (source only)

- Red first: focused `app/.venv/bin/python -m pytest
  tests/test_sequence_planning.py tests/test_validate_sequence.py -q` — 6 new
  behavior tests failed (clock-advance no-stale, legacy aligned-past accept,
  placement-past preserved, `run_proposal` intent, route clock survival).
- Green: same focused selection — 45 passed.
- Full backend: `app/.venv/bin/python -m pytest tests/ -q` — 3085 passed,
  1 warning, including the bootstrap tests.
- No frontend suite was run (a concurrent frontend child owns those paths).
- No live `:8746`/`:8790` call, no billed `/sequence`, no source write, no
  commit, no budget reset, no runstate migration. No live-runtime verification
  is claimed.
