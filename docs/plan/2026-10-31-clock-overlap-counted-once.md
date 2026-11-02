# Clock overlap counted once (2026-10-31)

## Problem

The Allotment breakdown double-counted shared clock time. Calendar events and
anchored blocks that occupy the same minutes were each charged their full size,
so `free` (and task room) came up short by the overlap. Reported from the live
"Shape your day" screen: a 5:30p cooking event over Sudsing (5:30p, 30m) and the
Foods Dinner window charged the day twice for the same half hour.

Reproduction before the fix (`_capacity_frame`, 15:15 frame, 17:30–19:00 fixed
event, Sudsing 17:30–18:00 + Foods Dinner 18:00–19:00): `fixed 3, anchored 3,
free 9` — six blocks of claims for three blocks of clock.

## Contract

- Segments keep their own sizes. The operator still sees Calendar 2h and
  Anchors 3h; nothing is silently shrunk to make the arithmetic add up.
- `capacity.overlap` is the shared amount, in blocks. It is returned to `free`
  and `available_for_selection` exactly once, and typed on both wire shapes
  (`/plan-inputs` flat, `/capacity-preview` under `segments`).
- Clock-bound claims are calendar `fixed`, calendar `work` meetings and
  anchored blocks. The part of Mint that is a bare allotment is a reservation,
  not clock time, and is never credited.
- A flexible window anchor (`Type: window` with an `End`) only overlaps when
  its window has no room left. It slides into free time inside its window
  before it is ever charged as shared, so the credit can never overstate free
  time. Anchors are walked in config order and consume the earliest time their
  window still leaves free, which also dedupes anchor-on-anchor claims.
- An anchor with no parseable Start has no clock position: it costs capacity
  but claims nothing shared.

## Surfaces

- `app/capacity.py`: `compute_capacity(overlap=...)` + interval helpers
  (`merge_intervals`, `interval_minutes`, `free_spans`).
- `app/main.py`: `_calendar_intervals`, `_anchor_claim_window`, and the
  overlap assembly in `_capacity_frame`.
- `frontend`: `Capacity.overlap`, Allotment "shared — counted once" line,
  Capacity card and Rail ledger credit, allocation pie and export prompt
  `shared` component. Text ledgers keep every segment at its own size; where
  the day is *drawn* (`model/capacitySegments.ts`) the credit comes out of the
  anchored slice first, then calendar, so bars, wedges and the day-capacity
  marker still line up with the real end of the day.

## Verification

- `app/tests/test_clock_overlap.py` — 13 cases: rigid anchor inside a
  meeting, anchor-on-anchor, flexible window with and without room,
  work-over-fixed, abstract allotment never credited, no-overlap regression,
  out-of-frame anchor, Start-less anchor costing capacity without credit,
  canonical readout.
- `frontend/src/ui/day-setup/AllotmentCard.test.tsx`,
  `frontend/src/ui/capacity-card.test.tsx`,
  `frontend/src/adapters/forgot.test.ts`, `frontend/src/model/pie.test.ts`,
  `frontend/src/model/capacitySegments.test.ts` — projection, rendering, wedge
  partitioning and the drawn-geometry credit.
- Full backend suite and full frontend suite (the five
  `test_judgment*` selector/params failures are pre-existing on `main`).
