"""Contract tests for the pure Capacities schema fingerprint/diff module.

The payload shape under test is the one ``CapacitiesAdapter._ensure_contract``
reads: a ``{"structures": [...]}`` document (a bare list is tolerated) whose
rows carry ``id`` (or ``structureId``), ``title`` and ``propertyDefinitions``
either as a list of definitions or as a mapping keyed by property id. Each
definition carries ``id``, ``name``, ``type``, ``writable`` and, for label
properties, ``labelSet`` entries of ``{"id", "name"}``.

Everything here is content-free: sentinel names and values are used so a test
fails the moment a name, title, or value leaks into a fingerprint or a
structured diff.
"""
from __future__ import annotations

import ast
import builtins
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_schema as cs  # noqa: E402


PROP_SENTINEL = "SENTINEL_PROP_NAME"
OPTION_SENTINEL = "SENTINEL_OPTION_NAME"
TITLE_SENTINEL = "SENTINEL_STRUCTURE_TITLE"


# ---------------------------------------------------------------------------
# Fixtures / payload builders
# ---------------------------------------------------------------------------


def _definition(prop_id, kind, *, name=None, writable=True, labels=None):
    row = {
        "id": prop_id,
        "name": name if name is not None else prop_id.title(),
        "type": kind,
        "writable": writable,
    }
    if labels is not None:
        row["labelSet"] = [{"id": oid, "name": oname} for oid, oname in labels]
    return row


def _structure(structure_id, definitions, *, title=None):
    return {
        "id": structure_id,
        "title": title if title is not None else structure_id,
        "propertyDefinitions": list(definitions),
    }


def _task_structure():
    return _structure(
        "RootTask",
        [
            _definition("title", "title"),
            _definition("due", "date"),
            _definition("status", "label", labels=[("open", "Open"), ("done", "Done")]),
        ],
    )


def _project_structure():
    return _structure(
        "custom-project",
        [
            _definition("title", "title"),
            _definition("state", "label", labels=[("active", "Active"), ("done", "Done")]),
        ],
    )


def _payload(*structures, extra=None):
    document = {"structures": list(structures)}
    if extra:
        document.update(extra)
    return document


def _fresh():
    """The baseline payload both fingerprints and diffs are compared against."""
    return _payload(_task_structure(), _project_structure())


def _empty_diff_fields(result):
    return (
        result.structures_added,
        result.structures_removed,
        result.properties_added,
        result.properties_removed,
        result.properties_retyped,
        result.properties_renamed,
        result.options_added,
        result.options_removed,
        result.options_renamed,
    )


# ---------------------------------------------------------------------------
# normalize_structures
# ---------------------------------------------------------------------------


def test_normalize_reads_the_adapter_shape():
    normalized = cs.normalize_structures(_fresh())

    assert normalized["structures"]["RootTask"]["status"] == {
        "type": "label",
        "options": ("done", "open"),
    }
    assert normalized["structures"]["RootTask"]["due"] == {"type": "date", "options": ()}
    assert normalized["display_names"]["RootTask"]["properties"]["status"] == "Status"
    assert normalized["display_names"]["RootTask"]["options"]["status"]["open"] == "Open"
    assert normalized["structure_names"]["RootTask"] == "RootTask"
    assert normalized["malformed"] == ()


def test_normalize_accepts_dict_style_property_definitions():
    structure = {
        "id": "RootTask",
        "title": "Task",
        "propertyDefinitions": {
            "status": _definition("status", "label", labels=[("open", "Open")]),
        },
    }

    normalized = cs.normalize_structures(_payload(structure))

    assert normalized["structures"]["RootTask"]["status"] == {
        "type": "label",
        "options": ("open",),
    }


def test_normalize_accepts_bare_list_and_contract_document():
    rows = [_task_structure()]

    from_list = cs.normalize_structures(rows)
    from_document = cs.normalize_structures(
        {"version": 1, "scope_key": "all", "generation": 3, "structures": {"structures": rows}}
    )

    assert from_list["structures"] == from_document["structures"]


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------


def test_fingerprint_stable_under_reordering():
    forward = _fresh()
    shuffled = _payload(
        _structure(
            "custom-project",
            [
                _definition("state", "label", labels=[("done", "Done"), ("active", "Active")]),
                _definition("title", "title"),
            ],
        ),
        _structure(
            "RootTask",
            [
                _definition("status", "label", labels=[("done", "Done"), ("open", "Open")]),
                _definition("due", "date"),
                _definition("title", "title"),
            ],
        ),
    )

    assert cs.fingerprint(forward) == cs.fingerprint(shuffled)


def test_fingerprint_stable_under_rename_only():
    renamed = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title", name=PROP_SENTINEL),
                _definition("due", "date", name=PROP_SENTINEL),
                _definition(
                    "status",
                    "label",
                    name=PROP_SENTINEL,
                    labels=[("open", OPTION_SENTINEL), ("done", OPTION_SENTINEL)],
                ),
            ],
            title=TITLE_SENTINEL,
        ),
        _project_structure(),
    )

    assert cs.fingerprint(_fresh()) == cs.fingerprint(renamed)


def test_fingerprint_ignores_writability_and_other_metadata():
    mutated = _payload(
        _structure(
            "RootTask",
            [
                {**_definition("title", "title"), "writable": False, "createdAt": "2026-01-01"},
                {**_definition("due", "date"), "writable": False},
                {
                    **_definition(
                        "status", "label", labels=[("open", "Open"), ("done", "Done")]
                    ),
                    "writable": False,
                    "labelSet": [
                        {"id": "open", "name": "Open", "count": 12},
                        {"id": "done", "name": "Done", "count": 4},
                    ],
                },
            ],
        ),
        _project_structure(),
    )

    assert cs.fingerprint(_fresh()) == cs.fingerprint(mutated)


def test_fingerprint_changes_on_option_added_or_removed():
    base = cs.fingerprint(_fresh())
    removed = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("due", "date"),
                _definition("status", "label", labels=[("open", "Open")]),
            ],
        ),
        _project_structure(),
    )
    added = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("due", "date"),
                _definition(
                    "status", "label", labels=[("open", "Open"), ("done", "Done"), ("blocked", "B")]
                ),
            ],
        ),
        _project_structure(),
    )

    assert cs.fingerprint(removed) != base
    assert cs.fingerprint(added) != base


def test_fingerprint_changes_on_property_added_removed_or_retyped():
    base = cs.fingerprint(_fresh())
    added = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("due", "date"),
                _definition("status", "label", labels=[("open", "Open"), ("done", "Done")]),
                _definition("estimate", "number"),
            ],
        ),
        _project_structure(),
    )
    removed = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("status", "label", labels=[("open", "Open"), ("done", "Done")]),
            ],
        ),
        _project_structure(),
    )
    retyped = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("due", "datetime"),
                _definition("status", "label", labels=[("open", "Open"), ("done", "Done")]),
            ],
        ),
        _project_structure(),
    )

    assert cs.fingerprint(added) != base
    assert cs.fingerprint(removed) != base
    assert cs.fingerprint(retyped) != base


def test_fingerprint_changes_on_structure_added_or_removed():
    base = cs.fingerprint(_fresh())
    added = _payload(_task_structure(), _project_structure(), _structure("custom-note", []))
    removed = _payload(_task_structure())

    assert cs.fingerprint(added) != base
    assert cs.fingerprint(removed) != base


def test_fingerprint_is_a_stable_hex_digest_treating_none_as_empty():
    assert cs.fingerprint(None) == cs.fingerprint({"structures": []})
    assert cs.fingerprint(None) == cs.fingerprint([])
    digest = cs.fingerprint(_fresh())

    assert isinstance(digest, str)
    assert len(digest) == 64
    assert int(digest, 16) >= 0
    assert digest == cs.fingerprint(_fresh())


# ---------------------------------------------------------------------------
# diff: baseline and each category
# ---------------------------------------------------------------------------


def test_diff_without_a_baseline_is_empty():
    result = cs.diff(None, _fresh())

    assert result.baseline is True
    assert result.severity == "none"
    assert all(not field for field in _empty_diff_fields(result))
    assert result.unresolved_open_values == {}
    assert result.fingerprint_before == ""
    assert result.fingerprint_after == cs.fingerprint(_fresh())
    assert cs.to_dict(result) == cs.to_dict(cs.diff(None, _fresh()))


def test_diff_of_identical_or_reordered_payloads_is_empty():
    shuffled = _payload(
        _structure(
            "custom-project",
            [
                _definition("state", "label", labels=[("done", "Done"), ("active", "Active")]),
                _definition("title", "title"),
            ],
        ),
        _task_structure(),
    )
    result = cs.diff(_fresh(), shuffled)

    assert result.baseline is False
    assert result.severity == "none"
    assert all(not field for field in _empty_diff_fields(result))
    assert result.fingerprint_before == result.fingerprint_after
    assert cs.summary_text(result) == ""


def test_diff_reports_structure_added_and_removed():
    result = cs.diff(
        _fresh(),
        _payload(_task_structure(), _structure("custom-note", [])),
    )

    assert result.structures_added == ("custom-note",)
    assert result.structures_removed == ("custom-project",)
    # Removal is warn by default: with no mapped filter every structure counts.
    assert result.severity == "warn"


def test_diff_reports_properties_added_and_removed():
    current = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("status", "label", labels=[("open", "Open"), ("done", "Done")]),
                _definition("estimate", "number"),
            ],
        ),
        _project_structure(),
    )
    result = cs.diff(_fresh(), current)

    assert result.properties_removed == (("RootTask", "due"),)
    assert result.properties_added == (("RootTask", "estimate"),)
    assert result.properties_retyped == ()
    # Removal is warn by default: with no mapped filter every structure counts.
    assert result.severity == "warn"


def test_diff_reports_property_retyped_with_both_types():
    current = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("due", "datetime"),
                _definition("status", "label", labels=[("open", "Open"), ("done", "Done")]),
            ],
        ),
        _project_structure(),
    )
    result = cs.diff(_fresh(), current)

    assert result.properties_retyped == (("RootTask", "due", "date", "datetime"),)
    assert result.severity == "warn"


def test_diff_reports_property_renamed_without_names():
    current = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("due", "date", name=PROP_SENTINEL),
                _definition("status", "label", labels=[("open", "Open"), ("done", "Done")]),
            ],
        ),
        _project_structure(),
    )
    result = cs.diff(_fresh(), current)

    assert result.properties_renamed == (("RootTask", "due"),)
    assert result.severity == "info"
    assert PROP_SENTINEL not in json.dumps(cs.to_dict(result))


def test_diff_reports_options_added_removed_and_renamed():
    added = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("due", "date"),
                _definition(
                    "status",
                    "label",
                    labels=[("open", "Open"), ("done", "Done"), ("blocked", "Blocked")],
                ),
            ],
        ),
        _project_structure(),
    )
    removed = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("due", "date"),
                _definition("status", "label", labels=[("open", "Open")]),
            ],
        ),
        _project_structure(),
    )
    renamed = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("due", "date"),
                _definition(
                    "status", "label", labels=[("open", OPTION_SENTINEL), ("done", "Done")]
                ),
            ],
        ),
        _project_structure(),
    )

    assert cs.diff(_fresh(), added).options_added == (("RootTask", "status", "blocked"),)
    assert cs.diff(_fresh(), removed).options_removed == (("RootTask", "status", "done"),)
    renamed_result = cs.diff(_fresh(), renamed)
    assert renamed_result.options_renamed == (("RootTask", "status", "open"),)
    assert renamed_result.severity == "info"
    assert OPTION_SENTINEL not in json.dumps(cs.to_dict(renamed_result))


def test_diff_reports_the_example_from_the_brief():
    current = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("due", "date"),
                _definition("status", "label", labels=[("open", "Open")]),
            ],
        ),
        _project_structure(),
        _structure("custom-note", []),
    )
    result = cs.diff(_fresh(), current)

    assert result.options_removed == (("RootTask", "status", "done"),)
    assert result.structures_added == ("custom-note",)
    assert result.severity == "warn"
    assert cs.summary_text(result) == (
        "Capacities changed since the last refresh: 1 status option removed, "
        "1 new type. Review the mapping in Connections."
    )


# ---------------------------------------------------------------------------
# diff: severity and mapped-structure filtering
# ---------------------------------------------------------------------------


def test_removing_a_mapped_structure_warns():
    current = _payload(_task_structure())
    mapped = cs.diff(_fresh(), current, mapped_structure_ids={"RootTask", "custom-project"})
    unmapped = cs.diff(_fresh(), current, mapped_structure_ids={"RootTask"})

    assert mapped.structures_removed == ("custom-project",)
    assert mapped.severity == "warn"
    assert unmapped.severity == "info"


def test_option_removal_on_an_unmapped_structure_is_info_only():
    current = _payload(
        _task_structure(),
        _structure(
            "custom-project",
            [
                _definition("title", "title"),
                _definition("state", "label", labels=[("active", "Active")]),
            ],
        ),
    )
    unmapped = cs.diff(_fresh(), current, mapped_structure_ids={"RootTask"})
    mapped = cs.diff(_fresh(), current, mapped_structure_ids={"custom-project"})
    all_mapped = cs.diff(_fresh(), current)

    assert unmapped.options_removed == (("custom-project", "state", "done"),)
    assert unmapped.severity == "info"
    assert mapped.severity == "warn"
    assert all_mapped.severity == "warn"


def test_property_removal_and_retype_respect_the_mapped_filter():
    current = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("status", "label", labels=[("open", "Open"), ("done", "Done")]),
            ],
        ),
        _project_structure(),
    )
    unmapped = cs.diff(_fresh(), current, mapped_structure_ids={"custom-project"})
    mapped = cs.diff(_fresh(), current, mapped_structure_ids={"RootTask"})

    assert unmapped.properties_removed == (("RootTask", "due"),)
    assert unmapped.severity == "info"
    assert mapped.severity == "warn"


def test_added_properties_and_structures_are_info_even_when_mapped():
    current = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("due", "date"),
                _definition("status", "label", labels=[("open", "Open"), ("done", "Done")]),
                _definition("estimate", "number"),
            ],
        ),
        _project_structure(),
        _structure("custom-note", []),
    )
    result = cs.diff(_fresh(), current, mapped_structure_ids={"RootTask"})

    assert result.severity == "info"


# ---------------------------------------------------------------------------
# diff: unresolved open status values
# ---------------------------------------------------------------------------


def test_open_values_resolve_by_id_and_by_name_case_insensitively():
    result = cs.diff(
        _fresh(),
        _fresh(),
        open_status_values={"RootTask": ["open", "Open", "OPEN", "done"]},
    )

    assert result.unresolved_open_values == {"RootTask": (0, 4)}
    assert result.severity == "none"


def test_open_values_resolve_against_any_label_property_of_the_structure():
    structure = _structure(
        "RootTask",
        [
            _definition("title", "title"),
            _definition("status", "label", labels=[("open", "Open"), ("done", "Done")]),
            _definition("stage", "label", labels=[("triage", "Triage"), ("Active", "Active")]),
        ],
    )
    result = cs.diff(
        _payload(structure),
        _payload(structure),
        open_status_values={"RootTask": ["triage", "active"]},
    )

    assert result.unresolved_open_values == {"RootTask": (0, 2)}


def test_open_values_partially_unresolved():
    result = cs.diff(
        _fresh(),
        _fresh(),
        open_status_values={"RootTask": ["open", "closed", "archived"]},
    )

    assert result.unresolved_open_values == {"RootTask": (2, 3)}
    assert result.severity == "warn"
    assert cs.summary_text(result) == (
        "Capacities changed since the last refresh: 2 open status values no longer "
        "resolve. Review the mapping in Connections."
    )


def test_unresolved_open_value_on_an_unmapped_structure_is_info():
    result = cs.diff(
        _fresh(),
        _fresh(),
        mapped_structure_ids={"RootTask"},
        open_status_values={"custom-project": ["closed"]},
    )

    assert result.unresolved_open_values == {"custom-project": (1, 1)}
    assert result.severity == "info"


def test_open_values_for_an_unknown_structure_are_ignored():
    result = cs.diff(
        _fresh(),
        _fresh(),
        open_status_values={"custom-unknown": ["closed"], "RootTask": ["open"]},
    )

    assert result.unresolved_open_values == {"RootTask": (0, 1)}
    assert result.severity == "none"


def test_a_removed_status_property_makes_its_open_values_unresolved():
    current = _payload(
        _structure(
            "RootTask",
            [_definition("title", "title"), _definition("due", "date")],
        ),
        _project_structure(),
    )
    result = cs.diff(
        _fresh(),
        current,
        mapped_structure_ids={"RootTask"},
        open_status_values={"RootTask": ["open", "done"]},
    )

    assert result.properties_removed == (("RootTask", "status"),)
    assert result.unresolved_open_values == {"RootTask": (2, 2)}
    assert result.severity == "warn"


# ---------------------------------------------------------------------------
# Malformed input never raises and stays deterministic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        (),
        "structures",
        42,
        {},
        {"structures": None},
        {"structures": "nope"},
        {"structures": [{}, "row", None, 7, ["nested"]]},
        {"structures": [{"id": "A"}, {"id": "A"}, {"title": "no id"}, {"id": 5}]},
        {
            "structures": [
                {
                    "id": "A",
                    "propertyDefinitions": "nope",
                }
            ]
        },
        {
            "structures": [
                {
                    "id": "A",
                    "propertyDefinitions": [
                        {"id": "p", "type": "label", "labelSet": "nope"},
                        {"id": "p", "type": "text"},
                        {"type": "text"},
                        "definition",
                        {"id": 9, "type": 7},
                    ],
                }
            ]
        },
        {
            "structures": [
                {
                    "id": "A",
                    "propertyDefinitions": {
                        "p": {"id": "p", "type": "label", "labelSet": [{"name": "x"}, {"id": None}]},
                        "q": "not a definition",
                    },
                }
            ]
        },
    ],
)
def test_malformed_payloads_never_raise_and_are_deterministic(payload):
    first = cs.normalize_structures(payload)
    second = cs.normalize_structures(payload)

    assert first == second
    assert cs.fingerprint(payload) == cs.fingerprint(payload)
    assert isinstance(first["structures"], dict)
    assert isinstance(first["malformed"], tuple)

    baseline = cs.diff(payload, payload)
    other = cs.diff(payload, _fresh())
    assert baseline.severity == "none"
    assert all(not field for field in _empty_diff_fields(baseline))
    assert isinstance(other.severity, str)
    assert other.severity in {"none", "info", "warn"}


def test_malformed_payload_is_marked_and_skipped():
    payload = {
        "structures": [
            {"id": "A", "propertyDefinitions": []},
            "not a row",
            {"id": "A", "propertyDefinitions": []},
            {"title": "missing id"},
        ]
    }
    normalized = cs.normalize_structures(payload)

    assert sorted(normalized["structures"]) == ["A"]
    assert normalized["malformed"]  # non-empty, deterministic markers
    assert normalized == cs.normalize_structures(payload)


def test_duplicate_and_malformed_properties_are_marked_not_raised():
    payload = {
        "structures": [
            {
                "id": "A",
                "propertyDefinitions": [
                    {"id": "p", "type": "label", "labelSet": [{"id": "x", "name": "X"}]},
                    {"id": "p", "type": "text"},
                    {"type": "text"},
                ],
            }
        ]
    }
    normalized = cs.normalize_structures(payload)

    assert normalized["structures"]["A"]["p"] == {"type": "label", "options": ("x",)}
    assert normalized["malformed"]


# ---------------------------------------------------------------------------
# to_dict / summary_text: JSON-safe, content-free, pluralised
# ---------------------------------------------------------------------------


def _sentinel_payload():
    return _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title", name=PROP_SENTINEL),
                _definition("due", "date", name=PROP_SENTINEL),
                _definition(
                    "status",
                    "label",
                    name=PROP_SENTINEL,
                    labels=[("open", OPTION_SENTINEL), ("done", OPTION_SENTINEL)],
                ),
            ],
            title=TITLE_SENTINEL,
        )
    )


def test_to_dict_is_json_safe_and_content_free():
    previous = _sentinel_payload()
    current = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title", name=PROP_SENTINEL),
                _definition(
                    "status",
                    "label",
                    name=PROP_SENTINEL,
                    labels=[("open", OPTION_SENTINEL)],
                ),
                _definition("estimate", "number", name=PROP_SENTINEL),
            ],
            title=TITLE_SENTINEL,
        ),
        _structure("custom-note", [], title=TITLE_SENTINEL),
    )
    document = cs.to_dict(cs.diff(previous, current))

    encoded = json.dumps(document)
    for sentinel in (PROP_SENTINEL, OPTION_SENTINEL, TITLE_SENTINEL):
        assert sentinel not in encoded
    assert document["schema_version"] == cs.SCHEMA_VERSION
    assert document["severity"] == "warn"
    assert document["structures_added"] == ["custom-note"]
    assert document["properties_removed"] == [["RootTask", "due"]]
    assert document["options_removed"] == [["RootTask", "status", "done"]]
    assert document["counts"]["options_removed"] == 1
    assert json.loads(encoded) == document


def test_to_dict_is_json_safe_for_a_baseline_diff():
    document = cs.to_dict(cs.diff(None, _sentinel_payload()))

    json.dumps(document)
    assert document["baseline"] is True
    assert document["severity"] == "none"
    assert document["fingerprint_before"] == ""
    assert document["counts"]["options_removed"] == 0


def test_summary_text_pluralises_correctly():
    previous = _fresh()
    two_removed = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("due", "date"),
                _definition("status", "label", labels=[("open", "Open")]),
            ],
        ),
        _structure(
            "custom-project",
            [
                _definition("title", "title"),
                _definition("state", "label", labels=[("active", "Active")]),
            ],
        ),
    )
    one_removed = _payload(
        _structure(
            "RootTask",
            [
                _definition("title", "title"),
                _definition("due", "date"),
                _definition("status", "label", labels=[("open", "Open")]),
            ],
        ),
        _project_structure(),
    )

    plural = cs.summary_text(cs.diff(previous, two_removed))
    singular = cs.summary_text(cs.diff(previous, one_removed))

    assert plural == (
        "Capacities changed since the last refresh: 2 status options removed. "
        "Review the mapping in Connections."
    )
    assert singular == (
        "Capacities changed since the last refresh: 1 status option removed. "
        "Review the mapping in Connections."
    )
    assert cs.summary_text(cs.diff(previous, previous)) == ""


def test_summary_text_is_content_free():
    previous = _sentinel_payload()
    current = _payload(_structure("RootTask", [_definition("title", "title")]))
    text = cs.summary_text(cs.diff(previous, current))

    for sentinel in (PROP_SENTINEL, OPTION_SENTINEL, TITLE_SENTINEL):
        assert sentinel not in text
    assert text.startswith("Capacities changed since the last refresh: ")


# ---------------------------------------------------------------------------
# Purity: stdlib only, no I/O reachable
# ---------------------------------------------------------------------------

_MODULE_PATH = Path(__file__).parent.parent / "capacities_schema.py"

_ALLOWED_IMPORTS = {
    "__future__",
    "hashlib",
    "json",
    "dataclasses",
    "typing",
    "collections",
}

_FORBIDDEN_IMPORT_ROOTS = {
    "builtins",
    "http",
    "httpx",
    "io",
    "main",
    "os",
    "pathlib",
    "requests",
    "socket",
    "subprocess",
    "urllib",
    "capacities_adapter",
    "capacities_refresh",
    "capacities_refresh_state",
    "capacities_cache_io",
}


def _imported_roots() -> set[str]:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                roots.add("<relative>")
            elif node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_module_imports_only_pure_stdlib():
    roots = _imported_roots()

    assert roots <= _ALLOWED_IMPORTS
    assert not (roots & _FORBIDDEN_IMPORT_ROOTS)
    assert "main" not in sys.modules or "main" not in roots


def test_module_performs_no_file_or_network_io(monkeypatch):
    def _forbidden(*args, **kwargs):
        raise AssertionError("the schema module must not open files")

    monkeypatch.setattr(builtins, "open", _forbidden)
    monkeypatch.setattr(io, "open", _forbidden)

    previous = _fresh()
    current = _payload(_task_structure())
    normalized = cs.normalize_structures(current)
    assert normalized["structures"]
    assert cs.fingerprint(current)
    result = cs.diff(previous, current, open_status_values={"RootTask": ["open"]})
    json.dumps(cs.to_dict(result))
    cs.summary_text(result)
