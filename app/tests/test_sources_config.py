"""Hand-typed ``sources`` key tolerance, unrecognized-key warnings and the
retired ``vault_enabled`` key.

Vault removal V4 deleted the ``sources.vault_enabled`` flag. The operator's
live ``~/.config/tdtb/config.json`` still carries
``sources: {vault_enabled: false, mode: "live"}``, so that exact shape must
keep loading: the legacy key is ignored silently (no effect, no validation
failure, no warning)."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app_config
import main


def _write(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class TestConfigKeyTolerance:
    """A hand-typed leading/trailing space in a JSON key must not silently
    discard the operator's instruction."""

    def test_sources_mode_reads_a_whitespace_key(self, tmp_path):
        path = _write(tmp_path, {"version": 1, "sources": {" mode": "live"}})
        assert app_config.sources_mode(path) == "live"

    def test_artifact_max_age_tolerates_whitespace_keys(self, tmp_path):
        path = _write(
            tmp_path,
            {"version": 1, "sources": {" artifact": {" max_age_minutes": 30}}},
        )
        assert app_config.artifact_max_age_minutes(path) == 30


class TestSourcesConfigWarnings:
    """Unrecognized keys are reported as written so a misspelled operator
    instruction is loud, not silently ignored."""

    def test_names_an_unrecognized_key_and_never_raises(self, tmp_path):
        path = _write(tmp_path, {"version": 1, "sources": {"mode": "live", "mod": "x"}})
        warnings = app_config.sources_config_warnings(path)
        assert len(warnings) == 1
        message = warnings[0]
        assert "'mod'" in message
        assert "known keys are" in message
        assert "may not be in effect" in message

    def test_whitespace_key_is_tolerated_and_reported_as_a_cleanup(self, tmp_path):
        # It IS honoured (the reader trims), so the warning must not claim it
        # was ignored. It is reported as a cleanup note instead.
        path = _write(tmp_path, {"version": 1, "sources": {" mode": "live"}})
        warnings = app_config.sources_config_warnings(path)
        assert len(warnings) == 1
        assert "' mode'" in warnings[0]
        assert "setting is in effect" in warnings[0]
        assert "ignored" not in warnings[0]
        assert app_config.sources_mode(path) == "live"

    def test_whitespace_and_typo_keys_report_separately(self, tmp_path):
        path = _write(
            tmp_path, {"version": 1, "sources": {" mode": "live", "modee": "x"}}
        )
        warnings = app_config.sources_config_warnings(path)
        assert len(warnings) == 2
        typo = [w for w in warnings if "may not be in effect" in w]
        padded = [w for w in warnings if "setting is in effect" in w]
        assert len(typo) == 1 and "modee" in typo[0]
        assert len(padded) == 1 and "' mode'" in padded[0]

    def test_nested_artifact_typo_is_reported(self, tmp_path):
        path = _write(
            tmp_path, {"version": 1, "sources": {"artifact": {"max_age_minute": 60}}}
        )
        warnings = app_config.sources_config_warnings(path)
        assert len(warnings) == 1
        assert "max_age_minute" in warnings[0]
        assert "sources.artifact" in warnings[0]

    def test_clean_section_does_not_warn(self, tmp_path):
        path = _write(
            tmp_path,
            {
                "version": 1,
                "sources": {"mode": "artifact", "artifact": {"max_age_minutes": 60}},
            },
        )
        assert app_config.sources_config_warnings(path) == []

    def test_empty_and_absent_sections_make_no_claim(self, tmp_path):
        assert app_config.sources_config_warnings(
            _write(tmp_path, {"version": 1, "sources": {}})) == []
        assert app_config.sources_config_warnings(
            _write(tmp_path, {"version": 1})) == []

    def test_unusable_document_does_not_warn(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text("{ not json", encoding="utf-8")
        assert app_config.sources_config_warnings(path) == []


LEGACY_LIVE_SHAPE = {"version": 1, "sources": {"vault_enabled": False, "mode": "live"}}


class TestLegacyVaultEnabledKey:
    """The operator's live config shape keeps working with the flag gone."""

    def test_the_flag_and_its_default_are_gone(self):
        assert not hasattr(app_config, "vault_enabled")
        assert not hasattr(app_config, "DEFAULT_VAULT_ENABLED")

    def test_legacy_shape_loads_and_mode_still_applies(self, tmp_path):
        path = _write(tmp_path, LEGACY_LIVE_SHAPE)
        assert app_config.load_document(path) is not None
        assert app_config.sources_mode(path) == "live"

    def test_legacy_key_is_ignored_without_a_warning(self, tmp_path):
        assert app_config.sources_config_warnings(_write(tmp_path, LEGACY_LIVE_SHAPE)) == []

    def test_legacy_key_in_any_value_or_padding_is_ignored_silently(self, tmp_path):
        for value in (True, False, "false", None, 3):
            path = _write(tmp_path, {"version": 1, "sources": {"vault_enabled": value}})
            assert app_config.sources_config_warnings(path) == []
        padded = _write(tmp_path, {"version": 1, "sources": {" vault_enabled": False}})
        assert app_config.sources_config_warnings(padded) == []
        assert app_config.load_document(padded) is not None

    def test_legacy_key_does_not_hide_a_real_typo(self, tmp_path):
        path = _write(
            tmp_path,
            {"version": 1, "sources": {"vault_enabled": False, "modee": "live"}},
        )
        warnings = app_config.sources_config_warnings(path)
        assert len(warnings) == 1 and "modee" in warnings[0]
        assert "vault_enabled" not in warnings[0].split("known keys are")[0]

    def test_a_layered_local_config_with_the_legacy_shape_adds_no_warning(self):
        # The default (no path) resolver reads the repo file and the local
        # override from the isolated TDTB_HOME.
        local = app_config.config_path()
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(json.dumps(LEGACY_LIVE_SHAPE), encoding="utf-8")
        assert app_config.sources_mode() == "live"
        assert app_config.sources_config_warnings() == []

    def test_plan_inputs_serves_with_the_legacy_config_and_no_warning(self):
        local = app_config.config_path()
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(json.dumps(LEGACY_LIVE_SHAPE), encoding="utf-8")
        app = main.create_app()
        app.state.build_read_clients = lambda c: (None, None)
        app.state.build_calendar_store = lambda c: None
        app.state.build_capacities_adapter = lambda c: None
        response = TestClient(app).get("/plan-inputs")
        assert response.status_code == 200
        body = response.json()
        assert not [
            w for w in body.get("source_warnings") or [] if "vault_enabled" in w
        ]
        assert body["source_counts"]["vault"] == 0
