# Capacities "Assigned" query vs TDTB loading — discrepancy record (2026-10-30)

Read-only comparison. Nothing in Capacities or TDTB was changed.

## Sources

- Owner's Capacities query **Assigned** (11 objects) — screenshots of the query editor and the result list.
- TDTB live state at `b86626f`: `GET /plan-inputs`, `/settings/capacities`,
  `/settings/capacities/source`, `/capacities/selections`, `/capacities/refresh/status`.
- Live Capacities objects read through MCP (`getObjectContent`) for the disputed rows.

## The two sets

Owner's query, 11 objects: Task 1 (Plants search) · Print 2 (Spider PRANK,
Little Halloween ghost) · Project 3 (Plant Stand Staining, WALL⋅E-OS Capacities
Buildout, Garage Buildout) · Continual 4 (Event Scheduling, Semi-Monthly
Adventure, Semi-Weekly Reflection, Press) · Event 1 (Halloween).

TDTB, 17 Capacities rows = **9 admitted by TDTB logic** + **8 owner selections**:

| Admitted by logic (9) | Type | In query? |
| --- | --- | --- |
| Plants search | Task | yes |
| Garage Buildout, Plant Stand Staining, WALL⋅E-OS Capacities Buildout | Project | yes |
| Buildout Deep CWEAN items, Reading, Wire up n8n flows, press | Task | no |
| Chistmats Gifts | Project | no |

| Owner selections (8) | Type | In query? |
| --- | --- | --- |
| Upper, Upper Fast | Press | no |
| Bird Whistle, Liter Box Automatic, Longer macos mini cord, Mazda Charger, OOTS Bed in Office, Router | Purchase | no |

## Missing from TDTB (7)

| Object | Type | Live state in Capacities | Why TDTB drops it |
| --- | --- | --- | --- |
| Spider PRANK | Print | `assigned: true`, status Printing | Print is not an active structure, and its Assigned-On property `759d2b92` is reported *unknown* |
| Little Halloween ghost (LED tealight compatible) | Print | `assigned: true`, status Printing | same |
| Event Scheduling | Continual | object exists, listed under the query | Continual is not active; its Assigned-On property `9a47c3c0` is *unknown* |
| Semi-Monthly Adventure | Continual | " | same |
| Semi-Weekly Reflection | Continual | " | same |
| Press | Continual | " | same — note TDTB's "press" row is a *Task* with that name, not this Continual |
| Halloween | Event | `assigned: true`, status Planning, date 2026-10-31 | Event is active and its boolean resolves, but the future-date filter drops it (`due > logical_day`) |

## Extra in TDTB (13)

| Row | Type | Why it is there | Why the query omits it |
| --- | --- | --- | --- |
| Buildout Deep CWEAN items, Reading, Wire up n8n flows, press | Task | Task date/deadline horizon + open status | the query limits Tasks to Date/Deadline within this week |
| Chistmats Gifts | Project | Project status gate (In Progress/On Hold) | deadline 2026-10-29 already passed (`stale_assigned`) |
| Upper, Upper Fast | Press | **owner selection**, flagged `status_unknown` | Assigned is unchecked (`assigned: false`, boolean `null`), and their status option id is dead (name equals its id) |
| Bird Whistle, Liter Box Automatic, Longer macos mini cord, Mazda Charger, OOTS Bed in Office, Router | Purchase | **owner selections**, flagged `status_unknown` | Purchase branch needs Assigned selected and a live status |

## Root causes, in order of blast radius

1. **Three types were never re-read by the last refresh.** Warnings say
   Purchase `b0ef1817`, Print `ced0f1b5` and Continual `e16f8eeb` are
   "unchanged since" earlier dates. Their property and status data is frozen,
   so a new boolean `Assigned` cannot be seen.
2. **Dead assignment declarations.** Project `0d194525` → `66a32af0`,
   Continual `e16f8eeb` → `9a47c3c0`, Print `ced0f1b5` → `759d2b92` are all
   reported *unknown property* and ignored. The mapping still points those
   three types at the deleted Assigned-On **date** properties.
3. **Print, Continual and Purchase are not active structures.** Even with a
   valid declaration, print and continual objects have no admission path.
4. **The future-date filter outranks the query.** Event, Purchase and any type
   with a date property drops objects dated after today, while the owner's
   query has no such condition (Halloween is the visible casualty).
5. **Selections outlive the source.** Eight acknowledged selections keep Press
   and Purchase rows in the plan that the query would not return, and six of
   them are flagged `status_unknown` on the plan row.
6. **Task admission is broader than the query.** TDTB uses a date/deadline
   horizon rather than "Date or Deadline within this week", so four date-less
   tasks appear.

## Corrections to the record

- `assigned_structures` currently names the new booleans for **Press**
  (`e56c01ad`) and **Event** (`f74cde3d`); those two ids do resolve.
- Live Print/Press/Purchase objects carry `assigned` as a boolean property
  (`614a706a` on Print, `e56c01ad` on Press), never a date.
- The live Print status option set still contains `Printing`, so the Print
  status list in the mapping is not the blocker; the missing type is.

## Open questions for the logic decision

1. Should TDTB mirror the query's semantics, or read the query's result set as
   the candidate list?
2. Should "Assigned" stay a per-type boolean declaration, or become one shared
   property name resolved per type at refresh?
3. Should the future-date filter keep outranking assignment for Events?
4. Should a selection survive when the source no longer marks the object
   assigned (the six `status_unknown` purchases)?

## Trace: why the last refresh skipped three types

`app/capacities_refresh.py:1059` lists only the mappings where
`adapter.can_contribute(mapping)` is true. `_structure_can_contribute`
(`app/capacities_adapter.py:2487`) returns true for: a native-task structure, a
resolved `assignment_property`, a resolved `assigned_on_property`, a structure
in `active_structures`, or an active stored rule.

Purchase, Print and Continual satisfy none of those:

- their declared `assigned_on_property` ids (`759d2b92`, `9a47c3c0`, and
  `66a32af0` for Project) no longer exist, so contract resolution drops them
  with the "unknown property" warning and the field becomes `None`;
- none of them declares a boolean `assigned_structures` entry;
- none is in `active_structures`.

So they fall out of `listing_types` and into `retained_types`
(`capacities_refresh.py:1085`), which keeps their previous membership **and
their previous `type_check_times`** (`capacities_refresh_state.py:1000-1013`).
That is the "unchanged since" warning: a retained type is reported as stale
because it was never checked, not because Capacities reported no change.

**This is a deadlock.** A type whose only assignment path is a dead declaration
is never enumerated, so it can never re-read the contract, so it can never
discover the boolean that replaced the dead date property. Nothing in the
current design breaks the loop; only a hand edit of the mapping (or adding the
type to `active_structures`) does.

## Live properties (read 2026-10-30 through Capacities MCP)

| Type | structure id | live `Assigned` boolean | TDTB declares today |
| --- | --- | --- | --- |
| Task | `RootTask` | — (query uses date/deadline) | none, native date gate |
| Project | `0d194525` | `6cdb0e7b-62fd-492f-beef-bbf867a3f475` | dead date `66a32af0` |
| Press | `6aa7b02a` | `e56c01ad-4a52-49fc-b22a-685ea0f6f2c3` | same, resolves |
| Event | `80c91235` | `f74cde3d-c228-4dbc-957a-42600c57963f` | same, resolves |
| Purchase | `b0ef1817` | `b73dc8e3-7b4e-456a-86ce-44510549b85d` | none |
| Print | `ced0f1b5` | `614a706a-bdd2-4e44-b14e-d9e95306d92d` | dead date `759d2b92` |
| Continual | `e16f8eeb` | `ea1e95a6-4ed6-4824-a1fb-77cf7fabe771` | dead date `9a47c3c0` |

Live status sets: Project Todo/Started/In Progress/On Hold/Completed/Dropped ·
Purchase Todo/Researching/Watching/Saving/Ready/Purchased/Dropped ·
Continual has **no** single Status label — `Daily Status` (Completed/Skipped/
Incomplete) plus `Operational Status` (Healthy/Warning/Failing/Archived) ·
Print keeps `Printing`.

Owning decision (2026-10-30): the assignment feed is curated **in Capacities**
first; TDTB then loads what Capacities marks, using each type's own properties.

## Three directions considered

| | A. Mirror the query via per-type declarations | B. Read the query result set as the source | C. Rewrite admission around one shared property |
| --- | --- | --- | --- |
| What changes | mapping only: declare the live boolean for every type, drop dead date declarations | new provider path that evaluates a saved Capacities query and returns its objects | new evaluator: one property *name* resolved per type at refresh, status lists kept, date gate demoted |
| Matches the owner's curation | yes — Capacities stays the decider | exactly, including query edits made later | yes, and it tolerates future property changes |
| Effort | small (settings + refresh + tests) | large (new API surface, caching, failure modes) | medium-large (new evaluation path, migration) |
| Breaks the deadlock | yes — a declared boolean makes the type contribute again | n/a | yes |
| Risk | none to admission beyond the types going from retained to read | the query API is not part of the current contract; failure would empty the plan | re-plumbing every admission test |

Recommendation: **A now, C later if the per-type map churns again**; keep B as a
fallback only if Capacities exposes a stable query endpoint.

## Proposed first build step (after this trace)

1. Decline the three dead Assigned-On declarations and add boolean declarations
   for Project `6cdb0e7b`, Print `614a706a`, Purchase `b73dc8e3`, Continual
   `ea1e95a6`. Keep Press `e56c01ad` and Event `f74cde3d`.
2. Run one real Refresh and confirm all seven types appear in
   `type_check_times` with one timestamp.
3. Re-run this diff and expect: Print (2), Continual (4) and Halloween (1)
   either loaded or explicitly dropped by a rule the owner agrees with.

## Decisions still open

1. **Future dates.** For an explicitly Assigned object dated later (Halloween,
   31 Oct): load it, or keep the date filter? Recommendation: load it — the
   owner curates in Capacities, and the date filter is TDTB's own invention.
2. **Stale selections.** Six of the eight selections point at objects the feed
   does not mark assigned. Recommendation: expire a selection at the next
   refresh when the source no longer marks the object assigned, and keep the
   acknowledged flag only for still-assigned objects.
3. **Task breadth.** TDTB admits four tasks the query does not. Recommendation:
   keep TDTB's date/deadline horizon (it is intentional) and treat the query as
   a curation surface, not a completeness proof.
4. **Continual status.** With no single Status label, what should gate a
   Continual? Recommendation: Assigned only, plus `Daily Status` empty to match
   the query's branch.

## Applied (owner approved 2026-10-30)

Settings `revision 32 -> 34`, source mapping `revision 18 -> 19`:

1. Dropped every dead `assigned_on_structures` entry (Project `66a32af0`,
   Print `759d2b92`, Continual `9a47c3c0`) — the map is now empty.
2. Declared the live boolean `Assigned` for Print `614a706a`, Purchase
   `b73dc8e3` and Continual `ea1e95a6` (Press `e56c01ad` and Event `f74cde3d`
   were already correct). Project deliberately left on its status gate, which
   matches the owner's query branch.
3. Mapped Continual's status property to `Daily Status` (`0caded0e`) with the
   open value `Incomplete`, because a custom structure needs an assignment or
   status mapping before the contract will publish.
4. **Enabled Purchase, Print and Continual as active sources.** This is the
   part that matters: `_resolve_assigned_structures` deliberately keeps a
   boolean declaration out of `_structure_can_contribute`, so declaring a
   boolean can never make an inactive type enumerate. Only an active flag (or a
   legacy marker, an Assigned-On declaration, or a stored rule) does.

One full Refresh (job `803a635f…`/next, 355 objects listed, all seven types
read) then produced:

- **All dead-declaration and "unchanged since" warnings are gone.**
- Print now loads both objects: Spider PRANK, Little Halloween ghost.
- Continual now loads all four: Event Scheduling, Semi-Monthly Adventure,
  Semi-Weekly Reflection, Press.
- Purchase now admits only assigned objects: Bird Whistle and Router dropped
  out (they were unassigned cache leftovers).
- Capacities rows went 17 -> 21; the six `status_unknown` selections remain
  (Upper, Upper Fast, and four purchases).

Still open, unchanged by the remap: Halloween (Event, dated 31 Oct) is dropped
by the future-date filter, and four date-less tasks plus Chistmats Gifts are
broader than the owner's query.

## Pros and cons of each direction

**A. Mirror the query with per-type declarations** (applied above)
+ Small, inspectable, and every rule stays where the owner can see it (Settings).
+ Breaks the retained-type deadlock as soon as a type is enabled.
+ No new provider surface, no new failure mode.
− Two knobs per type (Active + Assigned property), and forgetting Active is a
  silent no-op — exactly the trap hit today.
− Needs a hand edit whenever Capacities gains a type or renames a property.

**B. Read the query result set as the source**
+ Matches the owner's curation exactly, including future edits to the query.
+ No per-type mapping to maintain.
− Needs a provider endpoint TDTB does not have today (the query is a UI object;
  there is no documented API for it), plus caching, pagination and failure
  modes. A query rename or share change would empty the plan silently.
− Loses per-type duration/status semantics TDTB needs to plan.

**C. One shared assignment property resolved per type at refresh**
+ Tolerates property churn: a new boolean named "Assigned" is picked up without
  a settings edit; also removes the Active/Assigned two-knob trap.
+ Keeps per-type status and duration semantics.
− Largest rebuild: contract resolution, every admission test, and a migration
  for existing declarations.
− Resolution by name is a guess when two properties share a name.

## Decisions settled 2026-10-30 (owner)

1. **Tolerance**: resolve a dead declaration by property name once, record
   `declared -> resolved` with a timestamp and surface it, warn actionably, and
   never let a type freeze on stale timestamps. Ships with a chaos test that
   deletes a property mid-refresh.
2. **Settings**: one admission dropdown per type — Off / Status only / Requires
   Assigned checkbox / Requires Assigned date — which sets the Active flag
   itself, picks the property from the live schema instead of a typed GUID, and
   shows a preview count from the last refresh.
3. **Query import**: mirror the rules in TDTB as the source of truth, with an
   optional CSV-export reconcile path. No scraping, no waiting on the API.

### Why the query itself cannot be imported (evidence, 2026-10-30)

- `https://api.capacities.io/openapi.json` v1 has 17 paths and no query
  surface; the only reads are `/objects/search` (title + semantic, returns
  `{id, structureId, title}`), `/objects/structure`, `/objects/tag`,
  `/objects/collection`.
- The owner's query is object `629d010c-4a0d-408a-9397-f81379eba55c`
  (structure `RootQuery`). It lists and reads, but returns only title, icon and
  a null description; `readObjectBlocks` returns `{}`. Filters and results are
  computed app-side and never serialized.
- Capacities' open feedback request asks for enumeration + incremental sync +
  MCP/REST parity — roadmap work is enumeration, which still would not evaluate
  a saved query.
- Supported escape hatch: in-app query export (query -> ... -> Export -> CSV).
- Rejected: scraping the Electron app's IndexedDB (locked, unsupported, breaks
  on update) and headless browser automation of the web app (auth + DOM
  fragility).
