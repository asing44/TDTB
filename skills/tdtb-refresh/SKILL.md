---
name: tdtb-refresh
description: >-
  Regenerate the TDTB planning artifact (state/planning-artifact.json) from
  Todoist and Capacities over MCP. The skill fetches raw source data, hands it
  to the deterministic producer CLI, validates the result, and writes it
  atomically. Trigger on "refresh tdtb", "tdtb refresh", "regenerate the
  planning artifact", "rebuild the planning artifact", "pull my todoist into
  tdtb", "pull my capacities into tdtb", "update today's plan sources",
  "refresh today's tdtb plan", or when the cockpit says the planning artifact
  is missing, stale, or empty. Do NOT use for editing the rule set by hand, or
  for placement or commit (that is the app's job).
---

# TDTB planning-artifact refresh

You are the **producer** half of the artifact-contract pivot. The app no
longer reads Todoist or Capacities live in artifact mode; it consumes ONE
normalized file that this skill regenerates. You do the part only an agent can
do — speak Todoist and Capacities over MCP. **You never decide what is
admitted**: a deterministic CLI (`tools/produce_rows.py`) applies the
operator's rule file and emits the artifact. Do not re-rank, filter, rename, or
"improve" rows yourself.

## Contract

- **Artifact**: `state_dir()/planning-artifact.json`, i.e.
  `~/.config/tdtb/state/planning-artifact.json` (honours `TDTB_HOME`).
- **Rules**: `~/.config/tdtb/producer-rules.json` — first-match-wins in file
  order. A canonical starter lives at
  `skills/tdtb-refresh/producer-rules.json`; if the operator's file is
  missing, copy that starter there and say so.
- **Producer**: `<TDTB_REPO>/tools/produce_rows.py`, run with the TDTB venv
  interpreter (default `/Users/walle-mini/Repos/Projects/TDTB/app/.venv/bin/python`).
- **Validator**: `<TDTB_REPO>/tools/validate_artifact.py`.
- **Read cursor**: `<TDTB_REPO>/tools/read_cursor.py`, backed by
  `~/.config/tdtb/state/producer-cache.json`. It paces and remembers Capacities
  content reads so a run that hits the budget resumes instead of restarting.
- The write is atomic and retains one prior copy; never write the artifact
  with a bare file write.

## Procedure

### 1. Confirm the rules file exists.

If `~/.config/tdtb/producer-rules.json` is absent, copy
`skills/tdtb-refresh/producer-rules.json` to it and tell the operator you
installed the starter. Do not edit the rule set unless asked.

### 2. Fetch Todoist over MCP.

Resolve the operator's queries with these tools, then collect the raw task
objects:
- `todoist_find-filters` — the operator's saved filters (resolve the assigned
  and pool queries by name; do not invent queries).
- `todoist_find-projects` — project list, so project-scoped rules resolve.
- `todoist_find-tasks` — the open tasks that match the assigned/pool queries.
- `todoist_find-tasks-by-date` — tasks due the logical day (and any date the
  operator names).
- `todoist_find-completed-tasks` — completion state, so already-done ids are
  excluded before you assemble the source JSON.

Merge the open results, dedupe by task id, and drop completed ids. The rules
file decides pooling and assignment — you only supply the candidate tasks.

### 3. Fetch Capacities over MCP — list for free, read in budget.

Every Capacities **content read costs one of the API's 30 requests / 60
seconds**, so spend as few as possible:

1. **Free pre-filter by tag or collection.** When a rule names a tag or
   collection, call `capacities_listObjectsByTag` or
   `capacities_listObjectsByCollection`. **Membership IS the tag/collection: one
   listing, ZERO content reads.** Put the membership on each listed object
   (`collections` / `tags`) so the deterministic predicate can read it without a
   content read.
2. **Resolve each structure once.** Call `capacities_getObjectTypeShape` once
   per contributing structure before interpreting properties — never per
   object — so you know each property's name and type.
3. **Read properties under the cursor, in budget.** Only objects that need
   property values beyond membership cost a content read. Write the listed
   objects (with `id`, `structureId`, `spaceId`, `collections`, `tags`) to a
   file and ask the cursor what to read:

   ```bash
   TDTB_REPO=/Users/walle-mini/Repos/Projects/TDTB-worktrees/cockpit-ui-round2
   PY=/Users/walle-mini/Repos/Projects/TDTB/app/.venv/bin/python
   "$PY" "$TDTB_REPO/tools/read_cursor.py" plan \
     --input /tmp/tdtb-capacities-listed.json > /tmp/tdtb-capacities-plan.json
   ```

   Read content only for the ids in `need_read`, one object at a time over
   MCP, and record them:

   ```bash
   "$PY" "$TDTB_REPO/tools/read_cursor.py" record \
     --input /tmp/tdtb-capacities-read.json > /tmp/tdtb-capacities-recorded.json
   ```

   The cursor enforces **24 content reads per 60 seconds** (headroom under the
   API limit) and skips anything a previous run already read. If `plan` reports
   `"status": "partial"`, carry its `deferred` count into the source block and
   set the source `status` to `partial`; the next run continues from the
   cursor. Do not try to work around the budget.

### 4. Assemble the source JSON exactly in the CLI's shape, then produce.

Todoist counts live under `tasks`; Capacities objects under `objects`. A
Capacities entry carries the space id and each object's typed `properties`
(a content read), plus `collections`/`tags` (the free listing). Use
`"status": "partial"` with an explanation in `warnings` if a fetch degraded or
the cursor deferred objects, and `"failed"` if a source could not be read at
all.

```json
{
  "todoist": {
    "status": "ok",
    "read_at": "<ISO-8601 with offset>",
    "warnings": [],
    "tasks": [ "<raw Todoist task objects>" ]
  },
  "capacities": {
    "status": "partial",
    "read_at": "<ISO-8601 with offset>",
    "space_id": "<capacities space id>",
    "deferred": 6,
    "warnings": ["read budget reached; 6 objects deferred to the next run"],
    "objects": [
      {
        "id": "<object id>",
        "structureId": "<structure id>",
        "title": "...",
        "collections": ["Inbox"],
        "tags": [],
        "properties": { "<Property Name>": "<typed payload>" }
      }
    ]
  }
}
```

Evaluate deterministically and write the artifact:

```bash
TDTB_REPO=/Users/walle-mini/Repos/Projects/TDTB-worktrees/cockpit-ui-round2
PY=/Users/walle-mini/Repos/Projects/TDTB/app/.venv/bin/python
"$PY" "$TDTB_REPO/tools/produce_rows.py" \
  --rules ~/.config/tdtb/producer-rules.json \
  --logical-day "$(date +%F)" \
  --write < /tmp/tdtb-source.json > /tmp/tdtb-artifact.json
```

Exit codes: `2` input not JSON, `3` rules invalid (the offending rule id is
printed), `4` the built artifact failed its own validation (nothing was
written), `5` write failed. On any non-zero exit, stop and report.

### 5. Validate before trusting it.

Always re-run the validator on what was written, and report its result:

```bash
PY=/Users/walle-mini/Repos/Projects/TDTB/app/.venv/bin/python
"$PY" /Users/walle-mini/Repos/Projects/TDTB-worktrees/cockpit-ui-round2/tools/validate_artifact.py \
  ~/.config/tdtb/state/planning-artifact.json
```

### 6. Report to the operator.

Read the artifact's `admission` block and state, per rule id: how many records
matched, were admitted, and were dropped (`admission.per_rule`). Then list the
most recent drops with the rule that dropped them (`admission.recent_drops` —
`rule: null` means no rule matched). Name the artifact path and its
`logical_day`. If any `sources.<name>.status` is `partial` or `failed`, surface
its `warnings` and `deferred` count verbatim.

## Cursor limitation — read this before trusting the cache

The read cursor is **coverage, not change detection**. Capacities exposes no
guaranteed `updatedAt` (it is only a property type if the structure happens to
define one), so the cursor records *what has already been read* and nothing
else. A cached object is skipped on the next run **even if it changed since it
was read**, and nothing in this workflow will notice. A stale Capacities row is
therefore possible and silent.

To force a re-read, delete `~/.config/tdtb/state/producer-cache.json` (or the
one object's entry) before running the plan step. Do not describe or rely on
the cache as change detection.

## Rule schema (what the operator edits)

`producer-rules.json` is `{"version": 1, "rules": [ ... ]}`. A Todoist rule is:

```json
{
  "id": "unique-rule-id",
  "source": "todoist",
  "when": { "all": [ { "prop": "labels", "op": "in", "values": ["@work"] } ] },
  "assigned": true,
  "pool": false,
  "admit": true
}
```

A Capacities rule adds `structure` (required: the structure id or title the
rule scopes) and an optional `duration_prop` (the property that carries the
duration in minutes):

```json
{
  "id": "capacities-inbox-assigned",
  "source": "capacities",
  "structure": "Project",
  "duration_prop": "Duration",
  "when": { "prop": "collections", "op": "in", "values": ["Inbox"] },
  "assigned": true,
  "pool": false
}
```

- `when` is recursive: a combinator `{"all": [...]}`, `{"any": [...]}`,
  `{"not": {...}}`, or a leaf `{"prop", "op", "values"}`.
- Ops: `eq`, `in`, `exists`, `truthy`, `lt`, `gt`, `before`, `after`,
  `matches`. The value token `"$today"` resolves to the run's logical day.
- `prop` is a dotted path into the record. For Capacities, the typed property
  payloads are flattened deterministically by the producer (text/number/boolean
  to their value, a date to its start, a label/entity to its name), so a rule
  says `{"prop": "Duration", "op": "gt", "values": [60]}`, not a payload path.
- **First-match-wins**: the first rule whose `source` matches and whose `when`
  holds decides the record, so exclusion rules must come before admission
  rules. A record no rule matches is dropped. A Capacities rule also requires
  the record's structure to match its `structure`.
- `admit: false` excludes a matching record and records the drop against that
  rule id.
- `pool: true` emits the row with `assigned: false` (O1), regardless of
  `assigned`.

## Do not

- Do not hand-edit `planning-artifact.json` after producing it — use the
  hand-edit overlay (`state/planning-overlay.json`) for durable edits.
- Do not change `sources.mode` in `config.json`; that is the operator's opt-in.
- Do not add rows the rules dropped, or rename rows to dodge a duplicate. If a
  rule set looks wrong, report it and let the operator edit the rules file.
- Do not bypass the read cursor or read Capacities objects beyond the plan's
  `need_read` — that is how the 30-request window is respected.
