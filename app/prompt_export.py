"""prompt_export.py — the dedicated Commit-only Todoist export lane (U5 B2).

The morning prompts are local runtime state (``prompt_state.py``). This
module is the ONLY surface that may turn a prompt draft into an external
Todoist task, and only when an explicit live Commit dispatch calls it:

* EVERY non-empty draft exports — ``intention``, ``megan_nicety``,
  ``stoic_intention``, and browser-defined custom prompts
  (``custom-<id>`` keys). Nothing is per-prompt opted into any more.
* **Task shape**: a BUILT-IN prompt exports as a task whose TITLE is that
  prompt's fixed label (:data:`BUILTIN_PROMPT_TITLES` — ``Reminder``,
  ``For Meegy``, ``Stoic``) and whose DESCRIPTION is the prompt text. A
  browser-defined custom prompt has no fixed label, so its text stays the
  TITLE and no description is written.
* **Description skips**: today's logical-day skip store
  (``prompt_state.load_description_skips``) may clear a built-in prompt's
  description. The task is STILL created with its fixed-label title; only the
  description body is omitted. An absent/``False``/unreadable flag writes the
  description. The flag never gates the export and never affects a custom
  prompt.
* Authorization is read from server state — the current logical-day draft
  store. Browser captures/opt-ins in a request body are never authorization,
  and the legacy per-prompt opt-in store is not consulted at all.
* **Disabled prompts**: today's logical-day disabled store
  (``prompt_state.load_prompt_disabled``) may turn a built-in prompt OFF for
  the day. A disabled prompt is NOT exported at all — it produces no plan
  entry, no provider call, and no receipt, so re-enabling it later the same day
  still exports. The store is never consulted for a custom prompt.
* An empty/absent/non-string draft produces no task.
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
surface review state. Prompt text is sent only to the provider (as the task
TITLE for a custom prompt, or as its DESCRIPTION for a built-in one); it is
never placed in a receipt, a log, an error, or a response.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Protocol

import export_receipts
import prompt_state

#: Prompt keys that export to Todoist. Every built-in prompt now exports
#: (including ``intention``); browser-defined custom prompts are discovered
#: from the draft store at plan time (see :func:`plan_prompt_exports`).
PROMPT_EXPORT_KEYS: tuple[str, ...] = prompt_state.PROMPT_KEYS

#: The single export action. Extending this set is a contract change.
EXPORT_ACTION = export_receipts.ACTION_TODOIST_TASK

#: The fixed task TITLE of each BUILT-IN prompt (user-approved labels). The
#: prompt text becomes the task DESCRIPTION instead. A browser-defined custom
#: prompt has no fixed label, so its text remains the title with no
#: description (see :func:`plan_prompt_exports`).
BUILTIN_PROMPT_TITLES: dict[str, str] = {
    "intention": "Reminder",
    "megan_nicety": "For Meegy",
    "stoic_intention": "Stoic",
}

#: The routing the retired capture lane used: a project_id-less task lands in
#: the user's Todoist Inbox. Kept explicit so no ad-hoc project is invented.
EXPORT_ROUTING = "Inbox"

#: Bounded outcome statuses surfaced in the Commit result.
OUTCOME_DONE = "done"
OUTCOME_NEEDS_REVIEW = "needs_review"
OUTCOME_BLOCKED = "blocked"
#: The main commit did not land, so the export lane was skipped fail-closed.
OUTCOME_SKIPPED_MAIN_FAILURE = "skipped_main_failure"

#: Report-level marker key for a lane skipped by a failed main commit. It is
#: never a prompt key and never reaches the receipt store.
SKIPPED_MAIN_FAILURE_KEY = "__main_failure__"

_REASON_ALREADY_EXPORTED = "already exported for this logical day"
_REASON_PRIOR_UNCONFIRMED = "a prior attempt is unconfirmed — review before retrying"
_REASON_RECEIPT_STORE = "export receipt store is unavailable — nothing was created"
_REASON_PROVIDER_ERROR = "provider rejected the export — review before retrying"
_REASON_NO_TASK_ID = "provider returned no task id — review before retrying"
_REASON_RECEIPT_WRITE = "provider task created but the receipt could not be confirmed"
_REASON_MAIN_FAILURE = "main commit did not land — exports skipped"


class TodoistExportLike(Protocol):
    def create_task(
        self,
        content: str,
        project_id: str | None = ...,
        due_date: str | None = ...,
        description: str | None = ...,
        **fields: Any,
    ) -> dict: ...


@dataclass(frozen=True)
class PromptExportPlan:
    """One authorized export: a prompt key with a non-empty draft.

    ``title`` is what the provider receives as the task TITLE: the fixed label
    for a built-in prompt, or the prompt text for a custom prompt. ``content``
    is the private prompt text and ``description`` the task body — the text for
    a built-in prompt whose description is not skipped, else ``None`` (no
    description is written at all). ``content``, ``description``, and a
    custom prompt's ``title`` are deliberately absent from :meth:`as_preview`
    and from every outcome/receipt shape.
    """

    day: str
    civil_date: str
    prompt_key: str
    action: str
    content: str
    title: str
    description: str | None = None

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
    drafts: dict[str, str] | Any,
    description_skips: Mapping[str, bool] | Any = None,
    disabled: Mapping[str, bool] | Any = None,
) -> list[PromptExportPlan]:
    """Every non-empty, enabled draft for ``day`` exports; no opt-in is
    consulted.

    Built-in keys plan in canonical order, then browser-defined custom keys
    (``custom-<id>``) in sorted order. A draft that is absent/blank/non-string,
    a key that is neither built-in nor custom, and a built-in key that
    ``disabled`` marks produce nothing at all.

    A built-in key plans its fixed label as ``title`` and its text as
    ``description``, unless ``description_skips`` marks that key — then the
    title stays and the description is ``None`` so the provider writes a task
    with no description. A custom key has no fixed label: its text is the title
    and it never carries a description."""
    plans: list[PromptExportPlan] = []
    for key in _planned_keys(drafts):
        if _is_disabled(disabled, key):
            continue
        text = drafts.get(key)
        if not isinstance(text, str) or not text.strip():
            continue
        title, description = _task_shape(key, text.strip(), description_skips)
        plans.append(PromptExportPlan(
            day=day, civil_date=civil_date, prompt_key=key,
            action=EXPORT_ACTION, content=text.strip(),
            title=title, description=description,
        ))
    return plans


def _task_shape(
    key: str, text: str, description_skips: Any
) -> tuple[str, str | None]:
    """The provider-facing ``(title, description)`` for one planned prompt."""
    label = BUILTIN_PROMPT_TITLES.get(key)
    if label is None:
        # Custom prompt: the text stays the title, and no description is written.
        return text, None
    if _description_is_skipped(description_skips, key):
        return label, None
    return label, text


def _description_is_skipped(description_skips: Any, key: str) -> bool:
    """True only for an explicit boolean ``True``; absent/other -> write."""
    if not isinstance(description_skips, Mapping):
        return False
    return description_skips.get(key) is True


def _is_disabled(disabled: Any, key: str) -> bool:
    """True only for an explicit boolean ``True`` built-in flag.

    Absent/other values mean ENABLED, and a custom prompt can never be
    disabled — the product toggle exists for the fixed-label prompts only."""
    if key not in BUILTIN_PROMPT_TITLES:
        return False
    if not isinstance(disabled, Mapping):
        return False
    return disabled.get(key) is True


def _planned_keys(drafts: Any) -> list[str]:
    """The recognized draft keys to consider, in deterministic order."""
    if not isinstance(drafts, Mapping):
        return []
    known = [key for key in PROMPT_EXPORT_KEYS if key in drafts]
    custom = sorted(
        key for key in drafts if prompt_state.is_custom_prompt_key(key)
    )
    return known + custom


def preview_prompt_exports(
    *,
    day: str,
    civil_date: str,
    drafts: dict[str, str] | Any,
    disabled: Mapping[str, bool] | Any = None,
) -> list[dict[str, Any]]:
    """Content-free read-only preview for the shadow manifest.

    Never writes a receipt and never performs a provider call."""
    return [p.as_preview() for p in plan_prompt_exports(
        day=day, civil_date=civil_date, drafts=drafts, disabled=disabled,
    )]


# ---------------------------------------------------------------------------
# Server-state reads (fail closed)
# ---------------------------------------------------------------------------

def _server_drafts(day: str) -> dict[str, str]:
    try:
        return dict(prompt_state.load_drafts(day).drafts)
    except prompt_state.PromptStateError:
        return {}


def _server_description_skips(day: str) -> dict[str, bool]:
    """Today's day-scoped description-skip flags (fail closed to all-False).

    An unreadable/malformed skip store must never suppress a description, so a
    read failure degrades to today's default behavior and preserves the
    offending bytes. The legacy undated opt-in store is never consulted here."""
    try:
        return dict(prompt_state.load_description_skips(day).skips)
    except prompt_state.PromptStateError:
        return {}


def _server_prompt_disabled(day: str) -> dict[str, bool]:
    """Today's day-scoped disabled-prompt flags (fail closed to all-enabled).

    Enabled is the product default, so an unreadable/malformed store must never
    silently suppress a prompt: the read failure degrades to today's default
    behavior and preserves the offending bytes."""
    try:
        return dict(prompt_state.load_prompt_disabled(day).disabled)
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
            plan.title, project_id=None, due_date=plan.civil_date,
            description=plan.description,
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
    drafts: dict[str, str] | None = None,
    description_skips: dict[str, bool] | None = None,
    disabled: dict[str, bool] | None = None,
) -> list[PromptExportOutcome]:
    """Run the Commit-only export lane for the current logical day.

    Reads the server drafts, the day-scoped description-skip flags, and the
    day-scoped disabled-prompt flags unless explicitly supplied. Every
    non-empty, enabled draft exports (including ``intention`` and custom
    prompts) — a skip flag changes only the task shape, while a disabled flag
    removes the prompt from the plan entirely. A missing client blocks any
    planned export (nothing is created) without raising."""
    if drafts is None:
        drafts = _server_drafts(day)
    if description_skips is None:
        description_skips = _server_description_skips(day)
    if disabled is None:
        disabled = _server_prompt_disabled(day)
    plans = plan_prompt_exports(
        day=day, civil_date=civil_date, drafts=drafts,
        description_skips=description_skips, disabled=disabled,
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


def skipped_main_failure_outcomes(*, day: str) -> list[PromptExportOutcome]:
    """The explicit, content-free result when the main commit did not land.

    One lane-level marker so the report shows the skip and
    ``prompt_exports_ok`` is false rather than vacuously true. No provider
    call is made and no receipt is written."""
    return [PromptExportOutcome(
        day=day, prompt_key=SKIPPED_MAIN_FAILURE_KEY, action=EXPORT_ACTION,
        status=OUTCOME_SKIPPED_MAIN_FAILURE, reason=_REASON_MAIN_FAILURE,
    )]


def run_prompt_exports_for_report(
    report: Any,
    *,
    day: str,
    civil_date: str,
    todoist: TodoistExportLike | None,
    drafts: dict[str, str] | None = None,
    description_skips: dict[str, bool] | None = None,
    disabled: dict[str, bool] | None = None,
) -> list[PromptExportOutcome]:
    """Fail-closed gate: export ONLY after the main commit landed ok.

    ``orchestrate.run_orchestrated`` reports ``ok`` true only when every write
    surface landed. When it did not (``report["ok"] is not True``), the lane
    performs no provider call and mutates no receipt; it returns one explicit
    content-free skipped outcome so the Commit result stays honest. A report
    that is not a mapping is treated the same way."""
    if not (isinstance(report, dict) and report.get("ok") is True):
        return skipped_main_failure_outcomes(day=day)
    return run_prompt_exports(
        day=day, civil_date=civil_date, todoist=todoist, drafts=drafts,
        description_skips=description_skips, disabled=disabled,
    )


def attach_to_report(report: Any, outcomes: list[PromptExportOutcome]) -> Any:
    """Attach bounded export statuses to a Commit report (no prompt text).

    ``report["ok"]`` keeps its existing four-surface meaning; the export lane
    reports through ``prompt_exports``/``prompt_exports_ok`` so a stranded
    receipt is visible without silently masking a landed Todoist/calendar write."""
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
