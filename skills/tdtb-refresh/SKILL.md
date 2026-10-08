---
name: tdtb-refresh
description: >-
  Regenerate the TDTB planning artifact (state/planning-artifact.json) from
  Todoist over MCP. The skill fetches tasks, hands them to the deterministic
  producer CLI, validates the result, and writes it atomically. Trigger on
  "refresh tdtb", "tdtb refresh", "regenerate the planning artifact", "rebuild
  the planning artifact", "pull my todoist into tdtb", "update today's plan
  sources", "refresh today's tdtb plan", or when the cockpit says the planning
  artifact is missing, stale, or empty. Do NOT use for editing the rule set
  by hand, for placement or commit (that is the app's job), or for Capacities
  sources — Capacities evaluation lands in a later slice.
---

# TDTB planning-artifact refresh

You are the **producer** half of the artifact-contract pivot. The app no
longer reads Todoist live in artifact mode; it consumes ONE normalized file
that this skill regenerates. You do the part only an agent can do — speak
Todoist over MCP. **You never decide what is admitted**: a deterministic CLI
(`tools/produce_rows.py`) applies the operator's rule file and emits the
artifact. Do not re-rank, filter, rename, or "improve" rows yourself.

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
- The write is atomic and retains one prior copy; never write the artifact
  with a bare file write.

## Procedure

1. **Confirm the rules file exists.** If
   `~/.config/tdtb/producer-rules.json` is absent, copy
   `skills/tdtb-refresh/producer-rules.json` to it and tell the operator you
   installed the starter. Do not edit the rule set unless asked.

2. **Fetch Todoist over MCP.** Resolve the operator's queries with these
   tools, then collect the raw task objects:
   - `todoist_find-filters` — the operator's saved filters (resolve the
     assigned and pool queries by name; do not invent queries).
   - `todoist_find-projects` — project list, so project-scoped rules resolve.
   - `todoist_find-tasks` — the open tasks that match the assigned/pool
     queries.
   - `todoist_find-tasks-by-date` — tasks due the logical day (and any date
     the operator names).
   - `todoist_find-completed-tasks` — completion state, so already-done ids
     are excluded before you assemble the source JSON.
   Merge the open results, dedupe by task id, and drop completed ids. The
   rules file decides pooling and assignment — you only supply the candidate
   tasks.

3. **Assemble the source JSON** exactly in the CLI's shape and pipe it on
   stdin:

   ```json
   {
     "todoist": {
       "status": "ok",
       "read_at": "<ISO-8601 with offset>",
       "warnings": [],
       "tasks": [ "<raw Todoist task objects>" ]
     }
   }
   ```

   Use `"status": "partial"` with an explanation in `warnings` if a fetch
   degraded, and `"failed"` if Todoist could not be read at all.

4. **Evaluate deterministically and write the artifact.** Run the CLI from the
   TDTB repo root, passing the run's logical day explicitly so the artifact
   passes the app's O2 staleness check:

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

5. **Validate before trusting it.** Always re-run the validator on what was
   written, and report its result:

   ```bash
   "$PY" "$TDTB_REPO/tools/validate_artifact.py" \
     ~/.config/tdtb/state/planning-artifact.json
   ```

6. **Report to the operator.** Read the artifact's `admission` block and state,
   per rule id: how many records matched, were admitted, and were dropped
   (`admission.per_rule`). Then list the most recent drops with the rule that
   dropped them (`admission.recent_drops` — `rule: null` means no rule
   matched). Name the artifact path and its `logical_day`. If any
   `sources.<name>.status` is `partial` or `failed`, surface its warnings
   verbatim.

## Rule schema (what the operator edits)

`producer-rules.json` is `{"version": 1, "rules": [ ... ]}`. Each rule is:

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

- `when` is recursive: a combinator `{"all": [...]}`, `{"any": [...]}`,
  `{"not": {...}}`, or a leaf `{"prop", "op", "values"}`.
- Ops: `eq`, `in`, `exists`, `truthy`, `lt`, `gt`, `before`, `after`,
  `matches`. The value token `"$today"` resolves to the run's logical day.
- `prop` is a dotted path into the raw source record (e.g. `due.date`,
  `labels`, `priority`, `project_id`, `content`, `duration.amount`).
- **First-match-wins**: the first rule whose `source` matches and whose `when`
  holds decides the record, so exclusion rules must come before admission
  rules. A record no rule matches is dropped.
- `admit: false` excludes a matching record and records the drop against that
  rule id.
- `pool: true` emits the row with `assigned: false` (O1), regardless of
  `assigned`.
- Capacities rules (`"source": "capacities"` with `structure` and
  `duration_prop`) are already valid schema but are **ignored in this slice**;
  Capacities evaluation lands later. Leaving them in the file is harmless.

## Do not

- Do not hand-edit `planning-artifact.json` after producing it — use the
  hand-edit overlay (`state/planning-overlay.json`) for durable edits.
- Do not change `sources.mode` in `config.json`; that is the operator's opt-in.
- Do not add rows the rules dropped, or rename rows to dodge a duplicate. If a
  rule set looks wrong, report it and let the operator edit the rules file.
