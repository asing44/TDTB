"""Contract tests for the machine-local Capacities tag-title cache.

The plan rows replayed by the offline direct intake are projected from the
cached generation, which carries tag references as bare ids. The RootTag
catalog that names them is only readable while a refresh is running, so the
refresh persists an id -> title map here and the replay seeds its adapter from
it. Titles are display-only: never identity, never a fingerprint, never in the
published generation or the object cache.
"""
from __future__ import annotations

import json
import stat
import sys
import types
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_builder as cb  # noqa: E402
import capacities_cache_io as cache_io  # noqa: E402
import capacities_intake as ci  # noqa: E402
import capacities_refresh as cr  # noqa: E402
import capacities_refresh_state as crs  # noqa: E402
import capacities_settings  # noqa: E402
import capacities_tag_titles as ttt  # noqa: E402
from capacities_adapter import (  # noqa: E402
    CapacitiesAdapter,
    CapacitiesConfig,
    StructureMapping,
)

SPACE = "space-1"
OTHER_SPACE = "space-2"
BASE_URL = "https://api.capacities.io"
TAG = "3257d9ad-2c11-4fce-9675-a53b36a8c662"
OTHER_TAG = "5a25370b-f9a0-40cf-bc3a-0cab4744913c"
TODAY = date(2026, 10, 9)
NOON = datetime(2026, 10, 9, 12, 0)


# ---------------------------------------------------------------------------
# Cache contract
# ---------------------------------------------------------------------------

def _document(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_round_trip_and_namespace_isolation(tmp_path):
    path = tmp_path / "tag-titles.json"

    assert ttt.remember_titles(SPACE, BASE_URL, {TAG: "home"}, path=path) is True
    assert ttt.read_titles(SPACE, BASE_URL, path=path) == {TAG: "home"}
    # A trailing slash is the same provider scope, not a new namespace.
    assert ttt.read_titles(SPACE, BASE_URL + "/", path=path) == {TAG: "home"}
    # Another space or another provider URL sees nothing.
    assert ttt.read_titles(OTHER_SPACE, BASE_URL, path=path) == {}
    assert ttt.read_titles(SPACE, "https://other.example", path=path) == {}
    assert set(_document(path)["namespaces"]) == {
        cache_io.cache_namespace(SPACE, BASE_URL)
    }


def test_a_missing_cache_reads_empty_and_creates_nothing(tmp_path):
    path = tmp_path / "nested" / "tag-titles.json"

    assert ttt.read_titles(SPACE, BASE_URL, path=path) == {}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        json.dumps([]),
        json.dumps({"version": 2, "namespaces": {}}),
        json.dumps({"version": 1, "namespaces": []}),
        json.dumps({"version": 1, "namespaces": {cache_io.cache_namespace(SPACE, BASE_URL): 5}}),
    ],
)
def test_a_corrupt_or_unsupported_cache_reads_empty_without_repair(tmp_path, text):
    path = tmp_path / "tag-titles.json"
    path.write_text(text, encoding="utf-8")
    before = path.read_bytes()

    assert ttt.read_titles(SPACE, BASE_URL, path=path) == {}
    # The read side never repairs, rewrites, or adds a lock file.
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["tag-titles.json"]


def test_a_failing_write_returns_false_and_never_raises(tmp_path):
    # A path whose parent is a regular file cannot be created or written.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    assert ttt.remember_titles(SPACE, BASE_URL, {TAG: "home"}, path=blocker / "x.json") is False
    assert ttt.remember_titles(SPACE, BASE_URL, "not a mapping", path=tmp_path / "x.json") is False


def test_blank_titles_are_never_stored(tmp_path):
    path = tmp_path / "tag-titles.json"

    assert ttt.remember_titles(
        SPACE, BASE_URL, {TAG: "home", OTHER_TAG: "   ", "": "orphan"}, path=path
    )

    assert ttt.read_titles(SPACE, BASE_URL, path=path) == {TAG: "home"}


def test_the_cache_file_is_owner_only(tmp_path):
    path = tmp_path / "tag-titles.json"

    assert ttt.remember_titles(SPACE, BASE_URL, {TAG: "home"}, path=path)

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_other_namespaces_survive_a_rewrite(tmp_path):
    path = tmp_path / "tag-titles.json"

    assert ttt.remember_titles(SPACE, BASE_URL, {TAG: "home"}, path=path)
    assert ttt.remember_titles(OTHER_SPACE, BASE_URL, {TAG: "other space"}, path=path)

    assert ttt.read_titles(SPACE, BASE_URL, path=path) == {TAG: "home"}
    assert ttt.read_titles(OTHER_SPACE, BASE_URL, path=path) == {TAG: "other space"}


# ---------------------------------------------------------------------------
# Complete replaces, partial merges
# ---------------------------------------------------------------------------

def test_a_complete_catalog_replaces_the_namespace_map(tmp_path):
    path = tmp_path / "tag-titles.json"

    assert ttt.remember_titles(SPACE, BASE_URL, {TAG: "home", OTHER_TAG: "work"}, path=path)
    # A rename (and a dropped id) reach the cache on the next complete read.
    assert ttt.remember_titles(SPACE, BASE_URL, {TAG: "household"}, path=path)

    assert ttt.read_titles(SPACE, BASE_URL, path=path) == {TAG: "household"}


def test_a_partial_catalog_merges_without_deleting_known_ids(tmp_path):
    path = tmp_path / "tag-titles.json"

    assert ttt.remember_titles(SPACE, BASE_URL, {TAG: "home", OTHER_TAG: "work"}, path=path)
    assert ttt.remember_titles(
        SPACE, BASE_URL, {OTHER_TAG: "errands"}, merge=True, path=path
    )

    assert ttt.read_titles(SPACE, BASE_URL, path=path) == {
        TAG: "home",
        OTHER_TAG: "errands",
    }


# ---------------------------------------------------------------------------
# Adapter sink: what a refresh persists
# ---------------------------------------------------------------------------

def _structures():
    return [
        {
            "id": "RootTask",
            "title": "Task",
            "propertyDefinitions": [
                {"id": "title", "type": "title"},
                {"id": "due", "type": "date"},
                {
                    "id": "status",
                    "type": "label",
                    "labelSet": [{"id": "active", "name": "Active"}],
                },
            ],
        }
    ]


def _mapping():
    return StructureMapping(
        structure_id="RootTask",
        title_property="title",
        date_property="due",
        open_status_property="status",
        open_status_values=frozenset({"active"}),
    )


def _object(object_id, *, tags=None, title="Task"):
    properties = {
        "title": {"type": "title", "title": {"value": title}},
        "status": {"type": "label", "label": [{"id": "active"}]},
    }
    if tags is not None:
        properties["tags"] = {"type": "entity", "entity": tags}
    return {"id": object_id, "structureId": "RootTask", "properties": properties}


class _CatalogProvider:
    """A listing-capable seam that serves one RootTag catalog page."""

    supports_object_listing = True

    def __init__(self, objects, catalog=None, error=None):
        self._objects = list(objects)
        self._catalog = catalog
        self._error = error
        self.catalog_calls = []

    def fetch_structures(self):
        return _structures()

    def list_objects(self, structure_id, cursor=None):
        if structure_id == "RootTag":
            self.catalog_calls.append(cursor)
            if self._error is not None:
                raise self._error
            return self._catalog or {"objects": [], "next_cursor": None}
        assert structure_id == "RootTask"
        return {"objects": list(self._objects), "next_cursor": None}

    def get_object(self, object_id):  # pragma: no cover - never needed
        raise AssertionError("no content read expected")

    def patch_object(self, object_id, properties):  # pragma: no cover
        raise AssertionError("no write expected")


def _adapter(provider, sink=None):
    return CapacitiesAdapter(
        provider,
        CapacitiesConfig(space_id=SPACE, mappings=(_mapping(),)),
        tag_titles_sink=sink,
    )


class _Sink:
    def __init__(self):
        self.calls = []

    def __call__(self, titles, complete):
        self.calls.append((dict(titles), complete))


def test_a_complete_catalog_is_persisted_as_complete():
    sink = _Sink()
    provider = _CatalogProvider(
        [_object("task-1", tags=[{"id": TAG}])],
        catalog={"objects": [{"id": TAG, "title": "home"}], "next_cursor": None},
    )

    result = _adapter(provider, sink).items_for_day(TODAY)

    assert result.items[0]["capacities_tags"] == [
        {"space_id": SPACE, "tag_id": TAG, "title": "home"}
    ]
    assert sink.calls == [({TAG: "home"}, True)]


def test_a_partial_catalog_is_persisted_as_partial():
    sink = _Sink()
    provider = _CatalogProvider(
        [_object("task-1", tags=[{"id": TAG}, {"id": OTHER_TAG}])],
        # The second row has no title, so the listing is partial.
        catalog={
            "objects": [{"id": TAG, "title": "home"}, {"id": OTHER_TAG}],
            "next_cursor": None,
        },
    )

    _adapter(provider, sink).items_for_day(TODAY)

    assert sink.calls == [({TAG: "home"}, False)]


def test_a_failed_catalog_persists_nothing():
    sink = _Sink()
    provider = _CatalogProvider(
        [_object("task-1", tags=[{"id": TAG}])], error=RuntimeError("boom")
    )

    _adapter(provider, sink).items_for_day(TODAY)

    assert sink.calls == []


def test_a_raising_sink_never_breaks_the_projection():
    def sink(_titles, _complete):
        raise RuntimeError("cache exploded")

    provider = _CatalogProvider(
        [_object("task-1", tags=[{"id": TAG}])],
        catalog={"objects": [{"id": TAG, "title": "home"}], "next_cursor": None},
    )

    result = _adapter(provider, sink).items_for_day(TODAY)

    assert result.items[0]["capacities_tags"][0]["title"] == "home"


def test_no_sink_means_nothing_is_persisted():
    provider = _CatalogProvider(
        [_object("task-1", tags=[{"id": TAG}])],
        catalog={"objects": [{"id": TAG, "title": "home"}], "next_cursor": None},
    )

    result = _adapter(provider).items_for_day(TODAY)

    assert result.items[0]["capacities_tags"][0]["title"] == "home"


def test_the_refresh_coordinator_sink_writes_the_cache(tmp_path, monkeypatch):
    path = tmp_path / "tag-titles.json"
    monkeypatch.setattr(ttt, "DEFAULT_TAG_TITLES_CACHE_PATH", path)
    stub = types.SimpleNamespace(
        space_id=SPACE, provider=types.SimpleNamespace(base_url=BASE_URL)
    )

    cr.RefreshCoordinator._remember_tag_titles(stub, {TAG: "home"}, True)

    assert ttt.read_titles(SPACE, BASE_URL, path=path) == {TAG: "home"}


def test_the_refresh_coordinator_sink_merges_a_partial_read(tmp_path, monkeypatch):
    path = tmp_path / "tag-titles.json"
    monkeypatch.setattr(ttt, "DEFAULT_TAG_TITLES_CACHE_PATH", path)
    stub = types.SimpleNamespace(
        space_id=SPACE, provider=types.SimpleNamespace(base_url=BASE_URL)
    )
    assert ttt.remember_titles(SPACE, BASE_URL, {TAG: "home"}, path=path)

    cr.RefreshCoordinator._remember_tag_titles(stub, {OTHER_TAG: "work"}, False)

    assert ttt.read_titles(SPACE, BASE_URL, path=path) == {
        TAG: "home",
        OTHER_TAG: "work",
    }


def test_provider_base_url_resolution_prefers_the_seam_then_the_default():
    assert ttt.provider_base_url() == ttt.DEFAULT_PROVIDER_BASE_URL
    assert ttt.provider_base_url(object()) == ttt.DEFAULT_PROVIDER_BASE_URL
    assert (
        ttt.provider_base_url(types.SimpleNamespace(raw=types.SimpleNamespace(base_url="https://x.example")))
        == "https://x.example"
    )
    assert ttt.provider_base_url(types.SimpleNamespace(base_url="  ")) == (
        ttt.DEFAULT_PROVIDER_BASE_URL
    )


# ---------------------------------------------------------------------------
# Offline replay: persistence reaches the intake projection with no provider call
# ---------------------------------------------------------------------------

def _replay_read(objects):
    snapshot = crs.CompleteSnapshot(
        scope_key="all",
        revision=1,
        generation=1,
        installed_at=0.0,
        listing_checked_at=0.0,
        members=tuple(
            crs.SnapshotMember(
                object_id=obj["id"], type_key=obj["structureId"], content_read_at=0.0
            )
            for obj in objects
        ),
        type_check_times=(("RootTask", 0.0),),
    )
    return cb.DirectIntakeRead(
        space_id=SPACE,
        mappings=(_mapping(),),
        settings=capacities_settings.read_settings().settings,
        snapshot=snapshot,
        structures=_structures(),
        objects=tuple(objects),
        unreadable=0,
    )


@pytest.fixture
def replay_seam(monkeypatch):
    """Any listing through the offline seam fails the test and is recorded."""
    calls: list[tuple] = []

    def _trip(self, *args, **kwargs):
        calls.append(args)
        raise ci.ProviderCallDefect("offline intake must not list objects")

    monkeypatch.setattr(ci._OfflineProvider, "list_objects", _trip)
    return calls


def test_a_persisted_map_fills_titles_on_the_offline_replay(tmp_path, monkeypatch, replay_seam):
    path = tmp_path / "tag-titles.json"
    monkeypatch.setattr(ttt, "DEFAULT_TAG_TITLES_CACHE_PATH", path)
    # The refresh persisted the catalog it read.
    assert ttt.remember_titles(SPACE, BASE_URL, {TAG: "home"}, path=path)

    rows, pool, warnings, _coverage = ci.load_direct_rows(
        now=NOON, read=_replay_read([_object("task-1", tags=[{"id": TAG}])])
    )

    projected = [*rows, *pool]
    assert [row["capacities_tags"] for row in projected] == [
        [{"space_id": SPACE, "tag_id": TAG, "title": "home"}]
    ]
    assert replay_seam == []
    assert warnings == []


def test_a_rename_reaches_the_next_offline_replay(tmp_path, monkeypatch, replay_seam):
    path = tmp_path / "tag-titles.json"
    monkeypatch.setattr(ttt, "DEFAULT_TAG_TITLES_CACHE_PATH", path)
    assert ttt.remember_titles(SPACE, BASE_URL, {TAG: "home"}, path=path)
    # The next refresh reads the catalog again and the tag was renamed.
    assert ttt.remember_titles(SPACE, BASE_URL, {TAG: "household"}, path=path)

    rows, pool, _warnings, _coverage = ci.load_direct_rows(
        now=NOON, read=_replay_read([_object("task-1", tags=[{"id": TAG}])])
    )

    assert [*rows, *pool][0]["capacities_tags"] == [
        {"space_id": SPACE, "tag_id": TAG, "title": "household"}
    ]
    assert replay_seam == []


def test_a_missing_cache_leaves_replay_titles_blank(tmp_path, monkeypatch, replay_seam):
    monkeypatch.setattr(ttt, "DEFAULT_TAG_TITLES_CACHE_PATH", tmp_path / "absent.json")

    rows, pool, warnings, _coverage = ci.load_direct_rows(
        now=NOON, read=_replay_read([_object("task-1", tags=[{"id": TAG}])])
    )

    assert [*rows, *pool][0]["capacities_tags"] == [
        {"space_id": SPACE, "tag_id": TAG, "title": ""}
    ]
    assert replay_seam == []
    assert warnings == []


def test_a_corrupt_cache_leaves_replay_titles_blank_without_warning(
    tmp_path, monkeypatch, replay_seam
):
    path = tmp_path / "tag-titles.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(ttt, "DEFAULT_TAG_TITLES_CACHE_PATH", path)

    rows, pool, warnings, _coverage = ci.load_direct_rows(
        now=NOON, read=_replay_read([_object("task-1", tags=[{"id": TAG}])])
    )

    assert [*rows, *pool][0]["capacities_tags"] == [
        {"space_id": SPACE, "tag_id": TAG, "title": ""}
    ]
    assert path.read_text(encoding="utf-8") == "{not json"
    assert replay_seam == []
    assert warnings == []


def test_the_cache_is_keyed_per_space_on_replay(tmp_path, monkeypatch, replay_seam):
    path = tmp_path / "tag-titles.json"
    monkeypatch.setattr(ttt, "DEFAULT_TAG_TITLES_CACHE_PATH", path)
    assert ttt.remember_titles(OTHER_SPACE, BASE_URL, {TAG: "other space"}, path=path)

    rows, pool, _warnings, _coverage = ci.load_direct_rows(
        now=NOON, read=_replay_read([_object("task-1", tags=[{"id": TAG}])])
    )

    assert [*rows, *pool][0]["capacities_tags"] == [
        {"space_id": SPACE, "tag_id": TAG, "title": ""}
    ]
    assert replay_seam == []


def test_seeded_titles_never_change_the_source_fingerprint():
    """Titles are display-only: a rename cannot move any fingerprint."""
    objects = [_object("task-1", tags=[{"id": TAG}])]

    plain = _adapter(_CatalogProvider(objects, catalog={"objects": [], "next_cursor": None}))
    seeded = _adapter(_CatalogProvider(objects, catalog={"objects": [], "next_cursor": None}))
    seeded.seed_tag_titles({TAG: "home"})

    bare_row = plain.items_for_day_from_objects(TODAY, objects).items[0]
    seeded_row = seeded.items_for_day_from_objects(TODAY, objects).items[0]

    assert seeded_row["capacities_tags"] == [
        {"space_id": SPACE, "tag_id": TAG, "title": "home"}
    ]
    assert bare_row["capacities_tags"] == [
        {"space_id": SPACE, "tag_id": TAG, "title": ""}
    ]
    assert seeded_row["source_fingerprint"] == bare_row["source_fingerprint"]
