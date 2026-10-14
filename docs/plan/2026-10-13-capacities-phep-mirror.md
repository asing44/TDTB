# 2026-10-13 — Capacities rows mirror into the PHEP Todoist project

User-approved contract change. Every Capacities assigned row that is written
to the calendar at live commit ALSO creates one Todoist task under the **PHEP**
project, so the work is actionable in Todoist without becoming a second plan
item. This supersedes the S6b / U4 S8 statement that "Capacities rows are
calendar-only".

## What changed

Before: a Capacities assigned row produced exactly one calendar `create-event`
and no Todoist write.

After: the same row produces

1. the unchanged calendar `create-event` (title + start dedup, idempotent), and
2. one Todoist `create` under PHEP with
   - `content` = the row name,
   - `due_datetime` = the calendar block start (local wall time, naive
     `YYYY-MM-DDTHH:MM:SS`), with `duration`/`duration_unit=minute` = the block
     length,
   - `description` = the row's canonical `capacities://<space>/<object>` link.

A Capacities row that gets NO calendar block (e.g. a zero-block assigned row,
or one not present in the accepted sequence) gets NO mirror task. The mirror is
built only from the server-side manifest; a client-supplied type/project can
never reroute it off PHEP.

## Receipts and exclusion store

New machine-local store module: `app/capacities_mirror.py`.

- Path: `<app home>/state/capacities-mirror.json` (lock:
  `capacities-mirror.lock`). App home is `$TDTB_HOME` when set, else
  `~/.config/tdtb`. This is machine-local state, never `config/tdtb.json` and
  never the repository.
- Receipts are keyed by `(logical day, Capacities identity)` —
  `"<YYYY-MM-DD>::<identity>"` — with status `pending` / `done` /
  `needs_review`. The write-ahead `pending` record is persisted BEFORE the
  provider call, so:
  - a resume/re-commit of the same day never duplicates the task;
  - a changed row title cannot duplicate it (the key is the identity, not the
    title);
  - an ambiguous or errored attempt is recorded `needs_review` and is never
    blindly retried as success;
  - a landed `todoist_id` is persisted.
- The created Todoist ids are also recorded in `excluded_task_ids`. Todoist
  intake reads them and drops matching tasks from the assigned and pool
  surfaces, so a mirror never re-appears as a Todoist-sourced row on a later
  load (including later days while it is still open) and never triggers the
  `(Capacities)` name-collision rename of its own Capacities row.
- Reads are fail-closed: malformed/unreadable bytes raise a typed error and
  are never repaired, overwritten, or truncated. The commit lane then refuses
  to create (no duplicate); intake degrades with a visible warning and never
  crashes plan-inputs.

Writes follow the existing `capacities_cache_io` convention: a cross-process
process lock + POSIX advisory lock, and an atomic same-directory write.

## Commit behavior

- `app/shadow.py`: `build_plan_manifest` appends a step-A `todoist`
  `schedule` entry (`routing="PHEP"`, `capacities_mirror=True`) beside the
  Capacities calendar entry. The mirror lane is **create-only**: a live task
  with the same content — at any time or project — is a `no-op`, and is never
  retimed, claimed, or receipted. A Capacities row whose block has a missing
  or zero-length start/end keeps its unchanged calendar row but gets no mirror;
  the skip is surfaced as a note on the diff report (`notes`).
- `app/commit.py`: `plan_writes`/`_plan_todoist` resolve the PHEP project id
  from `_config_phep_project_id(config)` and carry the block-start
  `due_datetime`; `write_todoist` consults the mirror receipt store
  (begin → create → complete/fail). A failed/ambiguous mirror create is
  reported on the Todoist surface and never touches or rolls back the calendar
  write.
- `app/todoist_client.py`: `create_task` gained `due_datetime` and
  `description` (both were missing).
- `app/external_sources.py`: `fetch_todoist_items(client, config,
  exclude_ids=None)` drops excluded ids from assigned and pool.
- `app/main.py`: `get_plan_inputs` reads the exclusion list, passes it to the
  live fetch, and also filters artifact-sourced Todoist rows by `todoist_id`,
  so the digest (and therefore the persisted digest index, commit eligibility,
  sequencing and prompts) never sees a mirror. `_source_resolved_fallback`
  (the duration-memory reset reader) reads the same exclusions.

## Today's already-committed rows

`Cleanups` and `Garage Buildout` were committed earlier today: their calendar
events exist, but no mirror task exists yet. On the next commit run they will
get their PHEP tasks, because creation is keyed by the **receipt**, not by
calendar presence:

- the calendar entry already exists → the calendar writer collapses it to a
  `no-op` (title + start dedup, unchanged and idempotent);
- the mirror receipt does not exist for today → the Todoist writer creates the
  task once and records the receipt.

Re-running the same commit is idempotent on both surfaces: the calendar entry
stays a `no-op`, and the mirror receipt is `done` so no second task is created.

## Known limitations (accepted, not fixed here)

- **Created-but-unrecorded orphan id.** If the process dies after the provider
  create but before `complete_mirror` persists the id, the pending receipt
  blocks a duplicate, but the landed task's id is not in `excluded_task_ids`.
  A later intake can re-surface that task as a Todoist-sourced row and may
  trigger the `(Capacities)` name-collision rename.
- **Sticky `needs_review`.** A failed mirror receipt is `needs_review` with no
  clearing path other than deleting the machine-local store; the row stays
  uncreatable until an operator intervenes.
- **Pre-02:00 logical-day due is a day early.** A request whose logical day is
  the next civil date (before 02:00) lands the mirror due on the prior day;
  this is shared with the existing calendar path.
- **Receipt identity falls back to the title.** When a Capacities row carries
  no identity, the receipt key falls back to the row title, so a renamed row
  without an identity can re-create.

## Not verified

- No live Todoist or Calendar call was made; all behaviour is proven with
  in-memory fakes.
- The provider-side acceptance of `due_datetime`, `duration`, and `description`
  on create is NOT verified against the live API.
- Completing a mirror task in Todoist does NOT sync back to Capacities
  (completion sync is unchanged).

## Live activation

1. Restart the live `:8746` service (attended deployment action; never during a
   commit) so the backend serves the new write path, then GET-only proof of the
   expected contract.
2. Run the next commit. `Cleanups` and `Garage Buildout` (and any other
   Capacities row on the calendar that run) get their PHEP tasks; the calendar
   events are untouched.

No cutover or restart has been performed by this change.
