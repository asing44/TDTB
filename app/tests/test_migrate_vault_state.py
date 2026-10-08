"""Gate for the one-shot vault -> app-home store migration (S1).

`tools/migrate_vault_state.py` is run EXPLICITLY by a human, once, and never at
startup. Its contract:

  - copy the five app-owned stores into ``<app home>/state/``;
  - leave every vault file byte-identical (the vault stays the rollback point);
  - never copy the 0-byte runtime lock artifacts;
  - never overwrite an existing destination.

Fixture-only: ``tmp_path`` is the vault, an explicit ``state_dir`` keeps the
operator's real ``~/.config/tdtb`` out of every assertion.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import app_config  # noqa: E402
import migrate_vault_state as mvs  # noqa: E402


DAY = date(2026, 10, 8)
CACHE_REL = Path("00 - META/Cache")

RUNSTATE_NOTE = "\n".join([
    "---",
    f"valid_date: '{DAY}'",
    "written_at: '2026-10-07T00:00:00Z'",
    "---",
    "",
    "```json",
    json.dumps({"anchor": "legacy-anchor"}),
    "```",
    "",
])

RECENT_SELECTIONS_NOTE = "\n".join([
    "---",
    "runs:",
    "  - date: '2026-10-07'",
    "    selections: []",
    "---",
    "",
])


def _store_files() -> dict[str, str]:
    """Legacy vault cache name -> exact bytes held by the file."""
    return {
        f"tdtb-runstate-{DAY}.md": RUNSTATE_NOTE,
        "tdtb-recent-selections.md": RECENT_SELECTIONS_NOTE,
        f"tdtb-digest-index-{DAY}.json": json.dumps(
            {"valid_date": str(DAY), "items": [{"name": "Legacy"}]}
        ),
        "tdtb-exclusion-settings.json": json.dumps(
            {"version": 1, "revision": 3, "exclusions": {"tags": []}}
        ),
        "tdtb-capacities-settings.json": json.dumps({"version": 1, "revision": 5}),
        "tdtb-capacities-source.json": json.dumps({"version": 1, "revision": 2}),
        "tdtb-deferrals.json": json.dumps({"version": 1, "items": {}}),
    }


def _lock_files() -> dict[str, str]:
    return {
        "tdtb-exclusion-settings.lock": "",
        "tdtb-capacities-settings.lock": "",
        "tdtb-capacities-source.lock": "",
    }


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _seed(vault: Path) -> dict[str, bytes]:
    cache = vault / CACHE_REL
    cache.mkdir(parents=True, exist_ok=True)
    for name, text in {**_store_files(), **_lock_files()}.items():
        (cache / name).write_text(text, encoding="utf-8")
    return _snapshot(vault)


def _expected_destinations(state: Path) -> dict[str, Path]:
    return {
        f"tdtb-runstate-{DAY}.md": state / "runstate" / f"tdtb-runstate-{DAY}.md",
        "tdtb-recent-selections.md": state / "runstate" / "tdtb-recent-selections.md",
        f"tdtb-digest-index-{DAY}.json": state / "runstate" / f"tdtb-digest-index-{DAY}.json",
        "tdtb-exclusion-settings.json": state / "exclusions.json",
        "tdtb-capacities-settings.json": state / "capacities-settings.json",
        "tdtb-capacities-source.json": state / "capacities-source.json",
        "tdtb-deferrals.json": state / "deferrals.json",
    }


def test_migrate_copies_every_store_byte_for_byte(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    before = _seed(vault)

    results = mvs.migrate(vault, state_dir=state)

    assert {r.status for r in results} == {"copied"}
    for name, dest in _expected_destinations(state).items():
        assert dest.is_file(), name
        assert dest.read_bytes() == (vault / CACHE_REL / name).read_bytes(), name


def test_migrate_leaves_every_vault_file_byte_identical(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    before = _seed(vault)

    mvs.migrate(vault, state_dir=state)

    assert _snapshot(vault) == before


def test_migrate_never_copies_lock_files(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    _seed(vault)

    mvs.migrate(vault, state_dir=state)

    assert list(state.rglob("*.lock")) == []


def test_migrate_refuses_to_overwrite_existing_state(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    _seed(vault)
    mvs.migrate(vault, state_dir=state)
    es_copy = state / "exclusions.json"
    es_copy.write_text("newer app-owned state", encoding="utf-8")
    before = _snapshot(vault)

    results = mvs.migrate(vault, state_dir=state)

    assert es_copy.read_text(encoding="utf-8") == "newer app-owned state"
    statuses = {r.name: r.status for r in results}
    assert statuses["tdtb-exclusion-settings.json"] == "kept"
    assert _snapshot(vault) == before


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    _seed(vault)

    first = mvs.migrate(vault, state_dir=state)
    after_first = _snapshot(state)
    second = mvs.migrate(vault, state_dir=state)

    assert {r.status for r in first} == {"copied"}
    assert all(r.status in {"kept", "missing"} for r in second)
    assert _snapshot(state) == after_first


def test_migrate_dry_run_writes_nothing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    before = _seed(vault)

    results = mvs.migrate(vault, state_dir=state, dry_run=True)

    assert {r.status for r in results} == {"would-copy"}
    assert not state.exists()
    assert _snapshot(vault) == before


def test_migrate_reports_a_missing_store_without_creating_it(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    _seed(vault)
    (vault / CACHE_REL / "tdtb-deferrals.json").unlink()

    results = mvs.migrate(vault, state_dir=state)

    statuses = {r.name: r.status for r in results}
    assert statuses["tdtb-deferrals.json"] == "missing"
    assert not (state / "deferrals.json").exists()
    # Every other store still migrated.
    assert (state / "exclusions.json").is_file()


def test_migrate_defaults_to_the_app_home_state_dir(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    _seed(vault)
    monkeypatch.setenv("TDTB_HOME", str(home))

    mvs.migrate(vault)

    assert (home / "state" / "exclusions.json").is_file()
    assert (home / "state" / "runstate" / f"tdtb-runstate-{DAY}.md").is_file()
    assert app_config.state_dir() == home / "state"


def test_cli_reports_the_migration(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    _seed(vault)

    code = mvs.main(["--vault-root", str(vault), "--state-dir", str(state)])

    out = capsys.readouterr().out
    assert code == 0
    assert "copied 7" in out
    assert str(state) in out
