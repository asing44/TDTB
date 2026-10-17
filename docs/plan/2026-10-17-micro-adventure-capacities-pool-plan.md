# Micro-adventure Capacities pool: plan (2026-10-17)

Status: planned, not started. Base: main afeb931. Replaces the non-called stub from 5946fdb.

## Decisions (owner-approved 2026-10-17)

- **Fetch path:** the tdtb-refresh artifact. New optional top-level block `micro_adventure_ideas`. The app never reads Capacities live for this. Readable in both source modes; ignores logical_day.
- **Source of ideas:** Capacities **Event** objects that are either
  - in the collection **"Personal Adventures"** AND have `effort` = small or empty, OR
  - tagged **#_TDTB-live** (any effort).
  The idea text is the object title.
- **Merge:** append after the static config pool; dedupe by id and normalized text. Capacities ideas take at most half of the 8-item `live_pool`. Empty, failed, or stale results fall back to the static pool, then `FALLBACK_POOL`.
- **Category/effort fields:** not mapped into the pool entry beyond what the filter needs.

## Cost note

Collection and tag membership are free listings. The effort rule needs one content read per object listed in "Personal Adventures" but not tagged #_TDTB-live, under the read cursor (24 reads/60s). Tagged objects need no read.

## Slices (each <= ~40 tool calls)

1. **S1 pure core** (`app/micro_adventure.py`, `app/app_config.py`, stub test + new `app/tests/test_micro_adventure_capacities_pool.py`): normalizer, merge/dedupe, half-pool cap, statuses `disabled|not-configured|ok|stale|unavailable`, config keys (`source`: collection name, `tag`, `effort_property`, `effort_allowed`, `merge`). Accept: `enabled:false` yields today's pool; malformed block yields config pool plus warning.
2. **S2a artifact consumer** (`app/artifact_source.py`, `tools/validate_artifact.py`, tests, fixture): `load_micro_ideas()` like `load_habits`; block optional; bounded (200 items, 200 chars); stale when `read_at` older than `refresh_cadence_hours`; never raises.
3. **S2b producer** (`app/producer_rules.py`, `tools/produce_rows.py`, new `tools/micro_ideas_spec.py`, tests): source entry in, normalized block out; absent entry means no block.
4. **S3 app wiring** (`app/main.py` `_micro_adventure_state` + micro payload only; flip the stub test's not-referenced assertion): all three callers share one pool; payload adds `pool_source`, `pool_warnings`.
5. **S4 frontend** (`wire.ts`, contract fixtures, `DaySetupPanel.tsx`, micro test; route to `design` unattended): tolerate new fields; one-line degraded note only when warnings exist.
6. **S5 docs and skill** (`skills/tdtb-refresh/SKILL.md` step 3b, README): list collection and tag, cursor-read effort, emit source entry. First real refresh is the owner's call (needs MCP).

S2a and S2b can run in parallel after S1.

## Risks

- Renamed collection or tag silently empties the pool: producer must warn when an enabled filter lists 0 objects.
- Cursor is coverage, not change detection: an effort edit on an already-read object is missed until the cache entry is deleted.
- Stale block when a refresh skips step 3b: app falls back to config with a warning.
- Near-duplicate ideas with different text are both kept.

## Unchanged

History keyed by id, streak, pending_confirm, loose-end flow, Live-to-Todoist reroute, never-bump rule, commit gate.

## Verified 2026-10-17 (read-only MCP)

- Collection "Personal Adventures" exists: 7 Event objects (structure 80c91235-c892-4bff-a1ed-0ce19e48031e): FlyFishing, Coffee crawl, South Korea, Guitar Lessons, Kayaking, Gardens Sake, Frontier Flight.
- Tag `_TDTB-live` exists: 1 object (Gardens Sake, also in the collection).
- Event `effort` is a label property (frontmatterKey `effort`): Small, Medium, High. The rule is effort = Small or empty (case-insensitive); the 7 content reads are cheap.
