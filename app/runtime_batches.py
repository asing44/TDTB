"""runtime_batches.py — B3 batch apply + retry over runtime_actions.

A batch is a group of single-item runtime actions (the verbs ``apply_action``
already owns) executed sequentially, each under its own ``runstate.day_lock``.
The batch assigns one ``batch_id``, tags every journal action it creates with
it, and records a journal-level ``batches[]`` entry so a later retry can
address the unit without disturbing items that already succeeded.

Contracts:

- ``apply_batch`` never holds the day lock across items — ``apply_action``
  takes it per item, and ``threading.Lock`` is not reentrant. The batch entry
  is written ahead of each item and re-saved after it, so a crash mid-batch
  still leaves an inspectable record.
- A failure or refusal is reported per item and NEVER stops a later item;
  items that applied stay applied (no batch-level rollback — that is the
  later undo slice).
- ``retry_batch`` re-runs only items whose latest status is not
  ``applied``/``duplicate``; already-applied items are reported as-is and are
  not re-invoked.
- Old journals without ``batches[]`` and actions without ``batch_id`` keep
  working unchanged.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime
from typing import Any

import runstate
import runtime_actions as ra

# Per-item result status vocabulary (the batch's own, distinct from the
# action journal's applied/failed/compensated).
STATUS_APPLIED = "applied"
STATUS_FAILED = "failed"
STATUS_DUPLICATE = "duplicate"
STATUS_REFUSED = "refused"

# Items in either of these states need no retry.
_SETTLED = (STATUS_APPLIED, STATUS_DUPLICATE)


def _new_batch_id(valid_date: date) -> str:
    return f"rb-{valid_date}-{secrets.token_hex(4)}"


def _find_batch(journal: dict, batch_id: str) -> dict | None:
    for batch in journal.get("batches") or []:
        if isinstance(batch, dict) and batch.get("id") == batch_id:
            return batch
    return None


def _save_batch(valid_date: date, batch: dict) -> None:
    """Insert or replace one batch entry in the dated journal.

    Reuses ``runtime_actions``' atomic writer (the same file and single-writer
    discipline) rather than duplicating it. The day lock is taken here only,
    never held across an ``apply_action`` call, so the two cannot deadlock.
    """
    with runstate.day_lock(valid_date):
        journal = ra.load_journal(valid_date)
        batches = journal.setdefault("batches", [])
        for pos, existing in enumerate(batches):
            if isinstance(existing, dict) and existing.get("id") == batch["id"]:
                batches[pos] = batch
                break
        else:
            batches.append(batch)
        ra._write_journal(valid_date, journal)


def _spec(item: Any) -> tuple[Any, Any, dict]:
    """Normalize one batch item to ``(verb, target, args)``."""
    if not isinstance(item, dict):
        return None, None, {}
    return item.get("verb"), item.get("target"), item.get("args") or {}


def _item_status(action: dict) -> str:
    if action.get("duplicate"):
        return STATUS_DUPLICATE
    if action.get("status") == STATUS_APPLIED:
        return STATUS_APPLIED
    return STATUS_FAILED


def _execute_item(
    valid_date: date,
    verb: Any,
    target: Any,
    args: dict,
    batch_id: str,
    todoist: Any,
    store: Any,
    capacities: Any,
    now: datetime | None,
) -> tuple[str, Any, Any, bool]:
    """Run one item via ``apply_action`` and classify the outcome.

    Returns ``(status, error, action_id, mine)`` where ``mine`` is True only
    when the returned action was created under this ``batch_id`` (a duplicate
    may return an action owned by an earlier batch, which must not be claimed
    as this batch's ``item_action_ids``). A refusal is a ``RuntimeActionError``
    raised before anything was journaled; any other escape is a failure.
    """
    if verb is None:
        return STATUS_REFUSED, "batch item requires a verb", None, False
    try:
        action = ra.apply_action(
            valid_date, verb, target, args,
            todoist=todoist, store=store, capacities=capacities,
            now=now, batch_id=batch_id,
        )
    except ra.RuntimeActionError as exc:
        return STATUS_REFUSED, str(exc), None, False
    except Exception as exc:  # noqa: BLE001 — reported, never fatal to the batch
        return STATUS_FAILED, str(exc), None, False
    return (
        _item_status(action),
        action.get("error"),
        action.get("id"),
        action.get("batch_id") == batch_id,
    )


def _claim_action(batch: dict, action_id: Any, mine: bool) -> None:
    if mine and action_id and action_id not in batch["item_action_ids"]:
        batch["item_action_ids"].append(action_id)


def _result(index: Any, verb: Any, target: Any, status: str, error: Any) -> dict:
    return {"index": index, "verb": verb, "target": target,
            "status": status, "error": error}


def apply_batch(
    valid_date: date,
    items: list[dict] | None = None,
    *,
    todoist: Any = None,
    store: Any = None,
    capacities: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply ``items`` (``[{verb, target, args}]``) as one journaled batch.

    Calls the existing ``apply_action`` for each item in order; each runs
    under its own day lock. Successes are kept even when other items fail or
    refuse, and a failure never stops a later item.

    Returns ``{"batch_id": <id>, "results": [{index, verb, target, status,
    error}]}`` with ``status`` in ``applied|failed|duplicate|refused``.
    """
    specs = list(items or [])
    batch_id = _new_batch_id(valid_date)
    batch: dict[str, Any] = {
        "id": batch_id,
        "item_action_ids": [],
        "created_at": ra._now_iso(now),
        "undone_at": None,
        "undo_report": None,
        "items": [],
    }
    _save_batch(valid_date, batch)

    results: list[dict] = []
    for index, raw in enumerate(specs):
        verb, target, args = _spec(raw)
        status, error, action_id, mine = _execute_item(
            valid_date, verb, target, args, batch_id,
            todoist, store, capacities, now)
        batch["items"].append({
            "index": index, "verb": verb, "target": target, "args": args,
            "action_id": action_id, "status": status, "error": error,
        })
        _claim_action(batch, action_id, mine)
        _save_batch(valid_date, batch)
        results.append(_result(index, verb, target, status, error))
    return {"batch_id": batch_id, "results": results}


def retry_batch(
    valid_date: date,
    batch_id: str,
    *,
    todoist: Any = None,
    store: Any = None,
    capacities: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Re-run only the batch items whose latest status is not settled.

    ``applied``/``duplicate`` items are reported as-is and are not re-invoked.
    An unknown batch is a clean ``RuntimeActionError``.
    """
    journal = ra.load_journal(valid_date)
    batch = _find_batch(journal, batch_id)
    if batch is None:
        raise ra.RuntimeActionError(f"unknown batch {batch_id!r}")

    results: list[dict] = []
    for item in batch.get("items") or []:
        index = item.get("index")
        verb = item.get("verb")
        target = item.get("target")
        args = item.get("args") or {}
        if item.get("status") in _SETTLED:
            results.append(_result(index, verb, target,
                                   item["status"], item.get("error")))
            continue
        status, error, action_id, mine = _execute_item(
            valid_date, verb, target, args, batch_id,
            todoist, store, capacities, now)
        item["status"] = status
        item["error"] = error
        if action_id is not None:
            item["action_id"] = action_id
        _claim_action(batch, action_id, mine)
        _save_batch(valid_date, batch)
        results.append(_result(index, verb, target, status, error))
    return {"batch_id": batch_id, "results": results}
