"""prompt_export.py — the dedicated Commit-only Todoist export lane (U5 B2).

The morning prompts are private local runtime state (``prompt_state.py``).
This module is the ONLY surface that may turn a prompt draft into an external
Todoist task, and only when an explicit live Commit dispatch calls it:

* ``megan_nicety`` and ``stoic_intention`` export; ``intention`` NEVER does.
* Authorization is read from server state — the persistent undated opt-in
  record and the current logical-day draft store. Browser captures/opt-ins
  in a request body are never authorization.
* An opt-out or an empty draft produces no task.
* The due is the CIVIL calendar date (``datetime.now().date()``), not the
  logical-day date, so a pre-02:00 commit never back-dates the task.
* Routing reuses the retired capture lane's Inbox default (``project_id``
  omitted). No ad-hoc project is silently invented.
* Every attempt is guarded by a durable content-free receipt
  (``export_receipts.py``): a ``pending`` record is written BEFORE the
  provider call, ``done`` afterwards; an already-``done`` key is skipped, and
  an unconfirmed key is stranded as ``needs_review`` and never re-created.

The lane never raises for a provider or receipt failure: it returns bounded,
content-free outcomes so the Commit result stays honest and the caller can
surface review state. Prompt text is sent only to the provider; it is never
placed in a receipt, a log, an error, or a response.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

import export_receipts
import prompt_state

#: Prompt keys that export to Todoist. ``intention`` is deliberately absent —
#: it is private and never leaves the local draft store.
PROMPT_EXPORT_KEYS: tuple[str, ...] = ("megan_nicety", "stoic_intention")

#: The single export action. Extending this set is a contract change.
EXPORT_ACTION = export_receipts.ACTION_TODOIST_TASK

#: The routing the retired capture lane used: a project_id-less task lands in
#: the user's Todoist Inbox. Kept explicit so no ad-hoc project is invented.
EXPORT_ROUTING = "Inbox"

#: Bounded outcome statuses surfaced in the Commit result.
OUTCOME_DONE = "done"
OUTCOME_NEEDS_REVIEW = "needs_review"
OUTCOME_BLOCKED = "blocked"

_REASON_ALREADY_EXPORTED = "already exported for this logical day"
_REASON_PRIOR_UNCONFIRMED = "a prior attempt is unconfirmed — review before retrying"
_REASON_RECEIPT_STORE = "export receipt store is unavailable — nothing was created"
_REASON_PROVIDER_ERROR = "provider rejected the export — review before retrying"
_REASON_NO_TASK_ID = "provider returned no task id — review before retrying"
_REASON_RECEIPT_WRITE = "provider task created but the receipt could not be confirmed"


class TodoistExportLike(Protocol):
    def create_task(
        self,
        content: str,
        project_id: str | None = ...,
        due_date: str | None = ...,
        **fields: Any,
    ) -> dict: ...


@dataclass(frozen=True)
class PromptExportPlan:
    """One authorized export: a prompt key whose opt-in is on and draft is set.

    ``content`` is the private prompt text. It is deliberately absent from
    :meth:`as_preview` and from every outcome/receipt shape.
    """

    day: str
    civil_date: str
    prompt_key: str
    action: str
    content: str

    def as_preview(self) -> dict[str, Any]:
        return {
            "prompt_key": self.prompt_key,
            "action": self.action,
            "status": "planned",
        }


@dataclass(frozen=True)
class PromptExportOutcome:
    """The bounded, content-free result of one export attempt."""

    day: str
    prompt_key: str
    action: str
    status: str
    task_id: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt_key": self.prompt_key,
            "action": self.action,
            "status": self.status,
            "task_id": self.task_id,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Planning (pure, content-free preview)
# ---------------------------------------------------------------------------

def plan_prompt_exports(
    *,
    day: str,
    civil_date: str,
    optins: dict[str, bool] | Any,
    drafts: dict[str, str] | Any,
) -> list[PromptExportPlan]:
    """Authorized exports for ``day`` from server opt-ins and local drafts.

    ``intention`` is never planned. An opt-in that is not exactly ``True``, or
    a draft that is absent/blank/non-string, contributes nothing."""
    plans: list[PromptExportPlan] = []
    for key in PROMPT_EXPORT_KEYS:
        if optins.get(key) is not True:
            continue
        text = drafts.get(key)
        if not isinstance(text, str) or not text.strip():
            continue
        plans.append(PromptExportPlan(
            day=day, civil_date=civil_date, prompt_key=key,
            action=EXPORT_ACTION, content=text.strip(),
        ))
    return plans


def preview_prompt_exports(
    *,
    day: str,
    civil_date: str,
    optins: dict[str, bool] | Any,
    drafts: dict[str, str] | Any,
) -> list[dict[str, Any]]:
    """Content-free read-only preview for the shadow manifest.

    Never writes a receipt and never performs a provider call."""
    return [p.as_preview() for p in plan_prompt_exports(
        day=day, civil_date=civil_date, optins=optins, drafts=drafts,
    )]


# ---------------------------------------------------------------------------
# Server-state reads (fail closed)
# ---------------------------------------------------------------------------

def _server_optins() -> dict[str, bool]:
    try:
        return dict(prompt_state.load_optins().optins)
    except prompt_state.PromptStateError:
        return {}


def _server_drafts(day: str) -> dict[str, str]:
    try:
        return dict(prompt_state.load_drafts(day).drafts)
    except prompt_state.PromptStateError:
        return {}


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _redacted(exc: BaseException) -> str:
    """A bounded, content-free reason for a typed receipt failure.

    Only the exception class name is surfaced; the message could carry raw
    document bytes from a malformed store, so it is never echoed."""
    return f"export receipt store error ({type(exc).__name__})"


def _run_one(plan: PromptExportPlan, todoist: TodoistExportLike) -> PromptExportOutcome:
    try:
        decision = export_receipts.begin_export(
            day=plan.day, prompt_key=plan.prompt_key, action=plan.action,
        )
    except export_receipts.ExportReceiptError as exc:
        # Fail closed: the durable state is unknown, so no task is created and
        # no receipt is written. The offending bytes are preserved.
        return PromptExportOutcome(
            day=plan.day, prompt_key=plan.prompt_key, action=plan.action,
            status=OUTCOME_BLOCKED, reason=_redacted(exc),
        )

    if decision.action == export_receipts.SKIP_DONE:
        return PromptExportOutcome(
            day=plan.day, prompt_key=plan.prompt_key, action=plan.action,
            status=OUTCOME_DONE, task_id=decision.receipt.task_id,
            reason=_REASON_ALREADY_EXPORTED,
        )
    if decision.action == export_receipts.NEEDS_REVIEW:
        return PromptExportOutcome(
            day=plan.day, prompt_key=plan.prompt_key, action=plan.action,
            status=OUTCOME_NEEDS_REVIEW, task_id=decision.receipt.task_id,
            reason=decision.receipt.reason or _REASON_PRIOR_UNCONFIRMED,
        )

    # decision.action == BEGIN: this caller owns the only provider call.
    try:
        created = todoist.create_task(
            plan.content, project_id=None, due_date=plan.civil_date,
        )
    except Exception:  # noqa: BLE001 — provider failure never crashes the commit
        _safe_fail(plan, _REASON_PROVIDER_ERROR)
        return PromptExportOutcome(
            day=plan.day, prompt_key=plan.prompt_key, action=plan.action,
            status=OUTCOME_NEEDS_REVIEW, reason=_REASON_PROVIDER_ERROR,
        )

    task_id = None
    if isinstance(created, dict):
        raw = created.get("id")
        if raw is not None:
            task_id = str(raw).strip() or None
    if task_id is None:
        _safe_fail(plan, _REASON_NO_TASK_ID)
        return PromptExportOutcome(
            day=plan.day, prompt_key=plan.prompt_key, action=plan.action,
            status=OUTCOME_NEEDS_REVIEW, reason=_REASON_NO_TASK_ID,
        )

    try:
        export_receipts.complete_export(
            day=plan.day, prompt_key=plan.prompt_key, action=plan.action,
            task_id=task_id,
        )
    except export_receipts.ExportReceiptError:
        # The task exists but the done receipt did not persist; the pending
        # record remains, so a later run strands it as needs_review and never
        # blind-recreates.
        return PromptExportOutcome(
            day=plan.day, prompt_key=plan.prompt_key, action=plan.action,
            status=OUTCOME_NEEDS_REVIEW, task_id=task_id,
            reason=_REASON_RECEIPT_WRITE,
        )
    return PromptExportOutcome(
        day=plan.day, prompt_key=plan.prompt_key, action=plan.action,
        status=OUTCOME_DONE, task_id=task_id,
    )


def _safe_fail(plan: PromptExportPlan, reason: str) -> None:
    try:
        export_receipts.fail_export(
            day=plan.day, prompt_key=plan.prompt_key, action=plan.action,
            reason=reason,
        )
    except export_receipts.ExportReceiptError:
        pass  # a receipt write failure is already reflected by the outcome


def run_prompt_exports(
    *,
    day: str,
    civil_date: str,
    todoist: TodoistExportLike | None,
    optins: dict[str, bool] | None = None,
    drafts: dict[str, str] | None = None,
) -> list[PromptExportOutcome]:
    """Run the Commit-only export lane for the current logical day.

    Reads server opt-ins/drafts unless explicitly supplied. ``intention`` is
    never exported. A missing client blocks any planned export (nothing is
    created) without raising."""
    if optins is None:
        optins = _server_optins()
    if drafts is None:
        drafts = _server_drafts(day)
    plans = plan_prompt_exports(
        day=day, civil_date=civil_date, optins=optins, drafts=drafts,
    )
    if todoist is None:
        return [
            PromptExportOutcome(
                day=p.day, prompt_key=p.prompt_key, action=p.action,
                status=OUTCOME_BLOCKED, reason="todoist client unavailable",
            )
            for p in plans
        ]
    return [_run_one(plan, todoist) for plan in plans]


def attach_to_report(report: Any, outcomes: list[PromptExportOutcome]) -> Any:
    """Attach bounded export statuses to a Commit report (no prompt text).

    ``report["ok"]`` keeps its existing four-surface meaning; the export lane
    reports through ``prompt_exports``/``prompt_exports_ok`` so a stranded
    receipt is visible without silently masking a landed vault/calendar write."""
    if not isinstance(report, dict):
        return report
    entries = [o.as_dict() for o in outcomes]
    report["prompt_exports"] = entries
    report["prompt_exports_ok"] = all(e["status"] == OUTCOME_DONE for e in entries)
    return report


def civil_date_for(now: "Any") -> date:
    """The civil calendar date of ``now`` (a ``datetime``), distinct from the
    pre-02:00 logical day used to key drafts."""
    return now.date()
