"""U5 backend — the /capacities/rules route seam and the /plan-inputs opt-in
metadata read.

Fixture-only: the autouse ``_isolated_app_home`` fixture (conftest) puts
``TDTB_HOME`` in a per-test tmp dir, so no real machine state, vault, provider,
credential, or network is touched. Every rule, fallback, and prompt value is
synthetic.

Covers the two approved backend gaps:

- ``/plan-inputs`` exposes the persisted opt-in metadata top-level as
  ``prompt_optins = {optins, revision}`` (the shape the frontend's
  ``projectPromptOptins`` reads), and a corrupt store omits the block with a
  bounded, content-free warning rather than a fabricated all-false read.
- ``GET/POST /capacities/rules`` reuse ``capacities_rules``' own active/draft +
  optimistic-revision store and the published generation's structure contract
  for offline schema validation: a valid rule activates, an invalid one stays a
  draft with the previous active preserved, an unknown type is refused, and no
  provider or credential is ever read.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import app_config  # noqa: E402
import capacities_builder as cb  # noqa: E402
import capacities_refresh_state as crs  # noqa: E402
import capacities_rules as cr  # noqa: E402
import main as main_mod  # noqa: E402
import prompt_state  # noqa: E402

SPACE = "space-1"
TYPE = "custom-project"
RULES_URL = "/capacities/rules"
DIRECT_TS = 1_000_000.0

VALID_RULE = {"all": [{"prop": "status", "op": "eq", "values": ["active"]}]}
# The store deliberately adds no user regex execution (KTD5).
REGEX_RULE = {"prop": "status", "op": "matches", "values": ["a.*"]}
REMOVED_PROP_RULE = {"prop": "priority", "op": "eq", "values": ["high"]}


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
        {"id": "status", "type": "label", "labelSet": [
            {"id": "active", "name": "Active"}, {"id": "done", "name": "Done"}]},
        {"id": "minutes", "type": "number"},
        {"id": "due", "type": "date"},
    ]
    return [{"id": TYPE, "title": TYPE, "propertyDefinitions": properties}]


def _save_source(vault: Path) -> None:
    cb.save_source(
        vault,
        expected_revision=0,
        space_id=SPACE,
        structures=[
            cb.SourceStructureRecord(
                structure_id=TYPE,
                title_property="title",
                status_property="status",
                open_status_values=("active",),
                date_property="due",
                duration_property="minutes",
            ),
        ],
    )


def _publish_contract(vault: Path, *, object_ids: tuple[str, ...] = ()) -> None:
    """Install one complete generation carrying the structure contract.

    No cached object content is written, so a valid rule must activate from the
    contract schema alone — an insufficient cached VALUE never blocks
    activation."""
    store = cb.build_refresh_state(vault, SPACE)
    evidence = crs.SnapshotEvidence(
        scope_key="all",
        revision=1,
        required_types=(TYPE,),
        listings=(
            crs.TypeListing(
                type_key=TYPE,
                object_ids=object_ids,
                listing_checked_at=DIRECT_TS,
            ),
        ),
    )
    store.install_generation(evidence, expected_generation=0, structures=_contract())


def _post(
    client,
    *,
    structure_id=TYPE,
    rule=VALID_RULE,
    fallback_minutes=None,
    expected_revision=0,
    headers=None,
):
    body = {
        "structure_id": structure_id,
        "rule": rule,
        "fallback_minutes": fallback_minutes,
        "expected_revision": expected_revision,
    }
    return client.post(RULES_URL, json=body, headers=headers or _auth(client))


# ---------------------------------------------------------------------------
# /plan-inputs prompt opt-in metadata
# ---------------------------------------------------------------------------

def test_plan_inputs_exposes_persisted_optins_metadata(client):
    prompt_state.save_optins(
        expected_revision=0,
        optins={"intention": True, "megan_nicety": False, "stoic_intention": True},
    )
    body = client.get("/plan-inputs").json()
    assert body["prompt_optins"] == {
        "optins": {
            "intention": True,
            "megan_nicety": False,
            "stoic_intention": True,
        },
        "revision": 1,
    }
    assert not any("opt-in" in w.lower() for w in body["source_warnings"])


def test_plan_inputs_absent_optins_is_all_false_revision_zero(client):
    body = client.get("/plan-inputs").json()
    assert body["prompt_optins"] == {
        "optins": {
            "intention": False,
            "megan_nicety": False,
            "stoic_intention": False,
        },
        "revision": 0,
    }


def test_plan_inputs_persisted_optins_survive_restart(vault):
    prompt_state.save_optins(
        expected_revision=0,
        optins={
            "intention": True,
            "megan_nicety": False,
            "stoic_intention": False,
        },
    )
    # A fresh app instance (a restart) reads the same machine-local store.
    restarted = TestClient(main_mod.create_app(vault_root=vault))
    meta = restarted.get("/plan-inputs").json()["prompt_optins"]
    assert meta["optins"]["intention"] is True
    assert meta["revision"] == 1


def test_corrupt_optins_are_unavailable_with_a_bounded_warning(client):
    path = prompt_state.optins_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    body = client.get("/plan-inputs").json()
    # The block is OMITTED so the UI shows prefs unavailable, never all-false.
    assert "prompt_optins" not in body
    assert any("opt-in" in w.lower() for w in body["source_warnings"])
    # Content-free: no raw bytes or path leak into the response.
    assert "{not json" not in json.dumps(body)
    assert str(path) not in json.dumps(body)


# ---------------------------------------------------------------------------
# GET /capacities/rules — read contract
# ---------------------------------------------------------------------------

def test_rules_get_is_tokenless_and_documents_the_read_contract(client, vault):
    _save_source(vault)
    _publish_contract(vault)
    response = client.get(RULES_URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["space_id"] == SPACE
    assert body["configured"] is True
    assert body["revision"] == 0
    caps = body["capabilities"]
    assert caps["matches"] is False
    assert "matches" not in caps["ops"]
    assert set(caps["ops"]) == set(cr.OPS)
    assert caps["schema_source"] == "structure_contract"
    assert caps["contract_available"] is True
    entry = body["structures"][0]
    assert entry["structure_id"] == TYPE
    assert entry["mapped"] is True
    assert entry["active"] is None
    assert entry["draft"] is None
    assert entry["fallback_minutes"] is None
    assert entry["schema_available"] is True
    assert entry["schema"] == {
        "title": "title",
        "status": "label",
        "minutes": "number",
        "due": "date",
    }


def test_rules_get_without_a_source_is_the_empty_shape(client):
    body = client.get(RULES_URL).json()
    assert body["space_id"] is None
    assert body["configured"] is False
    assert body["structures"] == []
    assert body["revision"] == 0
    assert body["capabilities"]["contract_available"] is False


def test_rules_get_without_a_published_contract_reports_schema_unavailable(client, vault):
    _save_source(vault)
    body = client.get(RULES_URL).json()
    entry = body["structures"][0]
    assert entry["schema_available"] is False
    assert entry["schema"] == {}
    assert body["capabilities"]["contract_available"] is False


# ---------------------------------------------------------------------------
# POST /capacities/rules — token, conflict, activation, drafts
# ---------------------------------------------------------------------------

def test_rules_post_requires_the_token(client, vault):
    _save_source(vault)
    _publish_contract(vault)
    response = client.post(RULES_URL, json={
        "structure_id": TYPE,
        "rule": VALID_RULE,
        "fallback_minutes": None,
        "expected_revision": 0,
    }, headers={})
    assert response.status_code == 403
    assert not cr.rules_path().exists()


def test_rules_post_with_a_wrong_token_is_rejected(client, vault):
    _save_source(vault)
    _publish_contract(vault)
    response = _post(client, headers={"X-TDTB-Token": "wrong-token"})
    assert response.status_code == 403
    assert not cr.rules_path().exists()


def test_valid_rule_becomes_active_with_its_fallback(client, vault):
    _save_source(vault)
    _publish_contract(vault)
    response = _post(client, rule=VALID_RULE, fallback_minutes=45)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["save"]["valid"] is True
    assert body["save"]["active"] == VALID_RULE
    assert body["save"]["fallback_minutes"] == 45
    assert body["save"]["revision"] == 1
    assert body["revision"] == 1
    entry = body["structures"][0]
    assert entry["active"] == VALID_RULE
    assert entry["draft"] == VALID_RULE
    assert entry["fallback_minutes"] == 45
    stored = cr.load_rules(SPACE).structure(TYPE)
    assert stored.active == VALID_RULE
    assert stored.fallback_minutes == 45


def test_insufficient_cached_values_do_not_prevent_activation(client, vault):
    _save_source(vault)
    # The contract is installed with no listed/cached objects at all: the
    # schema comes from the published contract, so zero cached VALUES cannot
    # block a valid activation.
    _publish_contract(vault)
    snapshot = cb.build_refresh_state(vault, SPACE).load_snapshot("all")
    assert snapshot is not None and snapshot.members == ()
    response = _post(client, rule=VALID_RULE)
    assert response.status_code == 200, response.text
    assert response.json()["save"]["valid"] is True


def test_regex_rule_is_rejected_and_stays_a_draft(client, vault):
    _save_source(vault)
    _publish_contract(vault)
    response = _post(client, rule=REGEX_RULE)
    assert response.status_code == 200, response.text
    save = response.json()["save"]
    assert save["valid"] is False
    assert "matches" in (save["reason"] or "")
    assert save["active"] is None
    assert save["draft"] == REGEX_RULE


def test_invalid_rule_preserves_the_previous_active(client, vault):
    _save_source(vault)
    _publish_contract(vault)
    assert _post(client, rule=VALID_RULE, expected_revision=0).json()["save"]["valid"]
    response = _post(client, rule=REGEX_RULE, expected_revision=1)
    save = response.json()["save"]
    assert save["valid"] is False
    assert save["active"] == VALID_RULE  # previous active preserved
    assert save["draft"] == REGEX_RULE
    assert response.json()["structures"][0]["active"] == VALID_RULE


def test_rule_referencing_a_removed_property_stays_a_draft(client, vault):
    _save_source(vault)
    _publish_contract(vault)
    response = _post(client, rule=REMOVED_PROP_RULE)
    save = response.json()["save"]
    assert save["valid"] is False
    assert "priority" in (save["reason"] or "")
    assert save["active"] is None
    assert save["draft"] == REMOVED_PROP_RULE


def test_no_published_schema_saves_a_draft_honestly(client, vault):
    _save_source(vault)  # no published generation → no schema to validate against
    response = _post(client, rule=VALID_RULE)
    assert response.status_code == 200, response.text
    save = response.json()["save"]
    assert save["valid"] is False
    assert "schema" in (save["reason"] or "").lower()
    assert save["active"] is None
    assert save["draft"] == VALID_RULE


def test_unknown_or_foreign_structure_is_rejected(client, vault):
    _save_source(vault)
    _publish_contract(vault)
    response = _post(client, structure_id="not-mapped")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "capacities_rules_unknown_structure"
    assert not cr.rules_path().exists()


def test_stale_revision_is_a_409_with_both_revisions(client, vault):
    _save_source(vault)
    _publish_contract(vault)
    assert _post(client, expected_revision=0).status_code == 200
    response = _post(client, expected_revision=0)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "capacities_rules_conflict"
    assert detail["expected_revision"] == 0
    assert detail["current_revision"] == 1


# ---------------------------------------------------------------------------
# Fail-closed boundaries and no live reads
# ---------------------------------------------------------------------------

def test_source_read_failure_fails_closed(client, vault):
    path = cb.source_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert client.get(RULES_URL).status_code == 503
    assert _post(client).status_code == 503
    # The offending bytes are preserved, never repaired.
    assert path.read_text(encoding="utf-8") == "{not json"


def test_corrupt_rules_store_fails_closed(client, vault):
    _save_source(vault)
    _publish_contract(vault)
    path = cr.rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert client.get(RULES_URL).status_code == 503
    # A malformed existing store is a 409 on save (the settings/source family
    # convention) and the bytes are preserved.
    assert _post(client).status_code == 409
    assert path.read_text(encoding="utf-8") == "{not json"


def test_rules_routes_never_read_a_credential_or_call_a_provider(
    client, vault, monkeypatch
):
    _save_source(vault)
    _publish_contract(vault)

    def boom(*_args, **_kwargs):
        raise AssertionError("no provider/credential access expected")

    monkeypatch.setattr(cb, "load_capacities_token", boom)
    monkeypatch.setattr(client.app.state, "build_capacities_adapter", boom)
    monkeypatch.setattr(client.app.state, "build_refresh_coordinator", boom)

    assert client.get(RULES_URL).status_code == 200
    assert _post(client).status_code == 200


def test_rule_save_bumps_the_combined_refresh_revision(client, vault):
    """The existing coordinator staleness contract sees a rules save."""
    _save_source(vault)
    _publish_contract(vault)
    before = cb.refresh_config_revision(vault)
    assert _post(client, fallback_minutes=30).status_code == 200
    after = cb.refresh_config_revision(vault)
    assert after == before + 1
