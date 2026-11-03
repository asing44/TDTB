# Source refresh latency (2026-11-03)

Research note, read-only, from code. Provider doc URLs and wall-clock timings were not verified.

## What one refresh costs

- Frontend `refreshSources()` (`frontend/src/store/controller.ts:440`) = one `GET /plan-inputs` + one `GET /billed-ledger`; all provider cost is inside `/plan-inputs` (`app/main.py:5008-5540`).
- Todoist, every request, no cache: 2 filter reads (assigned + quick pool), projects, labels, one read per habit project, a `get_task` probe per micro-adventure candidate (`app/external_sources.py:208,336-404,511-548`; `app/main.py:2942-2957`).
- Capacities legacy live path: 1 structures read, a listing per contributing structure, then per-object content reads capped at 20 — about 23 requests cold (`app/capacities_adapter.py:169-176,1410,1681-1700`). Listings carry no typed properties, so each property decision costs a content read.
- Capacities direct intake: `/plan-inputs` makes zero Capacities calls; the paced work runs in the background job `/capacities/refresh/start` (`app/main.py:4399-4500`).
- Calendar is local EventKit.

## Limits

- Capacities: 30 requests/minute, asserted in code (`app/capacities_adapter.py:38-40`, `app/capacities_refresh.py:182-189`). Official docs not checked.
- Todoist: single `Retry-After` retry capped at 30s (`app/todoist_client.py:74-85`). Official limits not checked.

## Existing mechanisms

- Used: Capacities content cache (300s TTL), direct-refresh read-through TTL (600s), paced background job.
- Absent: Todoist Sync API `sync_token`, ETags, per-source partial refresh, server-side `/plan-inputs` cache.

## Done in this change

- `stagingAction` (done/drop/unassign/delete) no longer awaits a source refresh; the row leaves the view locally via `hideFromView`, and Undo restores it (`VIEW_UNHIDDEN`).

## Ranked options for the rest

1. Extend optimistic local update to the still-awaited refreshes: reassign, hide_always, batch apply/undo, exclusion saves (controller.ts ~:584,602,622,659,732,1071,1122,1381,1415,1511). Low effort, low risk.
2. Stale-while-revalidate for `/plan-inputs`: serve last-good, rebuild in background. Medium effort; digest fingerprint consistency is the risk.
3. Per-source partial refresh (`?source=todoist`). Medium-high; digest handling is whole-digest today.
4. Todoist Sync API incremental sync. High effort and risk; filter reads have no verified sync equivalent.
5. Raise the existing TTLs (300→600s). Trivial; slower edit propagation.
