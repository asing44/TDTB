"""todoist_client.py — httpx-based Todoist unified API v1 client.

Gate: TDD — tests/test_todoist_client.py must pass.

All filter/project/label IDs are caller-supplied (config layer) — never
hardcoded here (spec § 3.2). Token is resolved by the caller via
load_token() from ~/.config/tdtb/env (or any path), never read implicitly.

Migrated off the deprecated REST v2 API (retired — `rest/v2` now returns
410) onto the unified API v1 (https://api.todoist.com/api/v1). Verified
against the live OpenAPI spec (developer.todoist.com/api/v1) on 2026-07-12:
- GET /tasks/filter (query, cursor, limit) replaces GET /tasks?filter=...
  and returns a {"results": [...], "next_cursor": str|null} envelope —
  get_filter_tasks auto-paginates to exhaustion and still returns a flat
  list, preserving the old call contract.
- GET/POST /tasks/{task_id}, POST /tasks, POST /tasks/{task_id}/close are
  unchanged in path and body-field names (content, project_id, due_string,
  priority, labels, duration, duration_unit).
- Task IDs are opaque strings in both APIs — no int-parsing assumptions
  existed here to fix.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://api.todoist.com/api/v1"
DEFAULT_TIMEOUT = 15.0
MAX_RETRY_AFTER = 30.0

REMINDER_LABEL = "🔔Reminder"
NUDGE_PREFIX = "🔔 Nudge:"


class TodoistError(Exception):
    """Raised on a non-2xx Todoist API response."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body[:500]
        super().__init__(f"Todoist API error {status_code}: {self.body}")


class TodoistTokenError(Exception):
    """Raised when the token file is missing, malformed, or insecurely permissioned."""


def load_token(path: str | Path) -> str:
    """Parse KEY=VALUE lines from path, return TODOIST_TOKEN.

    Raises TodoistTokenError if the file is missing, TODOIST_TOKEN is absent,
    or the file's permissions are looser than 0600.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise TodoistTokenError(f"token file not found: {p}")

    mode = os.stat(p).st_mode & 0o777
    if mode & 0o077:
        raise TodoistTokenError(
            f"token file {p} has permissions {oct(mode)}; expected 0600 or stricter"
        )

    values: dict[str, str] = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()

    token = values.get("TODOIST_TOKEN") or values.get("TODOIST_API_TOKEN")
    if not token:
        raise TodoistTokenError(f"TODOIST_TOKEN (or TODOIST_API_TOKEN) not found in {p}")
    return token


class TodoistClient:
    """Thin httpx wrapper over the Todoist REST API v2."""

    def __init__(
        self,
        token: str,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = token
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {token}"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TodoistClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- internal request helper -------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, path, **kwargs)
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            delay = min(float(retry_after), MAX_RETRY_AFTER) if retry_after else 1.0
            time.sleep(delay)
            response = self._client.request(method, path, **kwargs)

        if not (200 <= response.status_code < 300):
            raise TodoistError(response.status_code, response.text)
        return response

    # -- task reads -----------------------------------------------------

    def get_filter_tasks(self, filter_id_or_query: str, limit: int | None = None) -> list[dict]:
        """GET /tasks/filter by filter_id_or_query (passed as the `query` param).

        v1 paginates via a `{"results": [...], "next_cursor": str|null}`
        envelope. Auto-paginates to exhaustion and returns a flat list so
        the call contract is unchanged for callers. `limit` bounds the
        page size per request, not the total result count.
        """
        params: dict[str, Any] = {"query": filter_id_or_query}
        if limit is not None:
            params["limit"] = limit

        results: list[dict] = []
        cursor: str | None = None
        while True:
            page_params = dict(params)
            if cursor is not None:
                page_params["cursor"] = cursor
            response = self._request("GET", "/tasks/filter", params=page_params)
            body = response.json()
            results.extend(body.get("results", []))
            cursor = body.get("next_cursor")
            if not cursor:
                break
        return results

    def get_task(self, task_id: str) -> dict:
        response = self._request("GET", f"/tasks/{task_id}")
        return response.json()

    # -- task writes ------------------------------------------------------

    def create_task(
        self,
        content: str,
        project_id: str | None = None,
        due_string: str | None = None,
        priority: int | None = None,
        labels: list[str] | None = None,
        duration: int | None = None,
        duration_unit: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {"content": content}
        if project_id is not None:
            # Omit for Inbox routing — v1 POST /tasks lands a project_id-less
            # task in the user's Inbox (a null project_id would be rejected).
            payload["project_id"] = project_id
        if due_string is not None:
            payload["due_string"] = due_string
        if priority is not None:
            payload["priority"] = priority
        if labels is not None:
            payload["labels"] = labels
        if duration is not None:
            payload["duration"] = duration
        if duration_unit is not None:
            payload["duration_unit"] = duration_unit
        response = self._request("POST", "/tasks", json=payload)
        return response.json()

    def update_task(self, task_id: str, **fields: Any) -> dict:
        response = self._request("POST", f"/tasks/{task_id}", json=fields)
        return response.json()

    def reschedule_task(self, task_id: str, due_string: str) -> dict:
        """Thin wrapper on update_task that ONLY sets due_string — never clobbers other fields.

        WARNING: due_string REPLACES the whole due, wiping any recurrence
        pattern — never use on a recurring task (use reschedule_task_datetime).
        """
        return self.update_task(task_id, due_string=due_string)

    def reschedule_task_datetime(self, task_id: str, due_datetime: str) -> dict:
        """Retime via due_datetime ("YYYY-MM-DDTHH:MM:SS") ONLY — the form that
        preserves an existing recurrence pattern (skill contract: recurring
        retimes must never go through due_string)."""
        return self.update_task(task_id, due_datetime=due_datetime)

    def reschedule_task_date(self, task_id: str, due_date: str) -> dict:
        """Advance a date-only due via due_date ("YYYY-MM-DD") ONLY — the
        non-completing form that preserves an existing recurrence pattern.
        Used by recurring Unassign so the occurrence advances without ever
        closing the task or wiping the recurrence string/type."""
        return self.update_task(task_id, due_date=due_date)

    def clear_task_date(self, task_id: str) -> dict:
        """Remove the task's due date entirely (non-recurring Unassign).

        ``due_string: ""`` is the supported "no due" write — never a
        completion operation, never a recurrence rewrite. Callers must only
        invoke this for non-recurring tasks (the engine reads the task's
        ``due.is_recurring`` before choosing clear vs advance)."""
        return self.update_task(task_id, due_string="")

    def close_task(self, task_id: str) -> None:
        self._request("POST", f"/tasks/{task_id}/close")

    def reopen_task(self, task_id: str) -> None:
        """T20: undo path for a runtime Complete."""
        self._request("POST", f"/tasks/{task_id}/reopen")

    def delete_task(self, task_id: str) -> None:
        """T20: permanent delete — only ever called through the runtime-action
        journal, which snapshots the full task as a before-image first."""
        self._request("DELETE", f"/tasks/{task_id}")


def exclude_reminders(tasks: list[dict]) -> list[dict]:
    """Drop tasks that are never-schedulable reminder/nudge pool noise.

    Excludes any task whose labels contain "🔔Reminder" or whose content
    starts with "🔔 Nudge:" (skill rule — these never enter the pool).
    """
    result = []
    for task in tasks:
        labels = task.get("labels") or []
        content = task.get("content") or ""
        if REMINDER_LABEL in labels:
            continue
        if content.startswith(NUDGE_PREFIX):
            continue
        result.append(task)
    return result
