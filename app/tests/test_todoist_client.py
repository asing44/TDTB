"""Tests for todoist_client.py — mocked transport only, no live API calls."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import httpx
import pytest

from todoist_client import (
    TodoistClient,
    TodoistError,
    TodoistTokenError,
    exclude_reminders,
    load_token,
)


def make_client(handler) -> TodoistClient:
    transport = httpx.MockTransport(handler)
    return TodoistClient(token="test-token", transport=transport)


# ---------------------------------------------------------------------------
# get_filter_tasks
# ---------------------------------------------------------------------------

def test_get_filter_tasks_encodes_filter_and_limit():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200, json={"results": [{"id": "1", "content": "task"}], "next_cursor": None}
        )

    client = make_client(handler)
    result = client.get_filter_tasks("today | overdue", limit=50)

    assert captured["params"]["query"] == "today | overdue"
    assert captured["params"]["limit"] == "50"
    assert captured["auth"] == "Bearer test-token"
    assert result == [{"id": "1", "content": "task"}]


def test_get_filter_tasks_without_limit_omits_param():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": [], "next_cursor": None})

    client = make_client(handler)
    client.get_filter_tasks("2360031067")

    assert "limit" not in captured["params"]


def test_get_filter_tasks_auto_paginates_to_exhaustion():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        if "cursor" not in params:
            return httpx.Response(
                200, json={"results": [{"id": "1"}], "next_cursor": "cursor-2"}
            )
        assert params["cursor"] == "cursor-2"
        return httpx.Response(200, json={"results": [{"id": "2"}], "next_cursor": None})

    client = make_client(handler)
    result = client.get_filter_tasks("today")

    assert len(calls) == 2
    assert result == [{"id": "1"}, {"id": "2"}]


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------

def test_get_task():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/tasks/123"
        return httpx.Response(200, json={"id": "123", "content": "hi"})

    client = make_client(handler)
    result = client.get_task("123")
    assert result["id"] == "123"


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------

def test_create_task_payload_shape():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "999"})

    client = make_client(handler)
    client.create_task(
        content="Do the thing",
        project_id="6fgXPMw28j7cRFMH",
        due_string="tomorrow",
        priority=3,
        labels=["🔔Reminder"],
        duration=30,
        duration_unit="minute",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/tasks"
    assert captured["body"] == {
        "content": "Do the thing",
        "project_id": "6fgXPMw28j7cRFMH",
        "due_string": "tomorrow",
        "priority": 3,
        "labels": ["🔔Reminder"],
        "duration": 30,
        "duration_unit": "minute",
    }


def test_create_task_minimal_payload_omits_optional_fields():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "1"})

    client = make_client(handler)
    client.create_task(content="minimal", project_id="p1")

    assert captured["body"] == {"content": "minimal", "project_id": "p1"}


# ---------------------------------------------------------------------------
# update_task / reschedule_task
# ---------------------------------------------------------------------------

def test_update_task_sends_given_fields():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "42"})

    client = make_client(handler)
    client.update_task("42", priority=4, content="new content")

    assert captured["path"] == "/api/v1/tasks/42"
    assert captured["body"] == {"priority": 4, "content": "new content"}


def test_reschedule_task_only_touches_due_string():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "42"})

    client = make_client(handler)
    client.reschedule_task("42", "next monday")

    assert captured["path"] == "/api/v1/tasks/42"
    assert captured["body"] == {"due_string": "next monday"}


# ---------------------------------------------------------------------------
# close_task
# ---------------------------------------------------------------------------

def test_close_task():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/tasks/7/close"
        assert request.method == "POST"
        return httpx.Response(204)

    client = make_client(handler)
    client.close_task("7")


# ---------------------------------------------------------------------------
# error surface
# ---------------------------------------------------------------------------

def test_non_200_raises_todoist_error_with_status_and_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="task not found")

    client = make_client(handler)
    with pytest.raises(TodoistError) as exc_info:
        client.get_task("missing")

    assert exc_info.value.status_code == 404
    assert "task not found" in exc_info.value.body


def test_429_retries_once_then_succeeds():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="rate limited")
        return httpx.Response(200, json={"results": [{"id": "1"}], "next_cursor": None})

    client = make_client(handler)
    result = client.get_filter_tasks("today")

    assert len(calls) == 2
    assert result == [{"id": "1"}]


def test_429_retries_once_then_fails_no_further_retries():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "0"}, text="still limited")

    client = make_client(handler)
    with pytest.raises(TodoistError) as exc_info:
        client.get_filter_tasks("today")

    assert len(calls) == 2
    assert exc_info.value.status_code == 429


def test_429_retry_after_capped_at_30s(monkeypatch):
    sleeps = []
    monkeypatch.setattr("todoist_client.time.sleep", lambda s: sleeps.append(s))

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "9999"}, text="rate limited")
        return httpx.Response(200, json={"results": [], "next_cursor": None})

    client = make_client(handler)
    client.get_filter_tasks("today")

    assert sleeps == [30.0]


# ---------------------------------------------------------------------------
# exclude_reminders
# ---------------------------------------------------------------------------

def test_exclude_reminders_drops_reminder_label():
    tasks = [
        {"id": "1", "content": "normal task", "labels": []},
        {"id": "2", "content": "reminder task", "labels": ["🔔Reminder"]},
    ]
    result = exclude_reminders(tasks)
    assert [t["id"] for t in result] == ["1"]


def test_exclude_reminders_drops_nudge_prefix():
    tasks = [
        {"id": "1", "content": "🔔 Nudge: drink water", "labels": []},
        {"id": "2", "content": "not a nudge", "labels": []},
    ]
    result = exclude_reminders(tasks)
    assert [t["id"] for t in result] == ["2"]


def test_exclude_reminders_passthrough_when_no_match():
    tasks = [
        {"id": "1", "content": "keep me", "labels": ["some-other-label"]},
        {"id": "2", "content": "keep me too", "labels": None},
    ]
    result = exclude_reminders(tasks)
    assert [t["id"] for t in result] == ["1", "2"]


# ---------------------------------------------------------------------------
# load_token
# ---------------------------------------------------------------------------

def test_load_token_happy_path(tmp_path: Path):
    env_file = tmp_path / "env"
    env_file.write_text("TODOIST_TOKEN=abc123\nOTHER_KEY=ignored\n")
    os.chmod(env_file, 0o600)

    assert load_token(env_file) == "abc123"


def test_load_token_missing_key(tmp_path: Path):
    env_file = tmp_path / "env"
    env_file.write_text("SOME_OTHER_KEY=value\n")
    os.chmod(env_file, 0o600)

    with pytest.raises(TodoistTokenError, match="TODOIST_TOKEN"):
        load_token(env_file)


def test_load_token_missing_file(tmp_path: Path):
    with pytest.raises(TodoistTokenError, match="not found"):
        load_token(tmp_path / "nonexistent")


def test_load_token_bad_permissions(tmp_path: Path):
    env_file = tmp_path / "env"
    env_file.write_text("TODOIST_TOKEN=abc123\n")
    os.chmod(env_file, 0o644)

    with pytest.raises(TodoistTokenError, match="permissions"):
        load_token(env_file)


def test_reschedule_task_datetime_posts_due_datetime_only():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "t1", "due": {
            "date": "2026-07-12T09:00:00", "is_recurring": True}})

    client = make_client(handler)
    client.reschedule_task_datetime("t1", "2026-07-12T09:00:00")

    assert captured["path"].endswith("/tasks/t1")
    assert captured["body"] == {"due_datetime": "2026-07-12T09:00:00"}


# ---------------------------------------------------------------------------
# T20: reopen_task / delete_task
# ---------------------------------------------------------------------------

def test_reopen_task():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/tasks/7/reopen"
        assert request.method == "POST"
        return httpx.Response(204)

    client = make_client(handler)
    client.reopen_task("7")


def test_delete_task():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/tasks/7"
        assert request.method == "DELETE"
        return httpx.Response(204)

    client = make_client(handler)
    client.delete_task("7")
