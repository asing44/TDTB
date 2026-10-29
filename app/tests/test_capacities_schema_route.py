"""Route tests for the Capacities schema-change notice (Settings banner).

``GET /capacities/refresh/status`` stays the tokenless local read; it now also
projects the stored schema-change sidecar (content-free ids/counts plus one
generated sentence). ``POST /capacities/refresh/schema-change/ack`` is
token-guarded like its ``start``/``cancel`` siblings and only flips the
acknowledgement of the matching ``to_hash``.

No test here reads a credential or contacts a provider: the provider is a fake
that is never called, and the state root is ``tmp_path``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import main as main_mod  # noqa: E402
import capacities_refresh as rr  # noqa: E402
import capacities_refresh_state as refresh_state  # noqa: E402
from capacities_refresh_helpers import (  # noqa: E402
    Clock,
    PRIMARY,
    SPACE,
    Sleeper,
    _mapping,
    _paged_provider,
    _store,
    TYPES,
)

STATUS_URL = "/capacities/refresh/status"
ACK_URL = "/capacities/refresh/schema-change/ack"

FROM_HASH = "a" * 64
TO_HASH = "b" * 64
STALE_HASH = "c" * 64
SUMMARY = (
    "Capacities changed since the last refresh: 1 status option removed. "
    "Review the mapping in Connections."
)
DETECTED_AT = "2026-10-29T12:00:00+00:00"

#: Strings that must never reach a route body: the sidecar carries ids and
#: counts only, so any object title, status value, or property name that a
#: broken writer smuggled in stays server-side.
SENTINEL_TITLE = "SENTINEL-STRUCTURE-TITLE"
SENTINEL_VALUE = "SENTINEL-STATUS-VALUE"
SENTINEL_PROPERTY = "SENTINEL-PROPERTY-NAME"

NOTICE_KEYS = {
    "severity",
    "summary",
    "detected_at",
    "generation",
    "to_hash",
    "from_hash",
    "acknowledged",
    "acknowledged_at",
    "diff",
}

COUNT_KEYS = (
    "structures_added",
    "structures_removed",
    "properties_added",
    "properties_removed",
    "properties_retyped",
    "properties_renamed",
    "options_added",
    "options_removed",
    "options_renamed",
    "unresolved_open_values",
)


def _factory(tmp_path):
    """A refresh coordinator over ``tmp_path`` whose provider is never called."""

    def build(config):
        return rr.RefreshCoordinator(
            root=tmp_path / "state",
            store=_store(tmp_path),
            provider=_paged_provider(objects={PRIMARY: [], "T2": []}),
            space_id=SPACE,
            mappings=(_mapping(PRIMARY),),
            revision_supplier=lambda: 1,
            scope_key="all",
            clock=Clock(),
            sleeper=Sleeper(Clock()),
        )

    return build


def _client(tmp_path, *, configured: bool = True) -> TestClient:
    app = main_mod.create_app()
    app.state.build_refresh_coordinator = (
        _factory(tmp_path) if configured else (lambda config: None)
    )
    client = TestClient(app)
    client.app_token = app.state.token
    return client


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _diff(**overrides) -> dict:
    counts = {key: 0 for key in COUNT_KEYS}
    counts["options_removed"] = 1
    document = {
        "schema_version": 1,
        "severity": "warn",
        "baseline": False,
        "fingerprint_before": FROM_HASH,
        "fingerprint_after": TO_HASH,
        "counts": counts,
        "structures_added": [],
        "structures_removed": [],
        "properties_added": [],
        "properties_removed": [],
        "properties_retyped": [],
        "properties_renamed": [],
        "options_added": [],
        "options_removed": [[PRIMARY, "status", "blocked-id"]],
        "options_renamed": [],
        "unresolved_open_values": {},
    }
    document.update(overrides)
    return document


def _record(**overrides) -> dict:
    record = {
        "schema_version": 1,
        "scope_key": "all",
        "from_hash": FROM_HASH,
        "to_hash": TO_HASH,
        "generation": 3,
        "detected_at": DETECTED_AT,
        "severity": "warn",
        "diff": _diff(),
        "summary": SUMMARY,
        "acknowledged": False,
        "acknowledged_at": None,
    }
    record.update(overrides)
    return record


def _store_with(tmp_path, **overrides):
    store = _store(tmp_path)
    store.write_schema_change(_record(**overrides))
    return store


def _sidecar_path(tmp_path) -> Path:
    return tmp_path / "state" / refresh_state.SCHEMA_CHANGE_FILENAME


def _string_leaves(value, path="$"):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _string_leaves(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _string_leaves(item, f"{path}[{index}]")


# ---------------------------------------------------------------------------
# (1) status: no record is null, on both branches
# ---------------------------------------------------------------------------


def test_status_reports_no_notice_when_none_is_stored(tmp_path):
    client = _client(tmp_path)

    response = client.get(STATUS_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["schema_change"] is None


def test_status_reports_no_notice_when_the_source_is_unconfigured(tmp_path):
    client = _client(tmp_path, configured=False)

    response = client.get(STATUS_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["schema_change"] is None


# ---------------------------------------------------------------------------
# (2) status: the warn notice is projected, without namespace or scope_key
# ---------------------------------------------------------------------------


def test_status_projects_the_stored_warn_notice(tmp_path):
    _store_with(tmp_path)
    client = _client(tmp_path)

    response = client.get(STATUS_URL)

    assert response.status_code == 200
    notice = response.json()["schema_change"]
    assert set(notice) == NOTICE_KEYS
    assert "namespace" not in notice
    assert "scope_key" not in notice
    assert notice["severity"] == "warn"
    assert notice["summary"] == SUMMARY
    assert notice["detected_at"] == DETECTED_AT
    assert notice["generation"] == 3
    assert notice["to_hash"] == TO_HASH
    assert notice["from_hash"] == FROM_HASH
    assert notice["acknowledged"] is False
    assert notice["acknowledged_at"] is None
    assert notice["diff"]["counts"]["options_removed"] == 1
    assert notice["diff"]["counts"]["structures_added"] == 0


def test_status_projects_only_counts_from_the_diff(tmp_path):
    _store_with(tmp_path)
    client = _client(tmp_path)

    notice = client.get(STATUS_URL).json()["schema_change"]

    assert set(notice["diff"]) == {"counts"}
    assert set(notice["diff"]["counts"]) == set(COUNT_KEYS)
    for value in notice["diff"]["counts"].values():
        assert type(value) is int


# ---------------------------------------------------------------------------
# (3) ack: the matching hash acknowledges, and status then says so
# ---------------------------------------------------------------------------


def test_ack_with_the_matching_hash_marks_the_notice_acknowledged(tmp_path):
    store = _store_with(tmp_path)
    client = _client(tmp_path)

    response = client.post(ACK_URL, headers=_auth(client), json={"to_hash": TO_HASH})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "acknowledged": True}
    stored = store.load_schema_change()
    assert stored["acknowledged"] is True
    assert isinstance(stored["acknowledged_at"], str) and stored["acknowledged_at"]
    notice = client.get(STATUS_URL).json()["schema_change"]
    assert notice["acknowledged"] is True
    assert notice["acknowledged_at"] == stored["acknowledged_at"]
    # Nothing else about the notice moved.
    assert notice["severity"] == "warn"
    assert notice["summary"] == SUMMARY
    assert notice["to_hash"] == TO_HASH
    assert notice["generation"] == 3


# ---------------------------------------------------------------------------
# (4) ack: a stale hash is a 409 and writes nothing
# ---------------------------------------------------------------------------


def test_ack_with_a_stale_hash_is_409_and_leaves_the_record_alone(tmp_path):
    store = _store_with(tmp_path)
    before = store.load_schema_change()
    client = _client(tmp_path)

    response = client.post(ACK_URL, headers=_auth(client), json={"to_hash": STALE_HASH})

    assert response.status_code == 409
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "stale"
    assert store.load_schema_change() == before
    assert client.get(STATUS_URL).json()["schema_change"]["acknowledged"] is False


# ---------------------------------------------------------------------------
# (5) ack: token-guarded exactly like its siblings
# ---------------------------------------------------------------------------


def test_ack_requires_the_app_token(tmp_path):
    store = _store_with(tmp_path)
    client = _client(tmp_path)

    response = client.post(ACK_URL, json={"to_hash": TO_HASH})

    sibling = client.post("/capacities/refresh/start", json={"mode": "refresh"})
    assert response.status_code == sibling.status_code == 403
    assert response.json()["detail"] == sibling.json()["detail"]
    assert store.load_schema_change()["acknowledged"] is False


def test_ack_rejects_a_wrong_token(tmp_path):
    _store_with(tmp_path)
    client = _client(tmp_path)

    response = client.post(
        ACK_URL, headers={"X-TDTB-Token": "not-the-token"}, json={"to_hash": TO_HASH}
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# (6) ack: a malformed body is a 422, like the sibling start body
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"to_hash": ""},
        {"to_hash": "   "},
        {"to_hash": 5},
        {"to_hash": None},
        {"to_hash": [TO_HASH]},
        {"to_hash": TO_HASH, "extra": 1},
    ],
)
def test_ack_rejects_a_malformed_body(tmp_path, body):
    store = _store_with(tmp_path)
    client = _client(tmp_path)

    response = client.post(ACK_URL, headers=_auth(client), json=body)

    assert response.status_code == 422
    assert store.load_schema_change()["acknowledged"] is False


# ---------------------------------------------------------------------------
# (7) ack: idempotent for an already acknowledged matching hash
# ---------------------------------------------------------------------------


def test_ack_is_idempotent(tmp_path):
    store = _store_with(tmp_path)
    client = _client(tmp_path)

    first = client.post(ACK_URL, headers=_auth(client), json={"to_hash": TO_HASH})
    after_first = store.load_schema_change()
    second = client.post(ACK_URL, headers=_auth(client), json={"to_hash": TO_HASH})

    assert first.status_code == second.status_code == 200
    assert second.json() == {"ok": True, "acknowledged": True}
    assert store.load_schema_change() == after_first


# ---------------------------------------------------------------------------
# (8) ack: nothing to acknowledge
# ---------------------------------------------------------------------------


def test_ack_without_a_notice_is_a_content_free_404(tmp_path):
    _store(tmp_path)
    client = _client(tmp_path)

    response = client.post(ACK_URL, headers=_auth(client), json={"to_hash": TO_HASH})

    assert response.status_code == 404
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "not_found"


def test_ack_with_an_unconfigured_source_keeps_the_sibling_503(tmp_path):
    client = _client(tmp_path, configured=False)

    response = client.post(ACK_URL, headers=_auth(client), json={"to_hash": TO_HASH})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "capacities_refresh_unconfigured"


# ---------------------------------------------------------------------------
# (9) status never raises on a corrupt sidecar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        b"{ this is not json",
        b"\xff\xfe binary",
        b"[]",
        b'{"version": 999, "severity": "warn"}',
        b'{"version": 1, "severity": "loud"}',
        b"",
    ],
)
def test_status_reads_null_when_the_sidecar_is_corrupt(tmp_path, payload):
    _store_with(tmp_path)
    _sidecar_path(tmp_path).write_bytes(payload)
    client = _client(tmp_path)

    response = client.get(STATUS_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["schema_change"] is None
    assert body["warnings"] == []


def test_ack_on_a_corrupt_sidecar_is_a_404(tmp_path):
    _store_with(tmp_path)
    _sidecar_path(tmp_path).write_bytes(b"{ this is not json")
    client = _client(tmp_path)

    response = client.post(ACK_URL, headers=_auth(client), json={"to_hash": TO_HASH})

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# (10) ack touches nothing else: no refresh, no other status field
# ---------------------------------------------------------------------------


def test_ack_changes_only_the_acknowledgement_of_the_status_read(tmp_path):
    _store_with(tmp_path)
    client = _client(tmp_path)
    before = client.get(STATUS_URL).json()

    client.post(ACK_URL, headers=_auth(client), json={"to_hash": TO_HASH})

    after = client.get(STATUS_URL).json()
    assert after["job"] is None
    assert after["phase"] is None
    assert after["outcome"] is None
    assert after["warnings"] == []
    assert after["configured"] is True
    assert after["snapshot"] == before["snapshot"]
    assert after["coverage"] == before["coverage"]
    assert after["progress"] == before["progress"]
    without_notice = {
        key: value for key, value in after.items() if key != "schema_change"
    }
    before_without_notice = {
        key: value for key, value in before.items() if key != "schema_change"
    }
    assert without_notice == before_without_notice
    assert after["schema_change"]["acknowledged"] is True


# ---------------------------------------------------------------------------
# (11) content-free: no object title, status value, or property name leaks
# ---------------------------------------------------------------------------


def test_the_route_bodies_carry_no_free_text_from_the_sidecar(tmp_path):
    store = _store_with(
        tmp_path,
        diff=_diff(
            counts={**_diff()["counts"], "options_added": SENTINEL_VALUE},
            structures_added=[SENTINEL_TITLE],
            properties_renamed=[[PRIMARY, SENTINEL_PROPERTY]],
        ),
        summary=SUMMARY,
    )
    client = _client(tmp_path)

    status_response = client.get(STATUS_URL)
    ack_response = client.post(
        ACK_URL, headers=_auth(client), json={"to_hash": TO_HASH}
    )

    assert status_response.status_code == 200
    assert ack_response.status_code == 200
    for response in (status_response, ack_response):
        text = json.dumps(response.json())
        assert SENTINEL_TITLE not in text
        assert SENTINEL_VALUE not in text
        assert SENTINEL_PROPERTY not in text
    notice = status_response.json()["schema_change"]
    allowed = {SUMMARY, TO_HASH, FROM_HASH, DETECTED_AT, "info", "warn"}
    for path, leaf in _string_leaves(notice):
        assert leaf in allowed, path
    # A non-integer count never reaches the UI as free text.
    assert notice["diff"]["counts"]["options_added"] == 0
    assert store.load_schema_change()["acknowledged"] is True
