"""Declared-property NAME fallback (property store + adapter + drawer).

The owner reworks assignment in Capacities per type, so a version-3 settings
declaration can name a property id that was renamed, deleted, or recreated.
The settings document stores no name, so this suite pins the machine-local
record that fills that gap:

* a declaration that resolves on the live contract remembers its display name
  and kind;
* a declaration whose id vanished follows that remembered NAME exactly once,
  with one actionable warning, and never rewrites the settings document;
* no name match, two same-name properties, or a retyped property keep today's
  unknown-property behaviour and say why;
* an ``assignedOn`` declaration recovers a recreated DATE property, and
  ``activeOrAssigned`` keeps its status half either way;
* the record persists across restarts, and a missing or corrupt file is
  tolerated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as ca  # noqa: E402
import capacities_assignment as cas  # noqa: E402
import capacities_builder as cb  # noqa: E402
import capacities_property_names as cpn  # noqa: E402
from tests.test_capacities_adapter import (  # noqa: E402
    OVERRIDE_STRUCTURE,
    SETTINGS_ASSIGNED_PROPERTY,
    TODAY,
    _active_settings,
    _assigned_on_mapping,
    _assigned_on_object,
    _named_definition,
    _override_adapter,
    _override_mapping,
    _override_object,
    _override_provider,
    _prop,
    _source_assigned_by_id,
)
from tests.test_capacities_active_or_assigned import (  # noqa: E402
    ASSIGNED_PROP,
    DAY,
    PARENT_ID,
    PROJECTS,
    SPACE,
    STATUS_PROP,
    _definition,
    _mappings as project_mappings,
    _prop as _project_prop,
    _settings as project_settings,
    _structures as project_structures,
)

BASE_URL = "https://api.capacities.example"
OLD_ID = "6cdb0e7b-62fd-492f-beef-bbf867a3f475"
NEW_ID = "0a3c8d51-9f4e-4b7a-8d61-2f9c5b7e4a10"
OLD_ON_ID = "11111111-2222-4333-8444-555555555555"
NEW_ON_ID = "99999999-8888-4777-8666-555555555555"
SINCE = "2026-10-01T09:00:00+00:00"


def _memory(path, *, now=None) -> cpn.PropertyNameMemory:
    return cpn.PropertyNameMemory(SPACE, BASE_URL, path=path, now=now)


def _seed(path, structure_id: str, property_id: str, name: str, kind: str) -> None:
    assert cpn.remember_property_names(
        SPACE,
        BASE_URL,
        {
            structure_id: {
                property_id: {
                    "name": name,
                    "kind": kind,
                    "since": SINCE,
                    "resolved": property_id,
                }
            }
        },
        path=path,
    )


def _structures_with(*definitions, drop=()):
    """Override structures whose custom type carries ``definitions`` minus ``drop``."""

    def project(row):
        rows = [
            definition
            for definition in row["propertyDefinitions"]
            if definition["id"] not in drop
        ]
        return {**row, "propertyDefinitions": [*rows, *definitions]}

    from tests.test_capacities_adapter import _override_structures

    return [
        project(row) if row["id"] == OVERRIDE_STRUCTURE else row
        for row in _override_structures()
    ]


class _Provider:
    def __init__(self, structures, objects):
        self._structures = structures
        self._objects = list(objects)

    def fetch_structures(self):
        return self._structures

    def list_objects(self, structure_id, cursor=None):
        if structure_id != OVERRIDE_STRUCTURE:
            return {"objects": [], "next_cursor": None}
        return {"objects": list(self._objects), "next_cursor": None}

    def get_object(self, object_id):  # pragma: no cover
        raise AssertionError("the row already carries its properties")


def _row(object_id="project-1", **properties):
    return _override_object(object_id, properties)


def _status_props(**extra) -> dict:
    """Open-status properties plus whatever else a row carries."""
    return {
        "state": _prop("label", "label", [{"id": "active", "name": "Active"}]),
        **extra,
    }


# ---------------------------------------------------------------------------
# The store itself
# ---------------------------------------------------------------------------


def test_records_round_trip_are_namespaced_and_survive_a_restart(tmp_path):
    path = tmp_path / "property-names.json"
    _seed(path, OVERRIDE_STRUCTURE, OLD_ID, "Assigned", "boolean")

    # A fresh read is a restart: the record is on disk, not in memory.
    assert cpn.read_property_names(SPACE, BASE_URL, path=path) == {
        OVERRIDE_STRUCTURE: {
            OLD_ID: {
                "name": "Assigned",
                "kind": "boolean",
                "since": SINCE,
                "resolved": OLD_ID,
            }
        }
    }
    # Another space or provider never sees it.
    assert cpn.read_property_names("other-space", BASE_URL, path=path) == {}
    assert cpn.read_property_names(SPACE, "https://other.example", path=path) == {}


def test_a_missing_or_corrupt_store_is_tolerated(tmp_path):
    missing = tmp_path / "absent.json"
    assert cpn.read_property_names(SPACE, BASE_URL, path=missing) == {}
    assert not missing.exists()

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json!!", encoding="utf-8")
    assert cpn.read_property_names(SPACE, BASE_URL, path=corrupt) == {}
    memory = _memory(corrupt)
    assert memory.known(OVERRIDE_STRUCTURE, OLD_ID) is None
    # A cold start may replace the corrupt file rather than fail.
    assert memory.flush() is True
    memory.record(
        OVERRIDE_STRUCTURE,
        OLD_ID,
        name="Assigned",
        kind="boolean",
        resolved=OLD_ID,
    )
    assert memory.flush() is True
    assert cpn.read_property_names(SPACE, BASE_URL, path=corrupt)[OVERRIDE_STRUCTURE][
        OLD_ID
    ]["name"] == "Assigned"


def test_since_moves_only_when_the_remembered_name_or_kind_changes(tmp_path):
    stamps = iter(["2026-10-01T00:00:00+00:00", "2026-10-02T00:00:00+00:00"])
    memory = _memory(tmp_path / "names.json", now=lambda: next(stamps))
    memory.record(
        OVERRIDE_STRUCTURE, OLD_ID, name="Assigned", kind="boolean", resolved=OLD_ID
    )
    memory.record(
        OVERRIDE_STRUCTURE, OLD_ID, name="Assigned", kind="boolean", resolved=OLD_ID
    )
    assert memory.snapshot()[OVERRIDE_STRUCTURE][OLD_ID]["since"] == (
        "2026-10-01T00:00:00+00:00"
    )
    memory.record(
        OVERRIDE_STRUCTURE, OLD_ID, name="Assign Me", kind="boolean", resolved=OLD_ID
    )
    assert memory.snapshot()[OVERRIDE_STRUCTURE][OLD_ID]["since"] == (
        "2026-10-02T00:00:00+00:00"
    )


# ---------------------------------------------------------------------------
# Adapter: recording and fallback
# ---------------------------------------------------------------------------


def test_a_live_declaration_remembers_its_name_and_kind(tmp_path):
    path = tmp_path / "names.json"
    provider = _override_provider(
        [
            _row(
                "project-1",
                **{
                    **_status_props(**{SETTINGS_ASSIGNED_PROPERTY: _prop("boolean", "boolean", True)}),
                },
            )
        ]
    )
    result = _override_adapter(
        provider,
        _override_mapping(),
        assignment_settings=_active_settings(),
        property_names=_memory(path),
    ).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["project-1"]
    assert result.warnings == []
    records = cpn.read_property_names(SPACE, BASE_URL, path=path)
    assert records[OVERRIDE_STRUCTURE][SETTINGS_ASSIGNED_PROPERTY] == {
        "name": "Settings-Assigned",
        "kind": "boolean",
        "since": records[OVERRIDE_STRUCTURE][SETTINGS_ASSIGNED_PROPERTY]["since"],
        "resolved": SETTINGS_ASSIGNED_PROPERTY,
    }


def test_a_dead_declaration_admits_through_the_remembered_name(tmp_path):
    path = tmp_path / "names.json"
    _seed(path, OVERRIDE_STRUCTURE, OLD_ID, "Assigned", "boolean")
    provider = _Provider(
        _structures_with(_named_definition(NEW_ID, "boolean", "Assigned")),
        [
            _row(
                "project-1",
                **_status_props(**{NEW_ID: _prop("boolean", "boolean", True)}),
            )
        ],
    )
    result = _override_adapter(
        provider,
        _override_mapping(assigned_property=OLD_ID),
        assignment_settings=_active_settings(),
        property_names=_memory(path),
    ).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["project-1"]
    assert result.items[0]["capacities_assignment"]["source_assigned"] is True
    fallback_warnings = [line for line in result.warnings if "is gone" in line]
    assert len(fallback_warnings) == 1
    warning = fallback_warnings[0]
    assert "Project" in warning or OVERRIDE_STRUCTURE in warning
    assert OLD_ID in warning
    assert "using 'Assigned'" in warning
    assert NEW_ID in warning
    assert "re-pick it in Settings" in warning
    # The resolution is remembered, so the drawer can project it and a later
    # read keeps following the same name.
    records = cpn.read_property_names(SPACE, BASE_URL, path=path)
    assert records[OVERRIDE_STRUCTURE][OLD_ID] == {
        "name": "Assigned",
        "kind": "boolean",
        "since": SINCE,
        "resolved": NEW_ID,
    }


def test_a_renamed_property_keeps_the_unknown_path_and_says_why(tmp_path):
    path = tmp_path / "names.json"
    _seed(path, OVERRIDE_STRUCTURE, OLD_ID, "Assigned", "boolean")
    provider = _Provider(
        _structures_with(_named_definition(NEW_ID, "boolean", "Assignment")),
        [
            _row(
                "project-1",
                **{**_status_props(), NEW_ID: _prop("boolean", "boolean", True)},
            )
        ],
    )
    result = _override_adapter(
        provider,
        _override_mapping(assigned_property=OLD_ID),
        assignment_settings=_active_settings(),
        property_names=_memory(path),
    ).items_for_day(TODAY)

    assert _source_assigned_by_id(result) == {"project-1": None}
    assert any(
        "unknown property" in line and OLD_ID in line for line in result.warnings
    )
    reasons = [line for line in result.warnings if "remembered-name fallback" in line]
    assert len(reasons) == 1
    assert "'Assigned'" in reasons[0]
    assert "no property is named" in reasons[0]


def test_two_properties_with_the_remembered_name_do_not_fall_back(tmp_path):
    path = tmp_path / "names.json"
    _seed(path, OVERRIDE_STRUCTURE, OLD_ID, "Assigned", "boolean")
    provider = _Provider(
        _structures_with(
            _named_definition(NEW_ID, "boolean", "Assigned"),
            _named_definition("second-assigned", "boolean", "Assigned"),
        ),
        [
            _row(
                "project-1",
                **{**_status_props(), NEW_ID: _prop("boolean", "boolean", True)},
            )
        ],
    )
    result = _override_adapter(
        provider,
        _override_mapping(assigned_property=OLD_ID),
        assignment_settings=_active_settings(),
        property_names=_memory(path),
    ).items_for_day(TODAY)

    assert _source_assigned_by_id(result) == {"project-1": None}
    reasons = [line for line in result.warnings if "remembered-name fallback" in line]
    assert len(reasons) == 1
    assert "2 properties are named 'Assigned'" in reasons[0]


def test_a_date_property_with_the_name_never_satisfies_an_assigned_mode(tmp_path):
    path = tmp_path / "names.json"
    _seed(path, OVERRIDE_STRUCTURE, OLD_ID, "Assigned", "boolean")
    provider = _Provider(
        _structures_with(_named_definition(NEW_ID, "date", "Assigned")),
        [
            _row(
                "project-1",
                **_status_props(
                    **{
                        NEW_ID: _prop(
                            "date",
                            "date",
                            {
                                "dateResolution": "day",
                                "start": "2026-09-29T00:00:00.000Z",
                            },
                        )
                    }
                ),
            )
        ],
    )
    result = _override_adapter(
        provider,
        _override_mapping(assigned_property=OLD_ID),
        assignment_settings=_active_settings(),
        property_names=_memory(path),
    ).items_for_day(TODAY)

    assert _source_assigned_by_id(result) == {"project-1": None}
    reasons = [line for line in result.warnings if "remembered-name fallback" in line]
    assert len(reasons) == 1
    assert "is a date, not a boolean" in reasons[0]


def test_assigned_on_recovers_a_recreated_date_property(tmp_path):
    path = tmp_path / "names.json"
    _seed(path, OVERRIDE_STRUCTURE, OLD_ON_ID, "Assigned On", "date")
    provider = _Provider(
        _structures_with(_named_definition(NEW_ON_ID, "date", "Assigned On")),
        [
            _assigned_on_object(
                "project-1",
                {
                    NEW_ON_ID: _prop(
                        "date",
                        "date",
                        {"dateResolution": "day", "start": "2026-09-29T00:00:00.000Z"},
                    )
                },
            )
        ],
    )
    result = _override_adapter(
        provider,
        _assigned_on_mapping(assigned_on_property=OLD_ON_ID),
        property_names=_memory(path),
    ).items_for_day(TODAY)

    assert _source_assigned_by_id(result) == {"project-1": True}
    fallback_warnings = [line for line in result.warnings if "Assigned On property" in line]
    assert len(fallback_warnings) == 1
    assert OLD_ON_ID in fallback_warnings[0]
    assert NEW_ON_ID in fallback_warnings[0]


def test_a_live_declaration_is_untouched_by_the_store(tmp_path):
    provider = _override_provider(
        [
            _row(
                "project-1",
                **_status_props(**{SETTINGS_ASSIGNED_PROPERTY: _prop("boolean", "boolean", True)}),
            )
        ]
    )
    result = _override_adapter(
        provider,
        _override_mapping(),
        assignment_settings=_active_settings(),
        property_names=_memory(tmp_path / "names.json"),
    ).items_for_day(TODAY)

    assert [row["capacities_id"] for row in result.items] == ["project-1"]
    assert result.warnings == []


# ---------------------------------------------------------------------------
# End to end: the Garage Buildout case through the builder's declaration
# resolution and the adapter, with the real Project structures.
# ---------------------------------------------------------------------------


class _ProjectProvider:
    """Project/RootTask structures with the Assigned property RECREATED."""

    def __init__(self, new_id: str, objects):
        self._new_id = new_id
        self._objects = list(objects)

    def fetch_structures(self):
        structures = project_structures()
        for row in structures:
            if row["id"] != PROJECTS:
                continue
            row["propertyDefinitions"] = [
                (
                    _definition(self._new_id, "boolean", name="Assigned")
                    if definition["id"] == ASSIGNED_PROP
                    else definition
                )
                for definition in row["propertyDefinitions"]
            ]
        return structures

    def list_objects(self, structure_id, cursor=None):
        if structure_id != PROJECTS:
            return {"objects": [], "next_cursor": None}
        return {"objects": list(self._objects), "next_cursor": None}

    def get_object(self, object_id):  # pragma: no cover
        raise AssertionError("rows carry their properties")


def _project_row(object_id, title, *, assigned, status) -> dict:
    label_id, label_name = status
    return {
        "id": object_id,
        "spaceId": SPACE,
        "structureId": PROJECTS,
        "properties": {
            "title": _project_prop("title", "title", {"value": title}),
            NEW_ID: _project_prop("boolean", "boolean", assigned),
            STATUS_PROP: _project_prop(
                "label", "label", [{"id": label_id, "name": label_name}]
            ),
        },
    }


def _garage_buildout() -> dict:
    return _project_row(
        PARENT_ID,
        "Garage Buildout",
        assigned=True,
        status=("background", "Background"),
    )



def test_garage_buildout_is_still_admitted_after_the_property_is_recreated(tmp_path):
    path = tmp_path / "names.json"
    _seed(path, PROJECTS, ASSIGNED_PROP, "Assigned", "boolean")
    settings = project_settings()
    mappings = cb._resolve_assigned_structures(
        project_mappings(assigned_property=None), settings
    )
    # The declaration is the version-3 settings property id, still the old one.
    assert next(
        mapping for mapping in mappings if mapping.structure_id == PROJECTS
    ).assigned_property == ASSIGNED_PROP

    result = ca.CapacitiesAdapter(
        _ProjectProvider(NEW_ID, [_garage_buildout()]),
        ca.CapacitiesConfig(
            space_id=SPACE,
            mappings=mappings,
            assignment_settings=cas.AssignmentSettings.from_v2(settings),
            settings=settings,
            property_names=_memory(path),
        ),
    ).items_for_day(DAY)

    assert [row["name"] for row in result.items] == ["Garage Buildout"]
    assert any("is gone" in warning and "Project" in warning for warning in result.warnings)
    assert cpn.read_property_names(SPACE, BASE_URL, path=path)[PROJECTS][ASSIGNED_PROP][
        "resolved"
    ] == NEW_ID


def test_active_or_assigned_keeps_its_status_half_through_the_fallback(tmp_path):
    # The union mode admits on the open status OR the declared checkbox. The
    # checkbox half follows the remembered name; the status half is untouched,
    # so a type still admits by status while the owner re-picks the property.
    path = tmp_path / "names.json"
    _seed(path, PROJECTS, ASSIGNED_PROP, "Assigned", "boolean")
    settings = project_settings()
    mappings = cb._resolve_assigned_structures(
        project_mappings(assigned_property=None), settings
    )
    result = ca.CapacitiesAdapter(
        _ProjectProvider(
            NEW_ID,
            [
                _project_row(
                    "by-status",
                    "Garden Prep",
                    assigned=False,
                    status=("in-progress", "In Progress"),
                ),
                _project_row(
                    "by-checkbox",
                    "Garage Buildout",
                    assigned=True,
                    status=("background", "Background"),
                ),
            ],
        ),
        ca.CapacitiesConfig(
            space_id=SPACE,
            mappings=mappings,
            assignment_settings=cas.AssignmentSettings.from_v2(settings),
            settings=settings,
            property_names=_memory(path),
        ),
    ).items_for_day(DAY)

    # Both halves admit: one row by status, one by the recovered checkbox.
    assert sorted(row["name"] for row in result.items) == [
        "Garage Buildout",
        "Garden Prep",
    ]
    assert any("is gone" in line for line in result.warnings)


# ---------------------------------------------------------------------------
# Drawer projection
# ---------------------------------------------------------------------------


def _write_source_record():
    path = cb.source_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "revision": 0,
                "space_id": SPACE,
                "structures": [{"structure_id": PROJECTS}],
            }
        ),
        encoding="utf-8",
    )


def test_declaration_diagnostics_reports_the_name_fallback(monkeypatch):
    import main as main_mod

    _write_source_record()
    _seed(cpn.default_path(), PROJECTS, ASSIGNED_PROP, "Assigned", "boolean")
    settings = project_settings()
    monkeypatch.setattr(
        main_mod,
        "_capacities_published_contract_schema",
        lambda: {PROJECTS: {NEW_ID: "boolean"}},
    )
    cpn.remember_property_names(
        SPACE,
        cb.CAPACITIES_BASE_URL,
        {
            PROJECTS: {
                ASSIGNED_PROP: {
                    "name": "Assigned",
                    "kind": "boolean",
                    "since": SINCE,
                    "resolved": NEW_ID,
                }
            }
        },
    )

    diagnostics = main_mod._capacities_declaration_diagnostics(settings, {})

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic["declared_property"] == ASSIGNED_PROP
    assert diagnostic["resolved_property"] == NEW_ID
    assert diagnostic["property_name"] == "Assigned"
    assert diagnostic["since"] == SINCE
    assert NEW_ID in diagnostic["reason"]
    assert "make this permanent" in diagnostic["reason"]


def test_declaration_diagnostics_says_why_no_fallback_applied(monkeypatch):
    import main as main_mod

    _write_source_record()
    settings = project_settings()
    monkeypatch.setattr(
        main_mod,
        "_capacities_published_contract_schema",
        lambda: {PROJECTS: {NEW_ID: "date"}},
    )
    cpn.remember_property_names(
        SPACE,
        cb.CAPACITIES_BASE_URL,
        {
            PROJECTS: {
                ASSIGNED_PROP: {
                    "name": "Assigned",
                    "kind": "boolean",
                    "since": SINCE,
                    "resolved": ASSIGNED_PROP,
                }
            }
        },
    )

    diagnostics = main_mod._capacities_declaration_diagnostics(settings, {})

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert "declared_property" not in diagnostic
    assert "did not resolve to a single live property" in diagnostic["reason"]
    assert ASSIGNED_PROP in diagnostic["reason"]


# ---------------------------------------------------------------------------
# Store bounds, read-only replay, and wiring into the live paths
# ---------------------------------------------------------------------------

def _stamps():
    counter = iter(range(10_000))
    return lambda: f"2026-11-17T00:00:{next(counter):04d}"


def test_the_store_keeps_only_the_newest_records_per_structure(tmp_path):
    path = tmp_path / "names.json"
    memory = cpn.PropertyNameMemory("space", BASE_URL, path=path, now=_stamps())
    total = cpn.MAX_RECORDS_PER_STRUCTURE + 5
    for n in range(total):
        memory.record("s", f"p{n}", name=f"Assigned {n}", kind="boolean", resolved=f"p{n}")
    assert memory.flush()
    kept = cpn.read_property_names("space", BASE_URL, path=path)["s"]
    assert len(kept) == cpn.MAX_RECORDS_PER_STRUCTURE
    assert "p0" not in kept and f"p{total - 1}" in kept


def test_a_read_only_memory_answers_but_never_writes(tmp_path):
    path = tmp_path / "names.json"
    writer = cpn.PropertyNameMemory("space", BASE_URL, path=path)
    writer.record("s", "old", name="Assigned", kind="boolean", resolved="old")
    assert writer.flush()
    before = path.read_bytes()

    reader = cpn.PropertyNameMemory("space", BASE_URL, path=path, writable=False)
    assert reader.known("s", "old") == ("Assigned", "boolean")
    reader.record("s", "new", name="Assigned", kind="boolean", resolved="new")
    assert reader.flush()
    assert path.read_bytes() == before

    missing = tmp_path / "absent.json"
    cpn.PropertyNameMemory("space", BASE_URL, path=missing, writable=False).flush()
    assert not missing.exists()


def test_the_offline_plan_replay_uses_a_read_only_memory():
    import capacities_intake

    source = Path(capacities_intake.__file__).read_text()
    assert "property_names=capacities_property_names.PropertyNameMemory(" in source
    assert "writable=False" in source


def test_refresh_records_names_from_the_live_contract():
    import capacities_refresh

    source = Path(capacities_refresh.__file__).read_text()
    block = source[source.index("self.adapter = capacities_adapter.CapacitiesAdapter("):]
    block = block[: block.index("# Tag titles the catalog read resolves")]
    assert "property_names=capacities_property_names.PropertyNameMemory(" in block
    assert "writable=False" not in block
