"""runtime_actions.py — T20 reversible runtime item actions.

Single-item verbs over today's plan: complete, done, skip_today,
remove_from_today, duration_edit, move_resize, delete_permanent, delete,
defer, assign, drop_from_plan, unassign. Every action runs through
a dated, ID-addressed journal with exact before-images, per-step applied
markers, reverse compensation on mid-apply failure, and one-step undo.

IMP-05 final source-action semantics (frozen reliability plan, action table):

- ``done`` — the final name of ``complete``: Todoist closes only the current
  occurrence (recurring advances normally); the vault gets its FileClass
  terminal status. The already-created TDTB block stays historical.
- ``drop_from_plan`` — a date-scoped runstate-only exclusion. It touches no
  Todoist/vault/calendar, removes the row from current planning only, is
  idempotent per date, and is undoable (eligible again).
- ``unassign`` — vault sets ``assigned: false``; a non-recurring Todoist task
  has its date cleared; a recurring task's due occurrence advances WITHOUT
  completing it, preserving the recurrence string/type and proving unchanged
  completion counters by read-back. Never closes, never touches deferrals.
- ``delete`` — the final name of ``delete_permanent``: every linked source
  target, atomically where feasible, plus only TDTB-owned calendar blocks
  (imported events are never in the manifest and are untouchable).

``defer`` (allocator-rewrite T1) is the one verb with no post-commit
precondition: it records a deferral in the rolling ``deferrals`` store and
best-effort un-schedules whatever derived records exist, so it is legal from
the staging queue as well as from the committed plan.

Contracts (locked decision 41 + T20 scope amendment 2026-07-25):

- Targets resolve from today's runstate ``plan_manifest`` — the app only ever
  touches artifacts it committed (Todoist rows it scheduled, owned Blocks
  calendar events, vault notes it flagged). Imported calendar entries never
  appear in the manifest and are therefore untouchable here.
- ``complete`` updates the authoritative source (Todoist close / FileClass-
  specific vault status flip, byte-preserving) — derived artifacts stay.
- ``skip_today`` / ``remove_from_today`` preserve assignment and source
  existence: they delete only today's owned Blocks event and clear the
  generated time-of-day (due date stays today).
- ``duration_edit`` / ``move_resize`` update only the intended derived
  records (owned event interval; Todoist duration field).
- ``delete_permanent`` deletes the source (Todoist delete / vault trash) and
  today's owned event — journaled with full before-images so undo can
  recreate (recreated Todoist tasks/events get NEW ids, recorded in-step).
- Surfaces are checked before ANY write: a verb whose plan needs an
  unavailable client fails closed with no journal entry and no side effect.

The journal lives beside the runstate note
(``00 - META/Cache/tdtb-runtime-journal-<date>.json``), is written atomically
(tmp + os.replace) and mutated only under ``runstate.day_lock`` — same
single-writer discipline as every other dated write.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import calendar_bridge
import commit
import deferrals
import runstate

VERBS = (
    "complete",
    "done",
    "skip_today",
    "remove_from_today",
    "duration_edit",
    "move_resize",
    "delete_permanent",
    "delete",
    "defer",
    "assign",
    "drop_from_plan",
    "unassign",
)

# T2: the verbs legal against a staging-phase (pre-commit) target. The rest
# act on derived records — an owned Blocks event, a generated time-of-day —
# that only exist after /commit, so they refuse rather than silently editing a
# source the app hasn't placed yet.
#
# IMP-05 (frozen action semantics): the four final intents are available
# before AND after commit. ``done``/``delete`` are the final names for
# complete/delete_permanent; ``drop_from_plan`` is runstate-only and
# ``unassign`` acts on the sources, none of which require a placed derived
# record.
STAGING_VERBS = (
    "complete", "done",
    "delete_permanent", "delete",
    "defer", "assign",
    "drop_from_plan", "unassign",
)

# `external_sources` gives every Todoist row this synthetic path. It is an
# identity, not a location — nothing may join it to a vault root.
TODOIST_PATH_PREFIX = "todoist://"

# FileClass → completion status value. FileClass enums are vault-defined
# (Propsec/MetadataMenu, `00 - META/Schemas/FileClasses/<type>.md`).
#
# T12d: this table was `{"capture": "processed"}` over a `"done"` default, and
# NO FileClass in the vault defines "done". Two consequences, both live on
# 2026-07-27: the value was invalid frontmatter Propsec would reject, and
# `tdtb_gather.CLOSED_STATUSES` didn't recognise it — so a completed note
# stayed OPEN and returned to the pool on the next refresh.
#
# Derivation rule, if a type is added: take the type's own enum and prefer
# "completed", else "closed", else "processed". Every value here must stay a
# member of CLOSED_STATUSES — `test_every_completion_value_reads_as_closed_to
# _the_gather` enforces exactly that, so the writer and the reader can never
# drift apart again.
DONE_VALUE_BY_FILECLASS = {
    # close to "processed" — inbox-shaped types
    "capture": "processed",
    "fleeting": "processed",
    "idea": "processed",
    "literature": "processed",
    # close to "closed" — types whose enum has no "completed"
    "press": "closed",
    "interval": "closed",
    "habit": "closed",
    "movement": "closed",
    "journal": "closed",
    "org": "closed",
    "peep": "closed",
    "place": "closed",
    "quote": "closed",
    "source": "closed",
    "term": "closed",
}
# The Global fileClass's terminal value — covers project, task, pursuit, plan,
# print, permanent, reflection, weekplan, adventure.
DEFAULT_DONE_VALUE = "completed"

TRASH_DIR_REL = ".trash"


class RuntimeActionError(Exception):
    """Refused/failed runtime action — message is the user-facing detail."""


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

def journal_rel_path(valid_date: date) -> str:
    return f"{runstate.CACHE_DIR_REL}/tdtb-runtime-journal-{valid_date}.json"


def load_journal(vault_root: Path | str, valid_date: date) -> dict[str, Any]:
    path = Path(vault_root) / journal_rel_path(valid_date)
    if not path.exists():
        return {"valid_date": str(valid_date), "actions": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"valid_date": str(valid_date), "actions": []}
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        return {"valid_date": str(valid_date), "actions": []}
    return data


def _write_journal(vault_root: Path | str, valid_date: date, journal: dict) -> None:
    path = Path(vault_root) / journal_rel_path(valid_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(journal, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    os.replace(tmp, path)


def find_action(journal: dict, action_id: str) -> dict | None:
    for action in journal.get("actions", []):
        if action.get("id") == action_id:
            return action
    return None


def idempotency_key(valid_date: date, verb: str, target: str, args: dict) -> str:
    payload = f"{valid_date}|{verb}|{target}|{json.dumps(args, sort_keys=True)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _new_action_id(valid_date: date, journal: dict) -> str:
    return f"ra-{valid_date}-{len(journal['actions']) + 1}-{secrets.token_hex(3)}"


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Target resolution (from today's committed plan_manifest)
# ---------------------------------------------------------------------------

def _resolve_from_digest_index(
    vault_root: Path | str, valid_date: date, name: str,
) -> dict[str, Any] | None:
    """T2 staging-phase fallback: resolve from the digest identity index the
    server itself wrote at /plan-inputs. Returns None when the name isn't in
    today's digest — an unknown item is still refused, exactly as post-commit.

    T12d: a digest ``path`` is only a VAULT path when it is not
    ``external_sources``' synthetic ``todoist://<id>``. Post-commit resolution
    is safe here because it keys on the manifest's ``system`` column; the
    staging index has no such column, so the pseudo-path has to be recognised
    by shape — the same guard `shadow.py` applies at its own two sites.
    Without it every staging verb against a Todoist row planned a vault write
    at ``<vault>/todoist://<id>``, which closed the task, raised ENOENT, and
    compensated the close back out.
    """
    rows = [r for r in runstate.read_digest_index(vault_root, valid_date)
            if r.get("name") == name]
    if not rows:
        return None
    out: dict[str, Any] = {"name": name, "phase": "staging"}
    for row in rows:
        path = str(row.get("path") or "")
        if row.get("todoist_id") and "todoist_id" not in out:
            out["todoist_id"] = str(row["todoist_id"])
        elif path.startswith(TODOIST_PATH_PREFIX) and "todoist_id" not in out:
            # A pseudo-path with no explicit id still identifies the task.
            out["todoist_id"] = path[len(TODOIST_PATH_PREFIX):]
        if path and not path.startswith(TODOIST_PATH_PREFIX) and "vault_path" not in out:
            out["vault_path"] = path
    return out


def resolve_target(vault_root: Path | str, valid_date: date, name: str) -> dict[str, Any]:
    """Resolve a plan-item name to its source artifacts.

    Post-commit (``plan_manifest`` present) resolution is unchanged and still
    authoritative — the app only touches artifacts it committed. When the name
    has no manifest row, T2 falls back to today's ``digest_index``, which is
    what makes complete/delete/defer usable from the staging queue before any
    commit exists. The resolved dict carries ``phase`` so the verb planner can
    refuse verbs that only make sense against placed artifacts.
    """
    state = runstate.read_runstate(vault_root, valid_date) or {}
    rows = [r for r in state.get("plan_manifest") or []
            if isinstance(r, dict) and r.get("name") == name]
    if not rows:
        staged = _resolve_from_digest_index(vault_root, valid_date, name)
        if staged is not None:
            return staged
        raise RuntimeActionError(
            f"{name!r}: not in today's plan manifest or digest")
    out: dict[str, Any] = {"name": name, "phase": "committed"}
    for row in rows:
        system = row.get("system")
        if system == "todoist" and "todoist_id" not in out:
            out["todoist_id"] = str(row.get("id_or_path"))
        elif system == "calendar" and "event_id" not in out:
            out["event_id"] = str(row.get("id_or_path"))
            out["event_time"] = row.get("time")
            out["event_duration_min"] = int(row.get("duration_min") or 0)
        elif system == "vault" and "vault_path" not in out:
            out["vault_path"] = str(row.get("id_or_path"))
    return out


# ---------------------------------------------------------------------------
# Byte-surgical vault helpers (same fence discipline as commit.py's
# _set_assigned_true — single-line edit, everything else byte-identical)
# ---------------------------------------------------------------------------

_FILECLASS_RE = re.compile(r"^\s*type:\s*\[?\s*([A-Za-z0-9_-]+)", re.MULTILINE)


def _fileclass_of(text: str) -> str | None:
    m = _FILECLASS_RE.search(text.split("\n---", 1)[0] if text.startswith("---") else "")
    return m.group(1).lower() if m else None


def _set_status(text: str, value: str) -> tuple[str, str]:
    """Set ``status: <value>`` in frontmatter, preserving every other byte."""
    if not text.startswith("---"):
        return text, "conflict"
    end = text.find("\n---", 3)
    if end == -1:
        return text, "conflict"
    fence = text[3:end]
    rest = text[end:]
    lines = fence.split("\n")
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("status:"):
            if stripped.split(":", 1)[1].strip() == value:
                return text, "noop"
            indent = line[: len(line) - len(line.lstrip())]
            lines[idx] = f"{indent}status: {value}"
            return "---" + "\n".join(lines) + rest, "updated"
    return text, "conflict"


def _set_assigned_false(text: str) -> tuple[str, str]:
    """Return ``(new_text, outcome)`` with ``assigned: false`` set.

    Mirror of ``commit._set_assigned_true`` for the Unassign direction: edits
    or inserts a single frontmatter line and never re-serializes YAML, so
    unrelated keys, comments, quoting, and the body stay byte-identical.
    ``outcome`` ∈ ``{"noop", "updated", "created"}``; ``"conflict"`` when
    there is no frontmatter fence to edit.
    """
    if not text.startswith("---"):
        return text, "conflict"
    end = text.find("\n---", 3)
    if end == -1:
        return text, "conflict"
    fence = text[3:end]
    rest = text[end:]
    lines = fence.split("\n")
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("assigned:"):
            value = stripped.split(":", 1)[1].strip().lower()
            if value == "false":
                return text, "noop"
            indent = line[: len(line) - len(line.lstrip())]
            lines[idx] = f"{indent}assigned: false"
            return "---" + "\n".join(lines) + rest, "updated"
    if lines and lines[-1] == "":
        lines.insert(len(lines) - 1, "assigned: false")
    else:
        lines.append("assigned: false")
    return "---" + "\n".join(lines) + rest, "created"


def drop_identity_of(target_or_row: dict[str, Any]) -> str:
    """Canonical source identity for a Drop exclusion.

    Todoist rows are ``todoist:<task id>``; vault rows are the vault-relative
    path. The ``todoist://`` pseudo-path is an identity marker, not a vault
    location, so it never becomes a vault identity. Falls back to the name/id
    for rows with no usable source identity (the manifest is name-keyed).
    """
    tid = target_or_row.get("todoist_id")
    if tid not in (None, ""):
        return f"todoist:{tid}"
    path = str(target_or_row.get("vault_path") or target_or_row.get("path") or "")
    if path and not path.startswith(TODOIST_PATH_PREFIX):
        return path
    return str(target_or_row.get("name") or target_or_row.get("id") or "")


# ---------------------------------------------------------------------------
# Step planning
# ---------------------------------------------------------------------------

def _hhmm_to_dt(valid_date: date, hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return datetime(valid_date.year, valid_date.month, valid_date.day, int(h), int(m))


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def _event_snapshot(store: Any, event_id: str) -> dict | None:
    ev = store.get_event(event_id)
    if ev is None:
        return None
    return {k: _iso(v) for k, v in ev.items()}


def plan_steps(verb: str, target: dict, args: dict, valid_date: date) -> list[dict]:
    """Pure: verb + resolved target -> ordered step specs (no clients)."""
    if target.get("phase") == "staging" and verb not in STAGING_VERBS:
        raise RuntimeActionError(
            f"{verb!r} needs a committed plan item — "
            f"{target.get('name')!r} is still staged")
    steps: list[dict] = []
    if verb in ("complete", "done"):
        if target.get("todoist_id"):
            steps.append({"kind": "todoist.close", "surface": "todoist",
                          "task_id": target["todoist_id"]})
        if target.get("vault_path"):
            steps.append({"kind": "vault.complete", "surface": "vault",
                          "path": target["vault_path"]})
        if not steps:
            raise RuntimeActionError(
                f"{target['name']!r}: no completable source in today's manifest")
    elif verb == "drop_from_plan":
        # IMP-05: a date-scoped TDTB exclusion. One runstate-only step — the
        # identity is the stable source identity (todoist:<id> / vault path)
        # so the same name across surfaces dedupes; the dated runstate note
        # scopes it to today, making the item eligible again tomorrow.
        steps.append({
            "kind": "runstate.drop",
            "surface": "runstate",
            "system": "runstate",
            "item": {k: target.get(k) for k in
                     ("name", "todoist_id", "vault_path", "id")},
            "date": str(valid_date),
        })
    elif verb == "unassign":
        # IMP-05: vault assigned:false + Todoist date clear (non-recurring) or
        # non-completing due advance (recurring). No deferral-memory nudge.
        task_id = target.get("todoist_id") or target.get("id")
        if target.get("vault_path"):
            steps.append({"kind": "vault.unassign", "surface": "vault",
                          "system": "vault", "path": target["vault_path"]})
        if task_id:
            steps.append({"kind": "todoist.unassign", "surface": "todoist",
                          "system": "todoist", "task_id": str(task_id),
                          "close": False})
        if not steps:
            raise RuntimeActionError(
                f"{target['name']!r}: no unassignable source")
    elif verb in ("skip_today", "remove_from_today"):
        if target.get("event_id"):
            steps.append({"kind": "calendar.delete", "surface": "calendar",
                          "event_id": target["event_id"]})
        if target.get("todoist_id"):
            steps.append({"kind": "todoist.clear_time", "surface": "todoist",
                          "task_id": target["todoist_id"]})
        if not steps:
            raise RuntimeActionError(
                f"{target['name']!r}: nothing scheduled today to remove")
    elif verb == "duration_edit":
        blocks = args.get("blocks")
        if not isinstance(blocks, int) or blocks < 1:
            raise RuntimeActionError("duration_edit requires integer blocks >= 1")
        minutes = blocks * 30
        if target.get("event_id"):
            steps.append({"kind": "calendar.resize", "surface": "calendar",
                          "event_id": target["event_id"], "minutes": minutes})
        if target.get("todoist_id"):
            steps.append({"kind": "todoist.duration", "surface": "todoist",
                          "task_id": target["todoist_id"], "minutes": minutes})
        if not steps:
            raise RuntimeActionError(
                f"{target['name']!r}: no derived records to resize")
    elif verb == "move_resize":
        start, end = args.get("start"), args.get("end")
        if not start or not end:
            raise RuntimeActionError("move_resize requires start and end (HH:MM)")
        if not target.get("event_id"):
            raise RuntimeActionError(
                f"{target['name']!r}: no owned Blocks event to move")
        steps.append({"kind": "calendar.move", "surface": "calendar",
                      "event_id": target["event_id"],
                      "start": start, "end": end})
    elif verb == "assign":
        # T9 forgot-strip: one-tap "yes, today after all". The inverse of the
        # signal that surfaced the row — a vault note gets assigned: true (the
        # same flip /commit's Step C performs), a Todoist row gets pulled to
        # today. Journaled and undoable like every other verb.
        if target.get("vault_path"):
            steps.append({"kind": "vault.assign", "surface": "vault",
                          "path": target["vault_path"]})
        if target.get("todoist_id"):
            steps.append({"kind": "todoist.assign_today", "surface": "todoist",
                          "task_id": target["todoist_id"]})
        if not steps:
            raise RuntimeActionError(
                f"{target['name']!r}: no assignable source")
    elif verb == "defer":
        # T1 defer-with-memory. The record ALWAYS happens (that is the verb);
        # un-scheduling today is best-effort on whatever derived records the
        # target actually has, so `defer` is legal pre-commit (nothing placed
        # yet) as well as post-commit — unlike remove_from_today it never
        # refuses an item with no scheduled artifacts.
        steps.append({"kind": "deferrals.record", "surface": "vault",
                      "item": {k: target.get(k) for k in
                               ("name", "vault_path", "todoist_id")}})
        if target.get("event_id"):
            steps.append({"kind": "calendar.delete", "surface": "calendar",
                          "event_id": target["event_id"]})
        if target.get("todoist_id"):
            steps.append({"kind": "todoist.clear_time", "surface": "todoist",
                          "task_id": target["todoist_id"]})
    elif verb in ("delete_permanent", "delete"):
        if target.get("event_id"):
            steps.append({"kind": "calendar.delete", "surface": "calendar",
                          "event_id": target["event_id"]})
        if target.get("todoist_id"):
            steps.append({"kind": "todoist.delete", "surface": "todoist",
                          "task_id": target["todoist_id"]})
        if target.get("vault_path"):
            steps.append({"kind": "vault.trash", "surface": "vault",
                          "path": target["vault_path"]})
        if not steps:
            raise RuntimeActionError(
                f"{target['name']!r}: nothing to delete in today's manifest")
    else:
        raise RuntimeActionError(f"unknown verb {verb!r}")
    return steps


# ---------------------------------------------------------------------------
# Step execution — apply / reverse (compensation and undo share reverse ops)
# ---------------------------------------------------------------------------

def _apply_step(step: dict, vault_root: Path, valid_date: date,
                todoist: Any, store: Any) -> None:
    kind = step["kind"]
    if kind == "todoist.close":
        step["before"] = {"task": todoist.get_task(step["task_id"])}
        todoist.close_task(step["task_id"])
    elif kind == "todoist.clear_time":
        task = todoist.get_task(step["task_id"])
        step["before"] = {"task": task}
        # T27 guard: a recurring due is pattern-owned — due_string in any form
        # (incl. "today") wipes the recurrence. The all-day/no-time intent on a
        # recurring task skips the due write entirely; the event removal above
        # already un-schedules today.
        if (task.get("due") or {}).get("is_recurring"):
            step["skipped_recurring"] = True
        else:
            todoist.reschedule_task(step["task_id"], "today")
    elif kind == "todoist.duration":
        step["before"] = {"task": todoist.get_task(step["task_id"])}
        todoist.update_task(step["task_id"], duration=step["minutes"],
                            duration_unit="minute")
    elif kind == "todoist.delete":
        step["before"] = {"task": todoist.get_task(step["task_id"])}
        todoist.delete_task(step["task_id"])
    elif kind == "calendar.delete":
        snap = _event_snapshot(store, step["event_id"])
        if snap is None:
            raise RuntimeActionError(
                f"owned event {step['event_id']!r} not found live")
        step["before"] = {"event": snap}
        store.delete_event(step["event_id"])
    elif kind in ("calendar.resize", "calendar.move"):
        snap = _event_snapshot(store, step["event_id"])
        if snap is None:
            raise RuntimeActionError(
                f"owned event {step['event_id']!r} not found live")
        step["before"] = {"event": snap}
        if kind == "calendar.resize":
            start_dt = datetime.fromisoformat(snap["start"])
            end_dt = start_dt + _minutes(step["minutes"])
            store.update_event(step["event_id"], end=end_dt)
        else:
            store.update_event(
                step["event_id"],
                start=_hhmm_to_dt(valid_date, step["start"]),
                end=_hhmm_to_dt(valid_date, step["end"]),
            )
    elif kind == "vault.complete":
        path = vault_root / step["path"]
        text = path.read_text(encoding="utf-8")
        step["before"] = {"text": text}
        done_value = DONE_VALUE_BY_FILECLASS.get(
            _fileclass_of(text) or "", DEFAULT_DONE_VALUE)
        new_text, outcome = _set_status(text, done_value)
        if outcome == "conflict":
            raise RuntimeActionError(
                f"{step['path']!r}: no editable status frontmatter line")
        step["done_value"] = done_value
        _atomic_write(path, new_text)
    elif kind == "vault.assign":
        path = vault_root / step["path"]
        text = path.read_text(encoding="utf-8")
        step["before"] = {"text": text}
        # Reuses /commit's Step C writer verbatim rather than a second
        # frontmatter editor — one byte-surgical implementation, one set of
        # edge cases (missing key, existing key, no fence).
        new_text, outcome = commit._set_assigned_true(text)
        if outcome == "conflict":
            raise RuntimeActionError(
                f"{step['path']!r}: no frontmatter fence to assign in")
        step["outcome"] = outcome
        _atomic_write(path, new_text)
    elif kind == "vault.unassign":
        # IMP-05 Unassign: set assigned: false, byte-preserving outside the
        # single frontmatter line (mirror of commit._set_assigned_true).
        path = vault_root / step["path"]
        text = path.read_text(encoding="utf-8")
        step["before"] = {"text": text}
        new_text, outcome = _set_assigned_false(text)
        if outcome == "conflict":
            raise RuntimeActionError(
                f"{step['path']!r}: no frontmatter fence to unassign in")
        step["outcome"] = outcome
        _atomic_write(path, new_text)
    elif kind == "todoist.assign_today":
        task = todoist.get_task(step["task_id"])
        step["before"] = {"task": task}
        # T27 guard, same as clear_time: a recurring due is pattern-owned and
        # any due_string write wipes the recurrence. A recurring task already
        # recurs onto today when it's due — there is nothing to assign.
        if (task.get("due") or {}).get("is_recurring"):
            step["skipped_recurring"] = True
        else:
            todoist.reschedule_task(step["task_id"], "today")
    elif kind == "todoist.unassign":
        # IMP-05 Unassign: non-recurring clears the date; recurring advances
        # the due occurrence WITHOUT completing (non-completing due update
        # that preserves the recurrence string/type), then proves unchanged
        # completion counters by read-back. An unsupported pattern fails
        # closed before any write; a completion change after the write is
        # compensated back by the engine.
        task = todoist.get_task(step["task_id"])
        step["before"] = {"task": task}
        due = task.get("due") or {}
        if due.get("is_recurring"):
            advance = _next_recurring_due(task)  # raises -> fail closed
            if due.get("datetime"):
                todoist.reschedule_task_datetime(step["task_id"], advance)
            else:
                todoist.reschedule_task_date(step["task_id"], advance)
            step["advance"] = advance
        else:
            todoist.clear_task_date(step["task_id"])
        after = todoist.get_task(step["task_id"])
        step["after"] = {"task": after}
        proof = _completion_proof(task, after)
        step["readback"] = proof
        if proof["completed"] or proof["changed"]:
            step["applied"] = True  # due write happened — mark so it reverses
            raise RuntimeActionError(
                "unassign would change completion history — fail closed; "
                "defer the recurring task in Todoist instead")
    elif kind == "runstate.drop":
        # IMP-05 Drop from plan: date-scoped runstate exclusion. Identity is
        # stable so a repeat is a no-op; the dated note scopes it to today.
        # The caller (apply_action / undo_action) already holds
        # ``runstate.day_lock``, so the RMW must NOT go through
        # ``runstate.update_runstate`` (non-reentrant lock -> deadlock).
        item = step.get("item") or {}
        identity = drop_identity_of(item)
        step["identity"] = identity
        step["before"] = {"dropped": runstate.read_dropped(vault_root, valid_date)}

        def _append_drop(state: dict[str, Any]) -> None:
            dropped = [d for d in (state.get("dropped") or [])
                       if d.get("identity") != identity]
            dropped.append({"identity": identity,
                            "name": item.get("name") or "",
                            "dropped_at": _now_iso()})
            state["dropped"] = dropped

        _locked_runstate_rmw(vault_root, valid_date, _append_drop)
    elif kind == "deferrals.record":
        item = step["item"]
        key = deferrals.key_for_item(item)
        step["key"] = key
        step["before"] = {"entry": deferrals.load_deferrals(vault_root).get(key)}
        step["entry"] = deferrals.record_deferral(vault_root, item, valid_date)
    elif kind == "vault.trash":
        path = vault_root / step["path"]
        text = path.read_text(encoding="utf-8")
        step["before"] = {"text": text, "path": step["path"]}
        trash_dir = vault_root / TRASH_DIR_REL
        trash_dir.mkdir(parents=True, exist_ok=True)
        trash_path = trash_dir / Path(step["path"]).name
        # never clobber an earlier trash entry
        n = 1
        while trash_path.exists():
            trash_path = trash_dir / f"{Path(step['path']).stem}-{n}{Path(step['path']).suffix}"
            n += 1
        os.replace(path, trash_path)
        step["trash_path"] = str(trash_path.relative_to(vault_root))
    else:  # pragma: no cover — plan_steps only emits the kinds above
        raise RuntimeActionError(f"unknown step kind {kind!r}")
    step["applied"] = True


def _reverse_step(step: dict, vault_root: Path, valid_date: date,
                  todoist: Any, store: Any) -> None:
    """Exact reverse of one applied step, from its before-image."""
    kind = step["kind"]
    before = step.get("before") or {}
    if kind == "todoist.close":
        todoist.reopen_task(step["task_id"])
    elif kind == "todoist.clear_time":
        if step.get("skipped_recurring"):
            return  # nothing was written — nothing to reverse
        due = (before.get("task") or {}).get("due") or {}
        if due.get("datetime"):
            todoist.reschedule_task_datetime(step["task_id"], due["datetime"])
        elif due.get("string"):
            todoist.reschedule_task(step["task_id"], due["string"])
    elif kind == "todoist.duration":
        prior = (before.get("task") or {}).get("duration")
        prior_minutes = prior.get("amount") if isinstance(prior, dict) else prior
        todoist.update_task(step["task_id"],
                            duration=prior_minutes, duration_unit="minute")
    elif kind == "todoist.delete":
        task = before.get("task") or {}
        created = todoist.create_task(
            task.get("content") or step["task_id"],
            project_id=task.get("project_id"),
            **({"description": task["description"]} if task.get("description") else {}),
        )
        step["recreated_task_id"] = created.get("id")
    elif kind == "calendar.delete":
        ev = before.get("event") or {}
        new_id = store.create_event(calendar_bridge.EventSpec(
            title=ev.get("title") or step["event_id"],
            start=datetime.fromisoformat(ev["start"]),
            end=datetime.fromisoformat(ev["end"]),
            calendar_id=ev.get("calendar_id") or "",
            notes=ev.get("notes"),
        ))
        step["recreated_event_id"] = new_id
    elif kind in ("calendar.resize", "calendar.move"):
        ev = before.get("event") or {}
        store.update_event(step["event_id"],
                           start=datetime.fromisoformat(ev["start"]),
                           end=datetime.fromisoformat(ev["end"]))
    elif kind == "vault.complete":
        _atomic_write(vault_root / step["path"], before["text"])
    elif kind == "vault.assign":
        _atomic_write(vault_root / step["path"], before["text"])
    elif kind == "vault.unassign":
        _atomic_write(vault_root / step["path"], before["text"])
    elif kind == "todoist.assign_today":
        if step.get("skipped_recurring"):
            return  # nothing was written — nothing to reverse
        due = (before.get("task") or {}).get("due") or {}
        if due.get("datetime"):
            todoist.reschedule_task_datetime(step["task_id"], due["datetime"])
        elif due.get("string"):
            todoist.reschedule_task(step["task_id"], due["string"])
    elif kind == "todoist.unassign":
        due = (before.get("task") or {}).get("due") or {}
        if due.get("datetime"):
            todoist.reschedule_task_datetime(step["task_id"], due["datetime"])
        elif due.get("date"):
            todoist.reschedule_task_date(step["task_id"], due["date"])
        elif due.get("string"):
            todoist.reschedule_task(step["task_id"], due["string"])
        # no due at all — the clear was reversed by not writing anything
    elif kind == "runstate.drop":
        _locked_runstate_rmw(vault_root, valid_date, lambda state: state.update(
            {"dropped": (before.get("dropped") or [])}))
    elif kind == "deferrals.record":
        deferrals.set_entry(vault_root, step["key"], before.get("entry"))
    elif kind == "vault.trash":
        original = vault_root / before["path"]
        original.parent.mkdir(parents=True, exist_ok=True)
        trash_path = vault_root / step["trash_path"]
        if trash_path.exists():
            os.replace(trash_path, original)
        else:  # trash entry vanished — restore from exact before-image bytes
            _atomic_write(original, before["text"])
    else:  # pragma: no cover
        raise RuntimeActionError(f"unknown step kind {kind!r}")


def _minutes(n: int):
    return timedelta(minutes=n)


# ---------------------------------------------------------------------------
# Recurring Unassign — non-completing due advance (frozen action table)
# ---------------------------------------------------------------------------
#
# Todoist has no supported "postpone recurring occurrence" endpoint; close is
# a completion operation. The locked contract therefore advances the due
# occurrence through a non-completing due update that preserves the recurrence
# string/type, and proves unchanged completion counters by read-back. When the
# pattern cannot be advanced deterministically, the apply fails closed BEFORE
# any write — never close, never clear the date, never masquerade as Done.

def _freq_interval(freq: str, interval: int) -> tuple[int, int, int]:
    """(days, months, years) advance for an RRULE/plain frequency."""
    if freq == "DAILY":
        return (interval, 0, 0)
    if freq == "WEEKLY":
        return (interval * 7, 0, 0)
    if freq == "MONTHLY":
        return (0, interval, 0)
    if freq == "YEARLY":
        return (0, 0, interval)
    raise RuntimeActionError(
        f"unsupported recurrence frequency {freq!r} — fail closed; "
        "defer the recurring task in Todoist instead")


def _recurrence_interval(due: dict[str, Any]) -> tuple[int, int, int]:
    """The per-occurrence advance for a due dict, from its rrule or string.

    Supports RRULE (``FREQ=DAILY|WEEKLY|MONTHLY|YEARLY`` with optional
    ``INTERVAL=N``) and the plain "every N day|week|month|year" forms.
    Anything else raises ``RuntimeActionError`` (fail closed).
    """
    rrule = str(due.get("rrule") or "").upper()
    freq_m = re.search(r"FREQ=(\w+)", rrule)
    if freq_m:
        interval = 1
        int_m = re.search(r"INTERVAL=(\d+)", rrule)
        if int_m:
            interval = max(1, int(int_m.group(1)))
        return _freq_interval(freq_m.group(1), interval)
    text = str(due.get("string") or "").lower()
    m = re.search(r"every\s+(\d+)?\s*(day|week|month|year)s?", text)
    if not m:
        raise RuntimeActionError(
            f"unsupported recurring pattern "
            f"{due.get('string') or due.get('rrule')!r} — fail closed; "
            "defer the recurring task in Todoist instead")
    interval = int(m.group(1) or 1)
    unit = {"day": "DAILY", "week": "WEEKLY",
            "month": "MONTHLY", "year": "YEARLY"}[m.group(2)]
    return _freq_interval(unit, interval)


def _add_months(dt: datetime, months: int) -> datetime:
    """Calendar-safe month arithmetic (clamp the day to the target month)."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _next_recurring_due(task: dict[str, Any]) -> str:
    """The next occurrence's due value (datetime or date ISO, matching the
    task's due shape) — or ``RuntimeActionError`` when not determinable.

    "every weekday" advances to the next weekday; all other supported
    patterns advance by their interval. The due shape is preserved:
    a datetime due advances as a datetime, a date-only due as a date.
    """
    due = task.get("due") or {}
    is_dt = bool(due.get("datetime"))
    raw = due.get("datetime") or due.get("date")
    if not raw:
        raise RuntimeActionError(
            "recurring task has no parseable due to advance — fail closed; "
            "defer the recurring task in Todoist instead")
    try:
        if is_dt:
            anchor = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if anchor.tzinfo is not None:
                anchor = anchor.replace(tzinfo=None)
        else:
            anchor = datetime.strptime(str(raw), "%Y-%m-%d")
    except ValueError as exc:
        raise RuntimeActionError(
            f"recurring due {raw!r} is not parseable — fail closed; "
            "defer the recurring task in Todoist instead") from exc
    text = str(due.get("string") or "").lower()
    if "weekday" in text:
        nxt = anchor + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
    else:
        days, months, years = _recurrence_interval(due)
        nxt = anchor
        if months or years:
            nxt = _add_months(nxt, months + years * 12)
        nxt = nxt + timedelta(days=days)
    if is_dt:
        return nxt.strftime("%Y-%m-%dT%H:%M:%S")
    return nxt.strftime("%Y-%m-%d")


def _completion_proof(before_task: dict[str, Any],
                      after_task: dict[str, Any]) -> dict[str, Any]:
    """Read-back evidence that Unassign never completed the task.

    Compares the completion/checked fields across the advance and verifies
    the recurrence string/type survived. Any completion change means the
    server misbehaved — the caller fails closed and compensates.
    """
    fields = ("is_completed", "completed_at", "completed_count", "checked")
    changed: dict[str, Any] = {}
    for field in fields:
        b, a = before_task.get(field), after_task.get(field)
        if b != a:
            changed[field] = {"before": b, "after": a}
    before_due = before_task.get("due") or {}
    after_due = after_task.get("due") or {}
    recurrence_preserved = (
        (before_due.get("string") or before_due.get("rrule"))
        == (after_due.get("string") or after_due.get("rrule"))
        and before_due.get("is_recurring") == after_due.get("is_recurring")
    )
    return {
        "completed": bool(after_task.get("is_completed"))
        or bool(after_task.get("completed_at")),
        "changed": changed,
        "recurrence_preserved": recurrence_preserved,
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _locked_runstate_rmw(vault_root: Path, valid_date: date,
                         mutator: Any) -> None:
    """Run-state RMW for callers that ALREADY hold ``runstate.day_lock``.

    ``runstate.update_runstate`` takes the per-day lock itself, and
    ``threading.Lock`` is not reentrant — calling it from inside
    ``apply_action``/``undo_action`` (which hold the same lock) deadlocks.
    This performs the same read-modify-write under the caller-held lock.
    """
    state = runstate.build_runstate(
        runstate.read_runstate(vault_root, valid_date) or None)
    mutator(state)
    runstate.write_runstate(vault_root, valid_date, state)


_SURFACE_CLIENT = {"todoist": "todoist", "calendar": "store",
                   "vault": None, "runstate": None}


def _check_surfaces(steps: list[dict], todoist: Any, store: Any) -> None:
    missing: list[str] = []
    for step in steps:
        surface = step["surface"]
        if surface == "todoist" and todoist is None:
            missing.append("todoist")
        elif surface == "calendar" and store is None:
            missing.append("calendar")
    if missing:
        raise RuntimeActionError(
            "surface unavailable: " + ", ".join(sorted(set(missing))))


# ---------------------------------------------------------------------------
# Public engine
# ---------------------------------------------------------------------------

def apply_action(
    vault_root: Path | str,
    valid_date: date,
    verb: str,
    target_name: str,
    args: dict | None = None,
    *,
    todoist: Any = None,
    store: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply one runtime verb to one plan item; returns the journal entry.

    Journal-write-ahead: the entry (with planned steps) is persisted BEFORE
    the first write, updated after apply/compensation, so a crash mid-action
    always leaves an inspectable record.
    """
    vault_root = Path(vault_root)
    args = args or {}
    if verb not in VERBS:
        raise RuntimeActionError(f"unknown verb {verb!r}")
    target = resolve_target(vault_root, valid_date, target_name)
    steps = plan_steps(verb, target, args, valid_date)
    _check_surfaces(steps, todoist, store)

    with runstate.day_lock(valid_date):
        journal = load_journal(vault_root, valid_date)
        key = idempotency_key(valid_date, verb, target_name, args)
        for existing in journal["actions"]:
            if (existing.get("idempotency_key") == key
                    and existing.get("status") in ("applied", "partial")):
                dup = dict(existing)
                dup["duplicate"] = True
                return dup

        action: dict[str, Any] = {
            "id": _new_action_id(valid_date, journal),
            "date": str(valid_date),
            "verb": verb,
            "target": target,
            "args": args,
            "idempotency_key": key,
            "status": "pending",
            "steps": steps,
            "created_at": _now_iso(now),
            "finished_at": None,
            "undone_at": None,
            "error": None,
        }
        journal["actions"].append(action)
        _write_journal(vault_root, valid_date, journal)

        try:
            for step in steps:
                _apply_step(step, vault_root, valid_date, todoist, store)
                _write_journal(vault_root, valid_date, journal)
            action["status"] = "applied"
        except Exception as exc:  # noqa: BLE001 — every failure is journaled
            action["error"] = str(exc)
            applied = [s for s in steps if s.get("applied")]
            comp_failed = False
            for step in reversed(applied):
                try:
                    _reverse_step(step, vault_root, valid_date, todoist, store)
                    step["compensated"] = True
                except Exception as comp_exc:  # noqa: BLE001
                    step["compensation_error"] = str(comp_exc)
                    comp_failed = True
            if not applied:
                action["status"] = "failed"
            elif comp_failed:
                action["status"] = "partial"
            else:
                action["status"] = "compensated"
        action["finished_at"] = _now_iso(now)
        _write_journal(vault_root, valid_date, journal)
        return action


def undo_action(
    vault_root: Path | str,
    valid_date: date,
    action_id: str,
    *,
    todoist: Any = None,
    store: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reverse one previously applied action from its exact before-images."""
    vault_root = Path(vault_root)
    with runstate.day_lock(valid_date):
        journal = load_journal(vault_root, valid_date)
        action = find_action(journal, action_id)
        if action is None:
            raise RuntimeActionError(f"unknown action {action_id!r}")
        if action.get("status") != "applied":
            raise RuntimeActionError(
                f"action {action_id!r} is not undoable "
                f"(status {action.get('status')!r})")
        steps = [s for s in action["steps"] if s.get("applied")]
        _check_surfaces(steps, todoist, store)
        undo_failed = False
        for step in reversed(steps):
            try:
                _reverse_step(step, vault_root, valid_date, todoist, store)
            except Exception as exc:  # noqa: BLE001
                step["undo_error"] = str(exc)
                undo_failed = True
        action["status"] = "undo_failed" if undo_failed else "undone"
        action["undone_at"] = _now_iso(now)
        _write_journal(vault_root, valid_date, journal)
        if undo_failed:
            raise RuntimeActionError(
                f"undo of {action_id!r} failed on one or more steps — "
                "journal holds per-step errors")
        return action
