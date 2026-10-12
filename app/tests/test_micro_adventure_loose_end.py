"""Loose-end resolution: the Set up day banner's Did it / Dismiss.

The pure rule answers only a still-pending entry. The route is token-guarded
and writes nothing but the micro-adventure log, so it never touches today's
plan, the day frame, or Todoist.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as main_mod  # noqa: E402
import micro_adventure as ma  # noqa: E402
from micro_adventure import HistoryEntry  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))
import tdtb_gather as gather  # noqa: E402

TODAY = date(2026, 7, 12)
LOG_PATH = ma.history_path


def _entry(day: str, entry_id: str, done: bool | None) -> HistoryEntry:
    return HistoryEntry(
        date=date.fromisoformat(day),
        id=entry_id,
        idea=f"idea {entry_id}",
        todoist_task_id=None,
        done=done,
    )


class TestResolveLooseEnd:
    def test_did_it_marks_the_pending_entry_done_and_keeps_the_rest(self):
        hist = (_entry("2026-07-11", "ma03", None), _entry("2026-07-10", "ma01", True))
        out = ma.resolve_loose_end(hist, date_=date(2026, 7, 11), entry_id="ma03", done=True)
        assert [(e.id, e.done) for e in out] == [("ma03", True), ("ma01", True)]

    def test_dismiss_marks_the_pending_entry_not_done(self):
        hist = (_entry("2026-07-11", "ma03", None),)
        out = ma.resolve_loose_end(hist, date_=date(2026, 7, 11), entry_id="ma03", done=False)
        assert out is not None and out[0].done is False

    def test_an_already_answered_entry_is_not_resolvable_again(self):
        hist = (_entry("2026-07-11", "ma03", True),)
        assert ma.resolve_loose_end(hist, date_=date(2026, 7, 11), entry_id="ma03", done=False) is None

    def test_unknown_date_or_id_is_none(self):
        hist = (_entry("2026-07-11", "ma03", None),)
        assert ma.resolve_loose_end(hist, date_=date(2026, 7, 11), entry_id="ma99", done=True) is None
        assert ma.resolve_loose_end(hist, date_=date(2026, 7, 10), entry_id="ma03", done=True) is None


@pytest.fixture
def vault(tmp_path) -> Path:
    root = tmp_path / "vault-root"
    root.mkdir()
    return root


@pytest.fixture
def client(vault, monkeypatch) -> TestClient:
    monkeypatch.setattr(gather, "effective_date", lambda now: TODAY)
    app = main_mod.create_app(vault_root=vault)
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _write_log(vault: Path, history_yaml: str) -> None:
    p = LOG_PATH()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "---\ndescription: test log\nschema_version: 1\nhistory:\n" + history_yaml + "---\n",
        encoding="utf-8",
    )


PENDING = "  - date: 2026-07-11\n    id: ma03\n    idea: Ride bike somewhere\n    done:\n"


def _post(client: TestClient, body: dict, *, token: bool = True):
    headers = {"X-TDTB-Token": client.app_token} if token else {}
    return client.post("/micro-adventure/loose-end", json=body, headers=headers)


class TestLooseEndRoute:
    def test_requires_the_token(self, client, vault):
        _write_log(vault, PENDING)
        r = _post(client, {"date": "2026-07-11", "id": "ma03", "done": True}, token=False)
        assert r.status_code == 403

    def test_did_it_writes_done_and_clears_the_banner(self, client, vault):
        _write_log(vault, PENDING)
        assert client.get("/plan-inputs").json()["micro_adventure"]["pending_confirm"] is not None
        r = _post(client, {"date": "2026-07-11", "id": "ma03", "done": True})
        assert r.status_code == 200
        assert [(e.id, e.done) for e in ma.read_history(LOG_PATH())] == [("ma03", True)]
        assert client.get("/plan-inputs").json()["micro_adventure"]["pending_confirm"] is None

    def test_dismiss_writes_not_done(self, client, vault):
        _write_log(vault, PENDING)
        assert _post(client, {"date": "2026-07-11", "id": "ma03", "done": False}).status_code == 200
        assert [(e.id, e.done) for e in ma.read_history(LOG_PATH())] == [("ma03", False)]

    def test_unknown_entry_is_404_and_the_log_is_untouched(self, client, vault):
        _write_log(vault, PENDING)
        before = (LOG_PATH()).read_text(encoding="utf-8")
        r = _post(client, {"date": "2026-07-11", "id": "ma99", "done": True})
        assert r.status_code == 404
        assert (LOG_PATH()).read_text(encoding="utf-8") == before

    def test_malformed_date_is_422(self, client, vault):
        _write_log(vault, PENDING)
        r = _post(client, {"date": "11 Jul", "id": "ma03", "done": True})
        assert r.status_code == 422
