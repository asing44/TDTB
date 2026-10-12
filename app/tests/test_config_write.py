"""Tests for config_write (anchored-block start writer) and its route.

The writer edits ONE value in the tracked repo config as a surgical text
replacement, so the byte-level diff test is the contract: only the targeted
``Start`` string may change. conftest points ``TDTB_REPO_CONFIG`` and
``TDTB_HOME`` at per-test tmp paths, so no test touches the real config."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import app_config  # noqa: E402
import config_write  # noqa: E402
import main as main_mod  # noqa: E402

# Deliberately not what json.dumps would emit: 2-space indent, a nested
# "Start" under template_blocks that must stay untouched, unicode, a trailing
# newline, and a duplicated start value elsewhere in the file.
REPO_CONFIG = """{
  "version": 1,
  "defaults": {
    "eod": "11:45 PM"
  },
  "anchored_blocks": [
    {
      "Block": "Sudsing",
      "Type": "hard",
      "Start": "5:45 PM",
      "End": null,
      "Duration": "30m",
      "Days": "daily",
      "overlap_allowed": false,
      "Notes": "café \\"quoted\\""
    },
    {
      "Block": "Foods Dinner",
      "Type": "window",
      "Start": "6:00 PM",
      "End": "8:30 PM",
      "Duration": "60m",
      "Days": "daily",
      "overlap_allowed": false,
      "Notes": null
    }
  ],
  "template_blocks": {
    "Trinoor Hours": [
      {
        "Start": "5:45 PM",
        "End": "6:15 PM"
      }
    ]
  }
}
"""


@pytest.fixture
def repo_cfg() -> Path:
    path = app_config.repo_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(REPO_CONFIG, encoding="utf-8")
    return path


class TestParseStart:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("17:45", "5:45 PM"),
            ("07:05", "7:05 AM"),
            ("00:00", "12:00 AM"),
            ("12:00", "12:00 PM"),
            ("23:59", "11:59 PM"),
            ("5:45 PM", "5:45 PM"),
            ("5:45 pm", "5:45 PM"),
            ("05:45 PM", "5:45 PM"),
            ("12:30 AM", "12:30 AM"),
            ("12:30 PM", "12:30 PM"),
            (" 6:15 AM ", "6:15 AM"),
        ],
    )
    def test_normalizes(self, raw, expected):
        assert config_write.normalize_start(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["", "noon", "25:00", "13:00 PM", "0:30 AM", "5:75 PM", "5:45", "5 PM", "17:45:10", None, 1745],
    )
    def test_rejects(self, raw):
        # "5:45" is ambiguous without a suffix only when hour < 13 AND no
        # 24h form is meant; the contract is HH:MM (two-digit) or H:MM AM/PM.
        with pytest.raises(config_write.InvalidStartError):
            config_write.normalize_start(raw)

    def test_two_digit_24h_below_ten_is_accepted(self):
        assert config_write.normalize_start("09:30") == "9:30 AM"


class TestSaveAnchoredStart:
    def test_changes_only_the_one_value_byte_for_byte(self, repo_cfg):
        before = repo_cfg.read_bytes().decode("utf-8")
        result = config_write.save_anchored_start("Sudsing", "18:15")
        after = repo_cfg.read_bytes().decode("utf-8")
        assert result == {"block": "Sudsing", "start": "6:15 PM", "previous_start": "5:45 PM"}
        # Exactly one replacement: the sibling entry, the nested template
        # block's identical "5:45 PM", the indentation and the trailing
        # newline are all untouched.
        expected = before.replace(
            '"Type": "hard",\n      "Start": "5:45 PM"',
            '"Type": "hard",\n      "Start": "6:15 PM"',
            1,
        )
        assert after == expected
        assert after.endswith("}\n")
        assert after.count("5:45 PM") == 1  # the template_blocks one remains
        assert json.loads(after)["anchored_blocks"][0]["Start"] == "6:15 PM"

    def test_value_of_different_length_still_a_single_replacement(self, repo_cfg):
        before = repo_cfg.read_text(encoding="utf-8")
        config_write.save_anchored_start("Foods Dinner", "09:05")
        after = repo_cfg.read_text(encoding="utf-8")
        assert after == before.replace('"Start": "6:00 PM"', '"Start": "9:05 AM"', 1)

    def test_key_order_and_other_entries_preserved(self, repo_cfg):
        before = json.loads(repo_cfg.read_text(encoding="utf-8"))
        config_write.save_anchored_start("Foods Dinner", "7:00 PM")
        after = json.loads(repo_cfg.read_text(encoding="utf-8"))
        assert list(after) == list(before)
        assert list(after["anchored_blocks"][1]) == list(before["anchored_blocks"][1])
        before["anchored_blocks"][1]["Start"] = "7:00 PM"
        assert after == before

    def test_same_value_is_a_noop_that_reports_it(self, repo_cfg):
        before = repo_cfg.read_bytes()
        mtime = repo_cfg.stat().st_mtime_ns
        result = config_write.save_anchored_start("Sudsing", "5:45 PM")
        assert result["start"] == "5:45 PM"
        assert repo_cfg.read_bytes() == before
        assert repo_cfg.stat().st_mtime_ns == mtime

    def test_crlf_file_keeps_its_line_endings(self, repo_cfg):
        crlf = REPO_CONFIG.replace("\n", "\r\n").encode("utf-8")
        repo_cfg.write_bytes(crlf)
        config_write.save_anchored_start("Sudsing", "6:30 PM")
        after = repo_cfg.read_bytes()
        assert after == crlf.replace(
            b'"Type": "hard",\r\n      "Start": "5:45 PM"',
            b'"Type": "hard",\r\n      "Start": "6:30 PM"',
            1,
        )

    def test_unknown_block(self, repo_cfg):
        before = repo_cfg.read_bytes()
        with pytest.raises(config_write.UnknownBlockError):
            config_write.save_anchored_start("Nope", "6:00 PM")
        assert repo_cfg.read_bytes() == before

    def test_invalid_time_leaves_file_untouched(self, repo_cfg):
        before = repo_cfg.read_bytes()
        with pytest.raises(config_write.InvalidStartError):
            config_write.save_anchored_start("Sudsing", "later")
        assert repo_cfg.read_bytes() == before

    def test_missing_repo_config_is_unknown_block(self):
        with pytest.raises(config_write.UnknownBlockError):
            config_write.save_anchored_start("Sudsing", "6:00 PM")

    def test_local_override_defining_anchored_blocks_conflicts(self, repo_cfg):
        local = app_config.config_path()
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(
            json.dumps({"version": 1, "anchored_blocks": []}), encoding="utf-8"
        )
        before = repo_cfg.read_bytes()
        with pytest.raises(config_write.ShadowedByLocalConfigError) as err:
            config_write.save_anchored_start("Sudsing", "6:00 PM")
        assert "config.json" in str(err.value)
        assert repo_cfg.read_bytes() == before

    def test_local_override_without_anchored_blocks_is_fine(self, repo_cfg):
        local = app_config.config_path()
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(
            json.dumps({"version": 1, "sources": {"mode": "live"}}), encoding="utf-8"
        )
        config_write.save_anchored_start("Sudsing", "6:00 PM")
        assert json.loads(repo_cfg.read_text(encoding="utf-8"))["anchored_blocks"][0][
            "Start"
        ] == "6:00 PM"

    def test_write_is_atomic_rename_and_keeps_mode(self, repo_cfg, monkeypatch):
        os.chmod(repo_cfg, 0o640)
        calls = []
        real_replace = os.replace

        def spy(src, dst):
            calls.append((Path(src), Path(dst)))
            return real_replace(src, dst)

        monkeypatch.setattr(config_write.os, "replace", spy)
        config_write.save_anchored_start("Sudsing", "6:00 PM")
        assert len(calls) == 1
        src, dst = calls[0]
        assert dst == repo_cfg.resolve()
        assert src.parent == repo_cfg.resolve().parent and src != dst
        assert not src.exists()  # temp file consumed by the rename
        assert (repo_cfg.stat().st_mode & 0o777) == 0o640
        assert sorted(p.name for p in repo_cfg.parent.iterdir()) == [repo_cfg.name]

    def test_failed_write_keeps_original_and_cleans_temp(self, repo_cfg, monkeypatch):
        before = repo_cfg.read_bytes()

        def boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(config_write.os, "replace", boom)
        with pytest.raises(OSError):
            config_write.save_anchored_start("Sudsing", "6:00 PM")
        assert repo_cfg.read_bytes() == before
        assert sorted(p.name for p in repo_cfg.parent.iterdir()) == [repo_cfg.name]

    def test_running_app_sees_the_change_without_restart(self, repo_cfg):
        def start_of(name):
            doc = app_config.load_document()
            return next(b["Start"] for b in doc["anchored_blocks"] if b["Block"] == name)

        assert start_of("Sudsing") == "5:45 PM"
        config_write.save_anchored_start("Sudsing", "6:15 PM")
        assert start_of("Sudsing") == "6:15 PM"
        sections = app_config.load_sections()
        assert sections["Anchored Lifestyle Blocks"][0]["Start"] == "6:15 PM"


class TestRoute:
    @pytest.fixture
    def client(self, tmp_path) -> TestClient:
        vault = tmp_path / "vault-root"
        vault.mkdir()
        app = main_mod.create_app()
        c = TestClient(app)
        c.app_token = app.state.token
        return c

    def _post(self, client, body, token=True):
        headers = {"X-TDTB-Token": client.app_token} if token else {}
        return client.post("/config/anchored-blocks/start", json=body, headers=headers)

    def test_requires_token(self, client, repo_cfg):
        assert self._post(client, {"block": "Sudsing", "start": "6:00 PM"}, token=False).status_code == 403
        assert client.post(
            "/config/anchored-blocks/start",
            json={"block": "Sudsing", "start": "6:00 PM"},
            headers={"X-TDTB-Token": "wrong"},
        ).status_code == 403

    def test_saves_and_returns_normalized_start(self, client, repo_cfg):
        r = self._post(client, {"block": "Sudsing", "start": "18:15"})
        assert r.status_code == 200
        assert r.json() == {"block": "Sudsing", "start": "6:15 PM", "previous_start": "5:45 PM"}
        assert '"Start": "6:15 PM"' in repo_cfg.read_text(encoding="utf-8")

    def test_plan_inputs_reflects_the_saved_start(self, client, repo_cfg):
        self._post(client, {"block": "Foods Dinner", "start": "7:00 PM"})
        assert (
            app_config.load_document()["anchored_blocks"][1]["Start"] == "7:00 PM"
        )

    def test_unknown_block_404(self, client, repo_cfg):
        before = repo_cfg.read_bytes()
        r = self._post(client, {"block": "Dentist (calendar)", "start": "6:00 PM"})
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "anchored_block_not_found"
        assert repo_cfg.read_bytes() == before

    def test_unparseable_time_422(self, client, repo_cfg):
        before = repo_cfg.read_bytes()
        r = self._post(client, {"block": "Sudsing", "start": "teatime"})
        assert r.status_code == 422
        assert repo_cfg.read_bytes() == before

    def test_missing_fields_422(self, client, repo_cfg):
        assert self._post(client, {"block": "Sudsing"}).status_code == 422
        assert self._post(client, {"start": "6:00 PM"}).status_code == 422

    def test_local_override_shadow_409(self, client, repo_cfg):
        local = app_config.config_path()
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(
            json.dumps({"version": 1, "anchored_blocks": [{"Block": "Sudsing", "Start": "5:00 PM"}]}),
            encoding="utf-8",
        )
        before = repo_cfg.read_bytes()
        r = self._post(client, {"block": "Sudsing", "start": "6:00 PM"})
        assert r.status_code == 409
        body = r.json()["detail"]
        assert body["code"] == "anchored_blocks_shadowed"
        assert "override" in body["message"].lower()
        assert repo_cfg.read_bytes() == before

    def test_write_failure_500_bounded_and_untouched(self, client, repo_cfg, monkeypatch):
        before = repo_cfg.read_bytes()

        def boom(src, dst):
            raise OSError("/secret/path exploded")

        monkeypatch.setattr(config_write.os, "replace", boom)
        r = self._post(client, {"block": "Sudsing", "start": "6:00 PM"})
        assert r.status_code == 500
        assert "/secret/path" not in json.dumps(r.json())
        assert repo_cfg.read_bytes() == before
