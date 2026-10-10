"""U3c-1 — durable per-identity Capacities selections plus the pure resolver.

Test-first: this file was written before ``app/capacities_selections.py``
existed so the red state is an import failure, then each test failed for its
stated reason against a no-op skeleton, then implemented to green.

Everything here is fixture-only — no provider, credential, vault, network, or
real ``$HOME`` read. The autouse ``_isolated_app_home`` fixture (conftest) puts
``TDTB_HOME`` in a per-test tmp dir, so the store under test never touches the
operator's machine state.
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "gather"))

import app_config  # noqa: E402
import capacities_adapter as ca  # noqa: E402
import capacities_builder as cb  # noqa: E402
import capacities_refresh_state as crs  # noqa: E402
import capacities_rules as cr  # noqa: E402
import capacities_selections as cs  # noqa: E402
import exclusion_settings as es  # noqa: E402
import main as main_mod  # noqa: E402
import runstate  # noqa: E402
import tdtb_gather as gather  # noqa: E402

SPACE = "space-1"
OTHER_SPACE = "space-2"
IDENTITY = f"capacities:{SPACE}:Project:obj-1"
IDENTITY_2 = f"capacities:{SPACE}:Project:obj-2"


def _row(
    identity: str,
    *,
    rule_state: str = "match",
    rule_revision: int = 0,
    completion_state: str = "open",
    reasons: tuple[str, ...] = (),
) -> dict:
    """A cached server row shaped like the adapter's output.

    Only keys the adapter actually emits are used; ``source_fingerprint`` is
    present on real rows but deliberately unused (the selection is
    identity-only)."""
    return {
        "id": "Write the thing",
        "name": "Write the thing",
        "path": "/tasks/obj-1",
        "identity": identity,
        "source": "capacities",
        "types": ["Project"],
        "assigned": True,
        "source_fingerprint": "fingerprint-obj-1",
        "capacities_rule": {"state": rule_state, "revision": rule_revision},
        "capacities_completion_state": completion_state,
        "capacities_review_reasons": list(reasons),
    }


def _entry(identity: str, *, rules_revision: int = 0, acknowledged: bool = False) -> dict:
    return {
        "identity": identity,
        "rules_revision": rules_revision,
        "acknowledged": acknowledged,
    }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def test_save_requires_canonical_identity_and_bumps_revision_with_conflict():
    with pytest.raises(cs.SelectionValidationError):
        cs.save_selections(
            space_id=SPACE,
            expected_revision=0,
            selections=[_entry("not-an-identity")],
        )
    assert not cs.selections_path().exists()

    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[_entry(IDENTITY, rules_revision=1, acknowledged=True)],
    )
    assert saved.revision == 1
    assert saved.selections == (cs.SelectionRecord(IDENTITY, 1, True),)
    assert cs.load_selections(SPACE) == saved

    target = cs.selections_path()
    before = target.read_bytes()
    with pytest.raises(cs.SelectionConflict) as caught:
        cs.save_selections(
            space_id=SPACE,
            expected_revision=0,
            selections=[_entry(IDENTITY)],
        )
    assert caught.value.expected_revision == 0
    assert caught.value.current_revision == 1
    assert target.read_bytes() == before


def test_forged_identity_is_rejected_on_save():
    forged = [
        "Write the thing",                       # a title
        "/vault/notes/obj-1.md",                 # a path
        "obj-1",                                 # a bare object id
        "capacities_id",                         # a capacities_* field name
        " Capacities:space-1:Project:obj-1",     # non-canonical casing/space
        f"capacities:{OTHER_SPACE}:Project:obj-1",  # a different space
    ]
    for value in forged:
        with pytest.raises(cs.SelectionValidationError):
            cs.save_selections(
                space_id=SPACE,
                expected_revision=0,
                selections=[_entry(value)],
            )
    assert not cs.selections_path().exists()


def test_save_rejects_non_bool_acknowledged_and_non_int_revision():
    with pytest.raises(cs.SelectionValidationError):
        cs.save_selections(
            space_id=SPACE,
            expected_revision=0,
            selections=[_entry(IDENTITY, acknowledged=1)],
        )
    with pytest.raises(cs.SelectionValidationError):
        cs.save_selections(
            space_id=SPACE,
            expected_revision=0,
            selections=[_entry(IDENTITY, rules_revision=True)],
        )
    with pytest.raises(cs.SelectionValidationError):
        cs.save_selections(
            space_id=SPACE,
            expected_revision=0,
            selections=[_entry(IDENTITY, rules_revision=-1)],
        )
    with pytest.raises(cs.SelectionValidationError):
        cs.save_selections(
            space_id=SPACE,
            expected_revision=0,
            selections=[_entry(IDENTITY), _entry(IDENTITY)],
        )
    assert not cs.selections_path().exists()


def test_corrupt_store_raises_instead_of_resetting():
    target = cs.selections_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    duplicate = (
        '{"version": 1, "revision": 1, "space_id": "space-1", '
        '"selections": [], "selections": []}'
    )
    target.write_text(duplicate, encoding="utf-8")
    with pytest.raises(cs.SelectionFormatError):
        cs.load_selections(SPACE)
    assert target.read_text(encoding="utf-8") == duplicate

    unknown = json.dumps(
        {
            "version": 1,
            "revision": 1,
            "space_id": SPACE,
            "selections": [_entry(IDENTITY)],
            "extra": True,
        }
    )
    target.write_text(unknown, encoding="utf-8")
    with pytest.raises(cs.SelectionFormatError):
        cs.load_selections(SPACE)
    assert target.read_text(encoding="utf-8") == unknown

    bad_bool = json.dumps(
        {
            "version": 1,
            "revision": 1,
            "space_id": SPACE,
            "selections": [_entry(IDENTITY, acknowledged=1)],
        }
    )
    target.write_text(bad_bool, encoding="utf-8")
    with pytest.raises(cs.SelectionFormatError):
        cs.load_selections(SPACE)
    assert target.read_text(encoding="utf-8") == bad_bool


def test_other_space_document_is_treated_as_absent():
    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[_entry(IDENTITY)],
    )
    assert saved.revision == 1
    target = cs.selections_path()
    before = target.read_bytes()

    foreign = cs.load_selections(OTHER_SPACE)
    assert foreign.space_id == OTHER_SPACE
    assert foreign.revision == 0
    assert foreign.selections == ()
    assert target.read_bytes() == before

    # The foreign-space document is absent for conflict purposes too: the
    # caller's first save into the new space starts from revision 0.
    replaced = cs.save_selections(
        space_id=OTHER_SPACE,
        expected_revision=0,
        selections=[_entry(f"capacities:{OTHER_SPACE}:Project:obj-9")],
    )
    assert replaced.revision == 1
    assert cs.load_selections(OTHER_SPACE).selections == replaced.selections


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

def test_forged_identity_absent_from_cached_rows_is_never_promoted():
    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[_entry(IDENTITY)],
    )
    promoted, notices = cs.resolve_selections(
        saved.selections,
        [],
        rules_revision=0,
        mapped_structures={"Project"},
        hard_excluded=frozenset(),
    )
    assert promoted == []
    assert notices == [{"code": "not_cached", "identity": IDENTITY}]
    # The record stays stored; the resolver never removes it.
    assert cs.load_selections(SPACE).selections == saved.selections


def test_hard_exclusion_removes_selection_with_notice():
    row = _row(IDENTITY)
    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[_entry(IDENTITY)],
    )
    promoted, notices = cs.resolve_selections(
        saved.selections,
        [row],
        rules_revision=0,
        mapped_structures={"Project"},
        hard_excluded=frozenset({IDENTITY}),
    )
    assert promoted == []
    assert notices == [{"code": "excluded", "identity": IDENTITY}]


def test_rule_change_retains_selection_with_warning():
    row = _row(IDENTITY, rule_revision=1)
    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[_entry(IDENTITY, rules_revision=1)],
    )
    promoted, notices = cs.resolve_selections(
        saved.selections,
        [row],
        rules_revision=2,
        mapped_structures={"Project"},
        hard_excluded=frozenset(),
    )
    assert promoted == [row]
    assert promoted[0] is not row
    assert notices == [{"code": "rule_changed", "identity": IDENTITY}]


def test_type_removal_retains_selection_as_disabled_record():
    row = _row(IDENTITY)
    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[_entry(IDENTITY)],
    )
    promoted, notices = cs.resolve_selections(
        saved.selections,
        [row],
        rules_revision=0,
        mapped_structures={"RootTask"},
        hard_excluded=frozenset(),
    )
    assert promoted == []
    assert notices == [{"code": "type_disabled", "identity": IDENTITY}]

    reloaded = cs.load_selections(SPACE)
    assert reloaded.revision == 1
    assert reloaded.selections == saved.selections


def test_unknown_row_selection_keeps_its_unknown_state_and_reasons():
    row = _row(
        IDENTITY,
        rule_state="unknown",
        completion_state="unknown",
        reasons=("rule_unknown", "completion_unknown"),
    )
    snapshot = copy.deepcopy(row)
    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[_entry(IDENTITY)],
    )
    promoted, notices = cs.resolve_selections(
        saved.selections,
        [row],
        rules_revision=0,
        mapped_structures={"Project"},
        hard_excluded=frozenset(),
    )
    assert notices == []
    assert len(promoted) == 1
    assert promoted[0] is not row
    assert promoted[0] == row
    assert promoted[0]["capacities_rule"] == {"state": "unknown", "revision": 0}
    assert promoted[0]["capacities_completion_state"] == "unknown"
    assert promoted[0]["capacities_review_reasons"] == [
        "rule_unknown",
        "completion_unknown",
    ]
    assert row == snapshot


def test_resolver_promotes_in_selection_order_and_stays_silent_when_current():
    rows = [_row(IDENTITY), _row(IDENTITY_2)]
    saved = cs.save_selections(
        space_id=SPACE,
        expected_revision=0,
        selections=[
            _entry(IDENTITY_2, rules_revision=3),
            _entry(IDENTITY, rules_revision=3),
        ],
    )
    promoted, notices = cs.resolve_selections(
        saved.selections,
        rows,
        rules_revision=3,
        mapped_structures={"Project"},
        hard_excluded=frozenset(),
    )
    assert notices == []
    assert promoted == [rows[1], rows[0]]
    assert promoted[0] is not rows[1]
    assert promoted[1] is not rows[0]


# ---------------------------------------------------------------------------
# U4 S4: GET/POST /capacities/selections (route level).
#
# The browser supplies only identities and flags. The server validates every
# identity against the S1-projected direct candidates, applies the shared
# hard exclusions, and stamps the rules revision itself. Neither route calls a
# provider, reads a credential, or writes Capacities.
# ---------------------------------------------------------------------------

ROOT = "RootTask"
CUSTOM = "T2"
TAG_A = "5a25370b-f9a0-40cf-bc3a-0cab4744913c"
DIRECT_TS = datetime(2026, 10, 10, 12, 0).timestamp()
SELECTIONS_URL = "/capacities/selections"


def _ident(object_id: str, structure: str = ROOT, *, space: str = SPACE) -> str:
    return f"capacities:{space}:{structure}:{object_id}"


@pytest.fixture
def vault(tmp_path) -> Path:
    root = tmp_path / "vault-root"
    root.mkdir()
    return root


@pytest.fixture
def client(vault) -> TestClient:
    c = TestClient(main_mod.create_app(vault_root=vault))
    c.app_token = c.app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _contract() -> list[dict]:
    properties = [
        {"id": "title", "type": "title"},
        {"id": "assigned", "type": "boolean"},
        {"id": "status", "type": "label", "labelSet": [
            {"id": "active", "name": "Active"}, {"id": "done", "name": "Done"}]},
        {"id": "due", "type": "date"},
        {"id": "completion", "type": "label", "writable": True, "labelSet": [
            {"id": "done", "name": "Done"}]},
    ]
    return [
        {"id": type_id, "title": type_id, "propertyDefinitions": properties}
        for type_id in (ROOT, CUSTOM)
    ]


def _save_source(vault: Path) -> None:
    common = dict(
        title_property="title",
        status_property="status",
        open_status_values=("active",),
        date_property="due",
        assignment_property="assigned",
        assignment_values=("true",),
    )
    cb.save_source(
        vault,
        expected_revision=0,
        space_id=SPACE,
        structures=[
            cb.SourceStructureRecord(structure_id=ROOT, **common),
            cb.SourceStructureRecord(
                structure_id=CUSTOM,
                completion_property="completion",
                completion_value="done",
                **common,
            ),
        ],
    )


def _object(object_id: str, structure: str = ROOT, *, completion=None, tags=None) -> dict:
    properties = {
        "title": {"type": "title", "title": {"value": object_id}},
        "assigned": {"type": "boolean", "boolean": True},
        "status": {"type": "label", "label": [{"id": "active", "name": "Active"}]},
    }
    if completion is not None:
        properties["completion"] = {
            "type": "label", "label": [{"id": completion, "name": completion}],
        }
    if tags is not None:
        properties["tags"] = {
            "type": "entity",
            "entity": [{"id": tag, "title": "tag"} for tag in tags],
        }
    return {"id": object_id, "structureId": structure, "properties": properties}


def _publish(vault: Path, objects: list[dict]) -> None:
    """Install one complete generation; every configured type is checked."""
    store = cb.build_refresh_state(vault, SPACE)
    listed = {type_id: [] for type_id in (ROOT, CUSTOM)}
    for obj in objects:
        store.put(obj["id"], obj["structureId"], obj)
        listed[obj["structureId"]].append(obj["id"])
    evidence = crs.SnapshotEvidence(
        scope_key="all",
        revision=1,
        required_types=tuple(listed),
        listings=tuple(
            crs.TypeListing(
                type_key=type_id,
                object_ids=tuple(ids),
                listing_checked_at=DIRECT_TS,
            )
            for type_id, ids in listed.items()
        ),
    )
    store.install_generation(evidence, expected_generation=0, structures=_contract())


def _ready(vault: Path, objects: list[dict]) -> None:
    """Direct intake, a saved source, and one published generation."""
    path = app_config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "sources": {"capacities_intake": "direct"}}),
        encoding="utf-8",
    )
    _save_source(vault)
    _publish(vault, objects)


STANDARD = [
    _object("obj-a"),
    # No completion value: UNKNOWN, carries completion_unknown (needs acknowledgement).
    _object("obj-unknown", CUSTOM),
]


def _select(client, *, expected=0, select=(), deselect=(), headers=None):
    """POST /capacities/selections. ``select`` holds ``(identity, acknowledge)`` pairs."""
    body = {
        "expected_revision": expected,
        "select": [
            {"identity": identity, "acknowledge": ack} for identity, ack in select
        ],
        "deselect": list(deselect),
    }
    return client.post(
        SELECTIONS_URL,
        headers=_auth(client) if headers is None else headers,
        json=body,
    )


def _seed(*records, expected=0) -> None:
    """Seed the store directly: ``(identity, rules_revision, acknowledged)`` triples."""
    cs.save_selections(
        space_id=SPACE,
        expected_revision=expected,
        selections=[
            {"identity": identity, "rules_revision": rules, "acknowledged": ack}
            for identity, rules, ack in records
        ],
    )


def _stored() -> dict:
    return {record.identity: record for record in cs.load_selections(SPACE).selections}


class TestSelectionsGet:
    def test_no_source_record_is_the_documented_empty_shape(self, client, vault):
        response = client.get(SELECTIONS_URL)

        assert response.status_code == 200
        assert response.json() == {
            "space_id": None,
            "revision": 0,
            "rules_revision": None,
            "selections": [],
        }
        assert not cs.selections_path().exists()

    def test_source_without_a_store_is_empty_for_that_space(self, client, vault):
        _save_source(vault)

        response = client.get(SELECTIONS_URL)

        assert response.json() == {
            "space_id": SPACE,
            "revision": 0,
            "rules_revision": 0,
            "selections": [],
        }
        assert not cs.selections_path().exists()

    def test_reports_space_revisions_and_each_selection(self, client, vault):
        _save_source(vault)
        _seed((_ident("obj-a"), 7, True))

        response = client.get(SELECTIONS_URL)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"space_id", "revision", "rules_revision", "selections"}
        assert body["space_id"] == SPACE
        assert body["revision"] == 1
        assert body["rules_revision"] == 0
        assert body["selections"] == [
            {"identity": _ident("obj-a"), "acknowledged": True, "rules_revision": 7},
        ]

    def test_is_tokenless_and_answers_under_legacy_intake(self, client, vault):
        # No config.json, so the intake is the legacy default: GET still reads the store.
        _save_source(vault)

        response = client.get(SELECTIONS_URL, headers={"X-TDTB-Token": "wrong-token"})

        assert response.status_code == 200

    def test_foreign_space_store_is_the_empty_shape(self, client, vault):
        _save_source(vault)
        cs.save_selections(
            space_id="space-2",
            expected_revision=0,
            selections=[{
                "identity": _ident("obj-a", space="space-2"),
                "rules_revision": 0,
                "acknowledged": False,
            }],
        )

        response = client.get(SELECTIONS_URL)

        assert response.json() == {
            "space_id": SPACE,
            "revision": 0,
            "rules_revision": 0,
            "selections": [],
        }

    def test_store_failure_is_a_bounded_503_that_keeps_the_bytes(self, client, vault):
        _save_source(vault)
        path = cs.selections_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")

        response = client.get(SELECTIONS_URL)

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "capacities_selections_storage_error"
        assert str(path) not in response.text
        assert str(vault) not in response.text
        assert path.read_bytes() == b"{not json"


class TestSelectionsPostAuth:
    def test_post_requires_the_token(self, client, vault):
        _ready(vault, STANDARD)

        response = _select(client, select=[(_ident("obj-a"), False)], headers={})

        assert response.status_code == 403
        assert not cs.selections_path().exists()

    def test_post_with_a_wrong_token_is_rejected(self, client, vault):
        _ready(vault, STANDARD)

        response = _select(
            client,
            select=[(_ident("obj-a"), False)],
            headers={"X-TDTB-Token": "wrong-token"},
        )

        assert response.status_code == 403
        assert not cs.selections_path().exists()


class TestSelectionsPostIntake:
    def test_legacy_intake_answers_422_and_writes_nothing(self, client, vault):
        _save_source(vault)  # no config.json: legacy default

        response = _select(client, select=[(_ident("obj-a"), False)])

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "capacities_intake_not_direct"
        assert not cs.selections_path().exists()


class TestSelectionsPostValidation:
    @pytest.mark.parametrize("forged", [
        "Write the thing",
        "/vault/notes/obj-a.md",
        "obj-a",
        "Capacities:space-1:RootTask:obj-a",
        "capacities:space-1:RootTask:obj-a ",
    ])
    def test_forged_identity_is_rejected_and_named(self, client, vault, forged):
        _ready(vault, STANDARD)

        response = _select(client, select=[(forged, False)])

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "invalid_identity"
        assert detail["identity"] == forged
        assert not cs.selections_path().exists()

    def test_cross_space_identity_is_rejected_and_named(self, client, vault):
        _ready(vault, STANDARD)
        identity = _ident("obj-a", space="space-2")

        response = _select(client, select=[(identity, False)])

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_identity"
        assert response.json()["detail"]["identity"] == identity
        assert not cs.selections_path().exists()

    def test_unknown_object_is_not_cached_and_named(self, client, vault):
        _ready(vault, STANDARD)
        identity = _ident("obj-missing")

        response = _select(client, select=[(identity, False)])

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "not_cached"
        assert response.json()["detail"]["identity"] == identity
        assert not cs.selections_path().exists()

    def test_completed_object_never_reaches_the_candidate_surface(self, client, vault):
        # The adapter drops a closed (completed) object before the candidate list,
        # so selecting it is not_cached, not excluded.
        _ready(vault, [_object("obj-done", CUSTOM, completion="done")])
        identity = _ident("obj-done", CUSTOM)

        response = _select(client, select=[(identity, False)])

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "not_cached"
        assert response.json()["detail"]["identity"] == identity

    def test_unmapped_structure_is_type_disabled_and_named(self, client, vault):
        _ready(vault, STANDARD)
        identity = _ident("obj-x", structure="Project")

        response = _select(client, select=[(identity, False)])

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "type_disabled"
        assert response.json()["detail"]["identity"] == identity
        assert not cs.selections_path().exists()

    def test_tag_excluded_identity_is_refused_and_writes_nothing(self, client, vault):
        # The adapter drops tag-excluded rows before the candidate list, so the
        # identity is refused as a non-candidate. The code is pinned below.
        es.save_settings(vault, expected_revision=0, exclusions=[
            {"source": "capacities", "space_id": SPACE, "tag_id": TAG_A},
        ])
        _ready(vault, [_object("obj-tagged", tags=[TAG_A])])
        identity = _ident("obj-tagged")

        response = _select(client, select=[(identity, False)])

        assert response.status_code == 409
        assert response.json()["detail"]["identity"] == identity
        assert not cs.selections_path().exists()

    def test_tag_excluded_identity_is_refused_and_named(self, client, vault):
        """A tag-excluded identity is refused as not_cached, not excluded.

        Settled by root (S4 report): load_direct_rows applies the tag policy
        inside the adapter, so a tag-excluded row never reaches the endpoint
        as a candidate; the refusal code is ``not_cached``. The safety
        outcome is identical - the write is refused with the identity named -
        and the ``excluded`` code still fires for today's drop list. A
        pre-exclusion candidate surface is a possible later refinement,
        recorded as a residual, not a defect.
        """
        es.save_settings(vault, expected_revision=0, exclusions=[
            {"source": "capacities", "space_id": SPACE, "tag_id": TAG_A},
        ])
        _ready(vault, [_object("obj-tagged", tags=[TAG_A])])

        response = _select(client, select=[(_ident("obj-tagged"), False)])

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "not_cached"
        assert response.json()["detail"]["identity"] == _ident("obj-tagged")
        assert not cs.selections_path().exists()

    def test_dropped_today_is_excluded_and_named(self, client, vault):
        _ready(vault, STANDARD)
        today = gather.effective_date(datetime.now())

        def _drop(state):
            state["dropped"] = [{
                "identity": _ident("obj-a"),
                "dropped_at": "2026-10-09T09:00:00-07:00",
            }]

        runstate.update_runstate(vault, today, _drop)

        response = _select(client, select=[(_ident("obj-a"), False)])

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "excluded"
        assert response.json()["detail"]["identity"] == _ident("obj-a")
        assert not cs.selections_path().exists()

    def test_review_candidate_without_acknowledgement_is_rejected(self, client, vault):
        _ready(vault, STANDARD)
        identity = _ident("obj-unknown", CUSTOM)

        response = _select(client, select=[(identity, False)])

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "acknowledgement_required"
        assert response.json()["detail"]["identity"] == identity
        assert not cs.selections_path().exists()

    def test_review_candidate_with_acknowledgement_is_stored(self, client, vault):
        _ready(vault, STANDARD)
        identity = _ident("obj-unknown", CUSTOM)

        response = _select(client, select=[(identity, True)])

        assert response.status_code == 200
        assert response.json()["selections"] == [
            {"identity": identity, "acknowledged": True, "rules_revision": 0},
        ]
        assert _stored()[identity].acknowledged is True

    def test_clear_candidate_needs_no_acknowledgement(self, client, vault):
        _ready(vault, STANDARD)

        response = _select(client, select=[(_ident("obj-a"), False)])

        assert response.status_code == 200
        assert _stored()[_ident("obj-a")].acknowledged is False

    def test_duplicate_identity_in_one_request_is_rejected(self, client, vault):
        _ready(vault, STANDARD)
        identity = _ident("obj-a")

        response = _select(client, select=[(identity, False), (identity, True)])

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "duplicate_identity"
        assert response.json()["detail"]["identity"] == identity
        assert not cs.selections_path().exists()

    def test_stale_revision_is_a_409_with_both_revisions(self, client, vault):
        _ready(vault, STANDARD)
        first = _select(client, expected=0, select=[(_ident("obj-a"), False)])
        assert first.status_code == 200
        assert first.json()["revision"] == 1
        before = cs.selections_path().read_bytes()

        stale = _select(
            client, expected=0, select=[(_ident("obj-unknown", CUSTOM), True)],
        )

        assert stale.status_code == 409
        detail = stale.json()["detail"]
        assert detail["code"] == "capacities_selections_conflict"
        assert detail["expected_revision"] == 0
        assert detail["current_revision"] == 1
        assert cs.selections_path().read_bytes() == before

    def test_unevaluable_tags_refuse_the_candidate_and_write_nothing(self, client, vault):
        es.save_settings(vault, expected_revision=0, exclusions=[
            {"source": "capacities", "space_id": SPACE, "tag_id": TAG_A},
        ])
        malformed = _object("obj-bad-tags")
        malformed["properties"]["tags"] = {"type": "text", "text": "tag"}
        _ready(vault, [malformed])

        response = _select(client, select=[(_ident("obj-bad-tags"), False)])

        # load_direct_rows refuses the whole projection on TagExclusionBlocked, so
        # the candidate is absent and the selection is not_cached.
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "not_cached"
        assert not cs.selections_path().exists()


class TestSelectionsPostMerge:
    def test_merge_keeps_untouched_records_and_stamps_only_the_new_one(self, client, vault):
        _ready(vault, STANDARD)
        _seed((_ident("obj-a"), 7, False))

        response = _select(
            client, expected=1, select=[(_ident("obj-unknown", CUSTOM), True)],
        )

        assert response.status_code == 200
        body = response.json()
        assert body["revision"] == 2
        records = {record["identity"]: record for record in body["selections"]}
        assert records[_ident("obj-a")] == {
            "identity": _ident("obj-a"), "acknowledged": False, "rules_revision": 7,
        }
        assert records[_ident("obj-unknown", CUSTOM)] == {
            "identity": _ident("obj-unknown", CUSTOM),
            "acknowledged": True,
            "rules_revision": 0,
        }

    def test_reselect_is_idempotent_and_never_duplicates(self, client, vault):
        _ready(vault, STANDARD)
        _seed((_ident("obj-a"), 7, False))

        response = _select(client, expected=1, select=[(_ident("obj-a"), False)])

        assert response.status_code == 200
        selections = response.json()["selections"]
        assert [record["identity"] for record in selections] == [_ident("obj-a")]
        assert selections[0]["rules_revision"] == 0
        assert response.json()["revision"] == 2

    def test_deselect_then_reselect_in_one_call_leaves_one_current_record(self, client, vault):
        _ready(vault, STANDARD)
        _seed((_ident("obj-a"), 7, True))

        response = _select(
            client,
            expected=1,
            select=[(_ident("obj-a"), False)],
            deselect=[_ident("obj-a")],
        )

        assert response.status_code == 200
        assert response.json()["selections"] == [
            {"identity": _ident("obj-a"), "acknowledged": False, "rules_revision": 0},
        ]

    def test_deselect_removes_only_the_named_record(self, client, vault):
        _ready(vault, STANDARD)
        _seed((_ident("obj-a"), 7, False), (_ident("obj-unknown", CUSTOM), 7, True))

        response = _select(client, expected=1, deselect=[_ident("obj-a")])

        assert response.status_code == 200
        assert response.json()["selections"] == [
            {"identity": _ident("obj-unknown", CUSTOM), "acknowledged": True, "rules_revision": 7},
        ]
        assert response.json()["revision"] == 2

    def test_deselect_of_an_unselected_identity_is_a_no_op(self, client, vault):
        _ready(vault, STANDARD)

        response = _select(client, expected=0, deselect=[_ident("obj-a")])

        assert response.status_code == 200
        assert response.json()["selections"] == []
        assert response.json()["revision"] == 1


class TestSelectionsPostRulesRevision:
    def test_client_supplied_rules_revision_is_never_accepted(self, client, vault):
        _ready(vault, STANDARD)
        entry = {"identity": _ident("obj-a"), "acknowledge": False, "rules_revision": 999}

        nested = client.post(
            SELECTIONS_URL,
            headers=_auth(client),
            json={"expected_revision": 0, "select": [entry], "deselect": []},
        )
        top_level = client.post(
            SELECTIONS_URL,
            headers=_auth(client),
            json={
                "expected_revision": 0,
                "select": [{"identity": _ident("obj-a"), "acknowledge": False}],
                "deselect": [],
                "rules_revision": 999,
            },
        )

        assert nested.status_code == 422
        assert top_level.status_code == 422
        assert not cs.selections_path().exists()

    def test_rules_revision_is_stamped_from_the_server_rules(self, client, vault):
        _ready(vault, STANDARD)
        rules_file = cr.rules_path()
        rules_file.parent.mkdir(parents=True, exist_ok=True)
        rules_file.write_text(
            json.dumps(cr.RulesRecord(space_id=SPACE, revision=5).as_dict()),
            encoding="utf-8",
        )

        response = _select(client, select=[(_ident("obj-a"), True)])

        assert response.status_code == 200
        assert response.json()["rules_revision"] == 5
        assert response.json()["selections"][0]["rules_revision"] == 5


class TestSelectionsPostStore:
    def test_corrupt_store_is_a_bounded_503_and_keeps_the_bytes(self, client, vault):
        _ready(vault, STANDARD)
        path = cs.selections_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")

        response = _select(client, select=[(_ident("obj-a"), False)])

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "capacities_selections_storage_error"
        assert str(path) not in response.text
        assert path.read_bytes() == b"{not json"

    def test_save_failure_is_a_bounded_503_without_paths(self, client, vault, monkeypatch):
        _ready(vault, STANDARD)

        def _boom(**_kwargs):
            raise cs.SelectionStoreError("write failed at /private/var/secret")

        monkeypatch.setattr(cs, "save_selections", _boom)

        response = _select(client, select=[(_ident("obj-a"), False)])

        assert response.status_code == 503
        assert "/private/var/secret" not in response.text
        assert str(vault) not in response.text


class TestSelectionsNoProvider:
    def test_both_routes_never_reach_a_provider_or_a_credential(
        self, client, vault, monkeypatch
    ):
        _ready(vault, STANDARD)
        calls: list[str] = []

        def _forbidden(label):
            def _fail(*_args, **_kwargs):
                calls.append(label)
                raise AssertionError(f"{label} must not be called")
            return _fail

        client.app.state.build_capacities_adapter = _forbidden("app adapter seam")
        # Local stores (the published generation, the assignment settings) are
        # read by design; only the provider and credential seams are poisoned.
        monkeypatch.setattr(cb, "build_capacities_adapter", _forbidden("builder"))
        monkeypatch.setattr(cb, "load_capacities_token", _forbidden("credential read"))
        monkeypatch.setattr(ca.CapacitiesRestClient, "__init__", _forbidden("provider client"))
        monkeypatch.setattr(
            main_mod.external_sources, "fetch_capacities_items", _forbidden("provider fetch"),
        )

        assert client.get(SELECTIONS_URL).status_code == 200
        assert _select(client, select=[(_ident("obj-a"), False)]).status_code == 200
        assert _select(client, expected=1, deselect=[_ident("obj-a")]).status_code == 200
        assert calls == []
