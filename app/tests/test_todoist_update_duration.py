"""Todoist UPDATE-path duration — mocked transport only, no live API calls.

Confirmed bug: the commit retime path sent only the due, so a planned 90-min
task kept its old Todoist duration. The planned length must ride in the SAME
POST /tasks/{id} as the due (one call — a separate duration call would leave a
half-applied block when it fails), and must be omitted entirely when the plan
carries no positive duration.
"""
from __future__ import annotations

import json

import httpx

from todoist_client import TodoistClient


def make_client(handler) -> TodoistClient:
    return TodoistClient(token="test-token", transport=httpx.MockTransport(handler))


def _capture(captured: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["calls"] = captured.get("calls", 0) + 1
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "42"})

    return handler


def test_reschedule_task_sends_duration_with_due_in_one_call():
    captured: dict = {}
    client = make_client(_capture(captured))

    client.reschedule_task("42", "today at 20:30", duration=90,
                           duration_unit="minute")

    assert captured["calls"] == 1
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/tasks/42"
    assert captured["body"] == {
        "due_string": "today at 20:30",
        "duration": 90,
        "duration_unit": "minute",
    }


def test_reschedule_task_without_duration_omits_duration_fields():
    captured: dict = {}
    client = make_client(_capture(captured))

    client.reschedule_task("42", "today at 20:30")

    assert captured["body"] == {"due_string": "today at 20:30"}


def test_reschedule_task_datetime_sends_duration_in_one_call():
    captured: dict = {}
    client = make_client(_capture(captured))

    client.reschedule_task_datetime("42", "2026-07-12T09:00:00", duration=45)

    assert captured["calls"] == 1
    assert captured["body"] == {
        "due_datetime": "2026-07-12T09:00:00",
        "duration": 45,
        "duration_unit": "minute",
    }


def test_reschedule_task_datetime_without_duration_omits_it():
    captured: dict = {}
    client = make_client(_capture(captured))

    client.reschedule_task_datetime("42", "2026-07-12T09:00:00")

    assert captured["body"] == {"due_datetime": "2026-07-12T09:00:00"}
