"""Tests for ``tools/plan_diff.py`` - the two-state-snapshot diff and the
snapshot command that feeds it.

Three things are pinned here:

* ``snapshot`` copies exactly the parts the offline projection needs
  (``state/`` incl. the refresh caches/contracts/``property-names.json``,
  ``config.json``, ``producer-rules.json``, ``tdtb-capacities-*.json``), never
  the locks, ``env``/``.env*``, ``capacities-mcp/``, ``backups/``, or older
  snapshots, and never writes the source home;
* data mode (``--base-state``/``--head-state`` under ONE ``--ref``) reports a
  row admitted only in head and an assignment change, over two synthetic state
  dirs published through the production refresh seams;
* ref mode (two git refs over one shared state copy) still works unchanged.

The synthetic states are published with the real store/projection path
(``capacities_builder.save_source`` + ``capacities_refresh_state`` install +
the tool's own snapshot copies), so the projections come from the same offline
path ``/plan-inputs`` uses. Everything is local: no network, no credential, no
provider, and no read of the operator's real ``~/.config/tdtb``.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import capacities_builder as cb  # noqa: E402
import capacities_refresh_state as crs  # noqa: E402
import plan_diff as pd  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SPACE = "space-1"
#: The adapter requires the canonical native structure to be mapped explicitly.
ROOT = "RootTask"
CUSTOM = "T2"
SCOPE = "all"
NOON_TS = datetime(2026, 10, 10, 12, 0).timestamp()
#: A fixed logical day, so no test depends on when it runs.
DAY = "2026-10-10"


# ---------------------------------------------------------------------------
# Home builders
# ---------------------------------------------------------------------------


def _write(path: Path, text: str = "{}") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _home_with_mixed_files(root: Path) -> Path:
    """A home carrying both the parts a snapshot must take and the parts it
    must never take (locks, env files, credentials, backups, old snapshots)."""
    home = root / "home"
    _write(home / "config.json", '{"version": 1}')
    _write(home / "producer-rules.json", "{}")
    _write(home / "tdtb-capacities-content-cache.json", "{}")
    _write(home / "tdtb-capacities-structure-titles.json", "{}")
    # Excluded: locks, env files, backups, credentials, older snapshots,
    # and the rotated pre-change copies of the root files.
    _write(home / "tdtb-capacities-content-cache.json.lock", "")
    _write(home / "config.json.pre-old", "{}")
    _write(home / "producer-rules.json.bak-20261001", "{}")
    _write(home / "env", "SOME_TOKEN=not-a-real-secret")
    _write(home / ".env", "SOME_TOKEN=not-a-real-secret")
    _write(home / "backups" / "pre-restart" / "config.json", "{}")
    _write(home / "capacities-mcp" / "oauth.json", "{}")
    _write(home / "snapshots" / "plan-diff-old" / "manifest.json", "{}")
    # Kept: the whole state tree, minus its own locks and env files.
    _write(home / "state" / "capacities-source.json", "{}")
    _write(home / "state" / "capacities-settings.json", "{}")
    _write(home / "state" / "capacities-mirror.json", "{}")
    _write(home / "state" / "capacities-settings.lock", "")
    _write(home / "state" / "env", "SOME_TOKEN=not-a-real-secret")
    _write(home / "state" / "capacities-refresh" / "property-names.json", "{}")
    _write(home / "state" / "capacities-refresh" / "property-names.json.lock", "")
    _write(home / "state" / "capacities-refresh" / "schema-change.json", "{}")
    _write(home / "state" / "capacities-refresh" / "contracts" / "abc.json", "{}")
    _write(home / "state" / "capacities-refresh" / "objects" / "abc.json", "{}")
    _write(home / "state" / "capacities-refresh" / "snapshots" / "abc.json", "{}")
    return home


def _tree(root: Path) -> dict[str, tuple[int, int]]:
    """``relative path -> (size, mtime_ns)`` for every file under ``root``."""
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ---------------------------------------------------------------------------
# The published Capacities generation (mirrors test_capacities_intake.py)
# ---------------------------------------------------------------------------


def _definition(prop_id: str, kind: str, labels=None) -> dict[str, Any]:
    row: dict[str, Any] = {"id": prop_id, "type": kind}
    if labels is not None:
        row["labelSet"] = [{"id": i, "name": n} for i, n in labels]
    return row


def _contract(types: tuple[str, ...] = (ROOT, CUSTOM)) -> list[dict[str, Any]]:
    properties = [
        _definition("title", "title"),
        _definition("assigned", "boolean"),
        _definition("status", "label", [("active", "Active"), ("done", "Done")]),
    ]
    return [
        {
            "id": type_id,
            "title": type_id,
            "propertyDefinitions": list(properties),
        }
        for type_id in types
    ]


def _save_record(types: tuple[str, ...] = (ROOT,)) -> None:
    structures = [
        cb.SourceStructureRecord(
            structure_id=type_id,
            title_property="title",
            status_property="status",
            open_status_values=("active",),
            assignment_property="assigned",
            assignment_values=("true",),
        )
        for type_id in types
    ]
    cb.save_source(expected_revision=0, space_id=SPACE, structures=structures)


def _object(object_id: str, type_id: str, *, assigned: bool) -> dict[str, Any]:
    return {
        "id": object_id,
        "structureId": type_id,
        "properties": {
            "title": {"type": "title", "title": {"value": object_id}},
            "assigned": {"type": "boolean", "boolean": assigned},
            "status": {"type": "label", "label": [{"id": "active", "name": "Active"}]},
        },
    }


def _publish(
    objects: list[dict[str, Any]],
    *,
    structures: Any,
    expected_generation: int = 0,
) -> crs.CompleteSnapshot:
    """Install one complete generation through the production store seam."""
    store = cb.build_refresh_state(SPACE)
    listed: dict[str, list[str]] = {}
    for obj in objects:
        store.put(obj["id"], obj["structureId"], obj)
        listed.setdefault(obj["structureId"], []).append(obj["id"])
    evidence = crs.SnapshotEvidence(
        scope_key=SCOPE,
        revision=1,
        required_types=tuple(listed),
        listings=tuple(
            crs.TypeListing(
                type_key=type_id,
                object_ids=tuple(ids),
                listing_checked_at=NOON_TS,
            )
            for type_id, ids in listed.items()
        ),
    )
    return store.install_generation(
        evidence,
        expected_generation=expected_generation,
        structures=structures,
    )


@pytest.fixture
def synthetic_home(tmp_path, monkeypatch):
    """A live-style TDTB home holding one published Capacities generation."""
    home = tmp_path / "live-home"
    home.mkdir()
    monkeypatch.setenv("TDTB_HOME", str(home))
    _save_record()
    snap = _publish(
        [_object("kept-task", ROOT, assigned=True), _object("flip-task", ROOT, assigned=True)],
        structures=_contract(),
    )
    return home, snap.generation


# ---------------------------------------------------------------------------
# snapshot: what it takes, what it leaves, what it writes
# ---------------------------------------------------------------------------


def test_snapshot_copies_the_plan_diff_parts_and_nothing_else(tmp_path):
    home = _home_with_mixed_files(tmp_path)
    out = tmp_path / "snap"

    manifest = pd.snapshot(home, out, day=DAY, repo=None)

    for kept in (
        "config.json",
        "producer-rules.json",
        "tdtb-capacities-content-cache.json",
        "tdtb-capacities-structure-titles.json",
        "state/capacities-source.json",
        "state/capacities-settings.json",
        "state/capacities-refresh/property-names.json",
        "state/capacities-refresh/schema-change.json",
        "state/capacities-refresh/contracts/abc.json",
        "state/capacities-refresh/objects/abc.json",
        "state/capacities-refresh/snapshots/abc.json",
        "manifest.json",
    ):
        assert (out / kept).is_file(), kept
    for gone in (
        "env",
        ".env",
        "config.json.pre-old",
        "producer-rules.json.bak-20261001",
        "backups",
        "capacities-mcp",
        "snapshots",
        "state/env",
        "state/capacities-settings.lock",
        "state/capacities-refresh/property-names.json.lock",
        "tdtb-capacities-content-cache.json.lock",
    ):
        assert not (out / gone).exists(), gone
    assert not list(out.rglob("*.lock"))
    assert manifest["entries"] == [
        "config.json",
        "producer-rules.json",
        "tdtb-capacities-content-cache.json",
        "tdtb-capacities-structure-titles.json",
        "state/",
    ]


def test_snapshot_never_writes_the_source_home(tmp_path):
    home = _home_with_mixed_files(tmp_path)
    before = _tree(home)

    pd.snapshot(home, tmp_path / "snap", day=DAY, repo=None)

    assert _tree(home) == before


def test_snapshot_manifest_records_time_source_home_git_head_and_day(tmp_path):
    home = _home_with_mixed_files(tmp_path)
    out = tmp_path / "snap"

    written = pd.snapshot(
        home, out, day=DAY, repo=None, now=datetime(2026, 10, 10, 9, 30, 15)
    )

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == written
    assert manifest["kind"] == "plan-diff-snapshot"
    assert manifest["source_home"] == str(home.resolve())
    assert manifest["git_head"] is None
    assert manifest["logical_day"] == DAY
    assert manifest["day_pinned"] is True
    assert manifest["created_at"].startswith("2026-10-10T09:30:15")
    assert manifest["file_count"] >= 9


def test_snapshot_defaults_to_a_timestamped_dir_under_the_home(tmp_path):
    home = _home_with_mixed_files(tmp_path)

    manifest = pd.snapshot(
        home, None, day=DAY, repo=None, now=datetime(2026, 10, 10, 9, 30, 15)
    )

    assert manifest["path"] == str(
        home.resolve() / "snapshots" / "plan-diff-20261010-093015"
    )
    # The new snapshot lives under the home but is never copied into itself.
    assert (home / "snapshots" / "plan-diff-20261010-093015" / "manifest.json").is_file()


def test_snapshot_refuses_to_clobber_or_to_overwrite_its_own_source(tmp_path):
    home = _home_with_mixed_files(tmp_path)
    out = tmp_path / "snap"
    pd.snapshot(home, out, day=DAY, repo=None)

    with pytest.raises(FileExistsError):
        pd.snapshot(home, out, day=DAY, repo=None)
    with pytest.raises(ValueError):
        pd.snapshot(home, home, day=DAY, repo=None)
    with pytest.raises(FileNotFoundError):
        pd.snapshot(tmp_path / "no-state-here", None, day=DAY, repo=None)


def test_snapshot_cli_writes_a_manifest_and_pins_the_day(tmp_path):
    home = _home_with_mixed_files(tmp_path)
    out = tmp_path / "snap"

    code = pd.main(
        ["snapshot", "--home", str(home), "--out", str(out), "--day", DAY,
         "--repo", str(REPO_ROOT)]
    )

    assert code == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["logical_day"] == DAY
    # A real repo at HEAD records the commit the snapshot was taken under.
    assert manifest["git_head"] and len(manifest["git_head"]) == 40
    # A second run into the same directory is a setup failure, not a clobber.
    assert (
        pd.main(
            ["snapshot", "--home", str(home), "--out", str(out), "--day", DAY,
             "--repo", str(REPO_ROOT)]
        )
        == 2
    )


# ---------------------------------------------------------------------------
# Pure diff/rendering units (no child processes)
# ---------------------------------------------------------------------------


def _row(object_id: str, *, assigned: bool) -> dict[str, Any]:
    return {
        "identity": f"capacities:{SPACE}:{ROOT}:{object_id}",
        "capacities_id": object_id,
        "capacities_structure_id": ROOT,
        "name": object_id,
        "assigned": assigned,
        "duration_minutes": 30,
    }


def test_assignment_change_is_a_changed_row_not_a_disappearance():
    base = {"day": DAY, "ref": "a", "sha": "a" * 40, "assigned": [_row("t", assigned=True)], "pool": []}
    head = {"day": DAY, "ref": "b", "sha": "b" * 40, "assigned": [], "pool": [_row("t", assigned=False)]}

    report = pd.diff(base, head)

    assert report["totals"]["only_in_base"] == 0
    assert report["totals"]["only_in_head"] == 0
    assert report["totals"]["changed"] == 1
    assert [change["field"] for change in report["changed"][0]["fields"]] == ["assigned"]
    assert report["mode"] == pd.MODE_REF


def test_report_header_states_mode_both_state_sources_and_the_code_ref():
    base = {"day": DAY, "ref": "base-ref", "sha": "a" * 40, "assigned": [_row("t", assigned=True)], "pool": []}
    head = {"day": DAY, "ref": "head-ref", "sha": "b" * 40, "assigned": [_row("t", assigned=False)], "pool": []}
    sources = {
        "base": {"kind": "state", "path": "/snap/base"},
        "head": {"kind": "state", "path": "/snap/head"},
    }

    data_md = pd.render_markdown(
        pd.diff(
            base, head, mode=pd.MODE_DATA, sources=sources,
            code={"ref": "HEAD", "sha": "c" * 40},
        ),
        day=DAY,
    )
    ref_md = pd.render_markdown(
        pd.diff(
            base, head, sources={"base": {"path": "/home/tdtb", "shared": True}},
            code={"ref": "a..b", "sha": "b" * 40},
        ),
        day=DAY,
    )

    assert "Mode **data**" in data_md
    assert "`HEAD`" in data_md
    assert "/snap/base" in data_md and "/snap/head" in data_md
    assert "Mode **ref**" in ref_md
    assert "/home/tdtb" in ref_md
    assert "Row differences caused by code" not in ref_md


# ---------------------------------------------------------------------------
# Data mode over two synthetic state dirs (child processes, real projection)
# ---------------------------------------------------------------------------


def _data_report(tmp_path, base_state: Path, head_state: Path) -> tuple[int, dict, str]:
    json_out = tmp_path / "diff.json"
    md_out = tmp_path / "diff.md"
    code = pd.main(
        [
            "--base-state", str(base_state),
            "--head-state", str(head_state),
            "--ref", "worktree",
            "--day", DAY,
            "--json-out", str(json_out),
            "--md-out", str(md_out),
        ]
    )
    return (
        code,
        json.loads(json_out.read_text(encoding="utf-8")) if json_out.exists() else {},
        md_out.read_text(encoding="utf-8") if md_out.exists() else "",
    )


def test_data_mode_detects_admitted_dropped_and_changed_rows(
    tmp_path, synthetic_home
):
    home, generation = synthetic_home
    base_state = tmp_path / "baseline"
    head_state = tmp_path / "current"
    pd.snapshot(home, base_state, day=DAY, repo=None)

    # The owner's rework: one row is unassigned (dropped with a reason), one
    # is newly admitted, and one keeps its identity but changes its title.
    renamed = _object("kept-task", ROOT, assigned=True)
    renamed["properties"]["title"]["title"]["value"] = "kept-task renamed"
    _publish(
        [
            renamed,
            _object("flip-task", ROOT, assigned=False),
            _object("added-task", ROOT, assigned=True),
        ],
        structures=_contract(),
        expected_generation=generation,
    )
    pd.snapshot(home, head_state, day=DAY, repo=None)

    code, report, markdown = _data_report(tmp_path, base_state, head_state)

    assert code == 0
    assert report["mode"] == pd.MODE_DATA
    assert report["day"] == DAY
    assert report["code"]["ref"] == "worktree"
    assert report["sources"]["base"]["path"] == str(base_state.resolve())
    assert report["sources"]["head"]["path"] == str(head_state.resolve())
    only_head = {row["capacities_id"] for row in report["only_in_head"]}
    assert only_head == {"added-task"}
    only_base = {row["capacities_id"] for row in report["only_in_base"]}
    assert only_base == {"flip-task"}
    changed = {
        entry["row"]["capacities_id"]: {
            change["field"]: (change["base"], change["head"])
            for change in entry["fields"]
        }
        for entry in report["changed"]
    }
    assert set(changed) == {"kept-task"}
    assert ("kept-task", "kept-task renamed") in changed["kept-task"].values()
    # The unassigned row left with a recorded drop reason, not silently.
    assert report["totals"]["unexplained"] == 0
    assert "Mode **data**" in markdown
    assert str(base_state.resolve()) in markdown
    assert str(head_state.resolve()) in markdown


def test_data_mode_against_itself_reports_no_change(tmp_path, synthetic_home):
    home, _generation = synthetic_home
    state = tmp_path / "only"
    pd.snapshot(home, state, day=DAY, repo=None)

    code, report, _markdown = _data_report(tmp_path, state, state)

    assert code == 0
    assert report["totals"] == {
        "base_rows": report["totals"]["base_rows"],
        "head_rows": report["totals"]["base_rows"],
        "only_in_base": 0,
        "only_in_head": 0,
        "changed": 0,
        "hidden_children": 0,
        "unexplained": 0,
    }
    assert report["totals"]["base_rows"] >= 2


def test_data_mode_rejects_half_a_pair_and_ref_mode_flags(tmp_path, synthetic_home):
    home, _generation = synthetic_home
    state = tmp_path / "only"
    pd.snapshot(home, state, day=DAY, repo=None)

    assert pd.main(["--base-state", str(state), "--day", DAY]) == 2
    assert (
        pd.main(
            ["--base-state", str(state), "--head-state", str(state), "--base", "HEAD"]
        )
        == 2
    )
    assert (
        pd.main(
            ["--base-state", str(tmp_path / "absent"), "--head-state", str(state)]
        )
        == 2
    )


# ---------------------------------------------------------------------------
# Ref mode is unchanged: two refs, one shared state copy
# ---------------------------------------------------------------------------


def test_ref_mode_still_diffs_two_refs_over_one_shared_state(tmp_path, synthetic_home):
    home, _generation = synthetic_home
    json_out = tmp_path / "ref.json"

    code = pd.main(
        ["--base", "HEAD", "--head", "HEAD", "--day", DAY,
         "--state-dir", str(home), "--json-out", str(json_out)]
    )

    assert code == 0
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["mode"] == pd.MODE_REF
    assert report["day"] == DAY
    assert report["sources"]["base"] == {
        "kind": "state",
        "path": str(home.resolve()),
        "shared": True,
    }
    assert report["base"]["sha"] == report["head"]["sha"]
    # Identical code over one shared copy can only produce an identical plan.
    assert report["totals"]["only_in_base"] == 0
    assert report["totals"]["only_in_head"] == 0
    assert report["totals"]["changed"] == 0
    assert report["totals"]["base_rows"] >= 2


def test_ref_mode_defaults_are_unchanged():
    args = pd.build_parser().parse_args([])

    assert args.command == "diff"
    assert (args.base, args.head) == (pd.DEFAULT_BASE, pd.DEFAULT_HEAD)
    assert (args.base_state, args.head_state) == (None, None)
    assert (args.state_dir, args.fail_on_unexplained, args.keep) == (None, False, False)
    assert date.fromisoformat(DAY) == date(2026, 10, 10)
