"""Route tests for the local Capacities source-mapping API.

GET /settings/capacities/source is a tokenless local read; POST
/settings/capacities/source/save is token-guarded and full-replacement. No
test here builds an adapter, reads a credential, or contacts a provider —
the no-provider test proves that with poisoned spies.
"""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import main as main_mod  # noqa: E402
import capacities_adapter as ca  # noqa: E402
import capacities_builder as cb  # noqa: E402
import artifact_source as art  # noqa: E402
import capacities_selections as sel  # noqa: E402
import exclusion_settings as es  # noqa: E402
import runstate  # noqa: E402


SPACE = "space-1"

VALIDATION_ERROR = {
    "code": "capacities_source_validation_error",
    "message": "Capacities source mapping payload is invalid.",
}


@pytest.fixture
def vault(tmp_path) -> Path:
    root = tmp_path / "vault-root"
    root.mkdir()
    return root


@pytest.fixture
def app(vault):
    return main_mod.create_app(vault_root=vault)


@pytest.fixture
def client(app) -> TestClient:
    c = TestClient(app)
    c.app_token = app.state.token
    return c


def _auth(client: TestClient) -> dict:
    return {"X-TDTB-Token": client.app_token}


def _structure(**overrides) -> dict:
    row = {
        "structure_id": "RootTask",
        "title_property": "title",
        "status_property": "status",
        "open_status_values": ["open", "On Hold"],
        "date_property": "date",
        "deadline_property": None,
        "duration_property": None,
        "assignment_property": None,
        "assignment_values": [],
        "completion_property": None,
        "completion_value": None,
    }
    row.update(overrides)
    return row


def _body(expected_revision: int = 0, **overrides) -> dict:
    body = {
        "expected_revision": expected_revision,
        "space_id": SPACE,
        "structures": [_structure()],
    }
    body.update(overrides)
    return body


def _source_bytes(vault: Path) -> bytes:
    return cb.source_path(vault).read_bytes()


class TestGet:
    def test_absent_record_is_null_and_creates_nothing(self, client, vault):
        before = sorted(
            str(p.relative_to(vault)) for p in vault.rglob("*")
        )

        response = client.get("/settings/capacities/source")

        assert response.status_code == 200
        assert response.json() == {"source": None, "persisted": False}
        assert not cb.source_path(vault).exists()
        # A read must not create the record, the lock, or any other path.
        assert not cb.lock_path(vault).exists()
        assert sorted(
            str(p.relative_to(vault)) for p in vault.rglob("*")
        ) == before

    def test_get_is_tokenless(self, client):
        response = client.get(
            "/settings/capacities/source",
            headers={"X-TDTB-Token": "wrong-token"},
        )

        assert response.status_code == 200

    def test_get_returns_the_persisted_record(self, client, vault):
        saved = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        )
        assert saved.status_code == 200

        body = client.get("/settings/capacities/source").json()

        assert body == {"source": saved.json()["source"], "persisted": True}
        assert body["source"]["revision"] == 1

    def test_corrupt_record_fails_closed_and_preserves_bytes(self, client, vault):
        path = cb.source_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")

        response = client.get("/settings/capacities/source")

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["code"] == "capacities_source_storage_error"
        assert "preserved" in detail["message"]
        assert path.read_bytes() == b"{not json"

    def test_unreadable_record_fails_closed(self, client, vault):
        path = cb.source_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()

        response = client.get("/settings/capacities/source")

        assert response.status_code == 500
        assert (
            response.json()["detail"]["code"]
            == "capacities_source_storage_error"
        )

    def test_unresolved_vault_is_503(self, monkeypatch):
        monkeypatch.delenv("TDTB_VAULT_ROOT", raising=False)
        app = main_mod.create_app()

        response = TestClient(app).get("/settings/capacities/source")

        assert response.status_code == 503


class TestPostAuth:
    def test_post_requires_token(self, client, vault):
        response = client.post("/settings/capacities/source/save", json=_body())

        assert response.status_code == 403
        assert not cb.source_path(vault).exists()

    def test_post_wrong_token_is_rejected(self, client, vault):
        response = client.post(
            "/settings/capacities/source/save",
            headers={"X-TDTB-Token": "wrong-token"},
            json=_body(),
        )

        assert response.status_code == 403
        assert not cb.source_path(vault).exists()


class TestPostPersistence:
    def test_round_trip_increments_the_revision_once(self, client, vault):
        response = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is True
        source = body["source"]
        assert source["version"] == 1
        assert source["revision"] == 1
        assert source["space_id"] == SPACE
        assert source["structures"] == [_structure()]

        read_back = client.get("/settings/capacities/source").json()
        assert read_back == {"source": source, "persisted": True}
        assert cb.read_source(vault).as_dict() == source

    def test_a_second_save_increments_the_revision_again(self, client, vault):
        first = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        )
        assert first.json()["source"]["revision"] == 1

        second = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(
                expected_revision=1,
                structures=[_structure(structure_id="Task")],
            ),
        )

        assert second.status_code == 200
        assert second.json()["source"]["revision"] == 2
        # Full replacement: only the new structure survives.
        assert [
            row["structure_id"]
            for row in second.json()["source"]["structures"]
        ] == ["Task"]

    def test_absent_record_is_revision_zero(self, client, vault):
        response = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(expected_revision=3),
        )

        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "capacities_source_conflict",
            "message": "Mapping changed; reload and review.",
            "expected_revision": 3,
            "current_revision": 0,
        }
        assert not cb.source_path(vault).exists()

    def test_stale_revision_conflicts_with_both_revisions(self, client, vault):
        client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        )
        before = _source_bytes(vault)

        response = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(expected_revision=0),
        )

        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "capacities_source_conflict",
            "message": "Mapping changed; reload and review.",
            "expected_revision": 0,
            "current_revision": 1,
        }
        assert _source_bytes(vault) == before

    def test_save_over_a_corrupt_record_is_409_without_erasing(
        self, client, vault
    ):
        path = cb.source_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("garbage", encoding="utf-8")

        response = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        )

        assert response.status_code == 409
        assert (
            response.json()["detail"]["code"]
            == "capacities_source_storage_error"
        )
        assert path.read_bytes() == b"garbage"

    def test_save_write_failure_is_500_and_preserves_bytes(
        self, client, vault, monkeypatch
    ):
        client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        )
        before = _source_bytes(vault)

        def fail_write(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(cb, "_atomic_write_json", fail_write)

        response = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(
                expected_revision=1,
                structures=[_structure(structure_id="Task")],
            ),
        )

        assert response.status_code == 500
        assert (
            response.json()["detail"]["code"]
            == "capacities_source_storage_error"
        )
        assert _source_bytes(vault) == before

    def test_unresolved_vault_is_503(self, client, monkeypatch):
        monkeypatch.delenv("TDTB_VAULT_ROOT", raising=False)
        app = main_mod.create_app()

        response = TestClient(app).post(
            "/settings/capacities/source/save",
            headers={"X-TDTB-Token": app.state.token},
            json=_body(),
        )

        assert response.status_code == 503


class TestPostValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            _body(expected_revision=True),                     # bool revision
            _body(expected_revision=-1),                       # negative
            _body(expected_revision=1.5),                      # float
            _body(expected_revision="1"),                      # string
            _body(space_id=123),                               # int space id
            _body(space_id=" padded "),                        # whitespace id
            _body(space_id=""),                                # empty id
            _body(extra="nope"),                               # unknown top key
            {"expected_revision": 0, "space_id": SPACE},       # missing list
            {"space_id": SPACE, "structures": [_structure()]},  # missing revision
            {"expected_revision": 0, "structures": [_structure()]},
            _body(structures="not-a-list"),                    # string list
            _body(structures=[]),                              # empty list
            _body(structures=[{"structure_id": "a", "bogus": 1}]),
            _body(structures=[{"structure_id": "a"}] * 2),     # duplicate ids
            _body(structures=["not-an-object"]),
            _body(structures=[{"structure_id": "a", "title_property": None}]),
            _body(structures=[{"structure_id": " padded "}]),
            _body(
                structures=[
                    {"structure_id": "a", "open_status_values": ["open", " OPEN "]}
                ]
            ),
        ],
    )
    def test_malformed_bodies_are_422_and_leave_the_bytes_identical(
        self, client, vault, payload
    ):
        assert client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        ).status_code == 200
        before = _source_bytes(vault)

        response = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=payload,
        )

        assert response.status_code == 422
        assert response.json()["detail"] == VALIDATION_ERROR
        assert _source_bytes(vault) == before

    @pytest.mark.parametrize(
        "raw",
        [
            "{not json",
            "",
            "[]",
            "null",
            "{}",
            # Duplicate JSON keys collapse under FastAPI's default parse;
            # this route's parse must reject them at every level.
            (
                '{"expected_revision": 0, "expected_revision": 0, '
                f'"space_id": "{SPACE}", '
                '"structures": [{"structure_id": "a"}]}'
            ),
            (
                '{"expected_revision": 0, '
                f'"space_id": "{SPACE}", '
                '"structures": [{"structure_id": "a", '
                '"structure_id": "b"}]}'
            ),
        ],
    )
    def test_raw_body_failures_are_422_and_leave_the_bytes_identical(
        self, client, vault, raw
    ):
        assert client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        ).status_code == 200
        before = _source_bytes(vault)

        response = client.post(
            "/settings/capacities/source/save",
            headers={**_auth(client), "Content-Type": "application/json"},
            content=raw,
        )

        assert response.status_code == 422
        assert response.json()["detail"] == VALIDATION_ERROR
        assert _source_bytes(vault) == before


class TestConcurrentSave:
    def test_racing_saves_from_one_revision_produce_one_winner(
        self, app, vault
    ):
        token = app.state.token
        barrier = threading.Barrier(2)

        def attempt() -> tuple[int, dict]:
            client = TestClient(app)
            barrier.wait()
            response = client.post(
                "/settings/capacities/source/save",
                headers={"X-TDTB-Token": token},
                json=_body(
                    expected_revision=0,
                    structures=[_structure(structure_id="racing")],
                ),
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: attempt(), range(2)))

        assert sorted(status for status, _ in results) == [200, 409]
        winner = next(body for status, body in results if status == 200)
        conflict = next(body for status, body in results if status == 409)
        assert winner["source"]["revision"] == 1
        assert conflict["detail"] == {
            "code": "capacities_source_conflict",
            "message": "Mapping changed; reload and review.",
            "expected_revision": 0,
            "current_revision": 1,
        }
        assert cb.read_source(vault).revision == 1


class TestNoProviderContact:
    def test_get_and_save_never_build_an_adapter_or_touch_a_provider(
        self, client, vault, monkeypatch
    ):
        calls: list[str] = []

        def forbidden(label: str):
            def _fail(*_args, **_kwargs):
                calls.append(label)
                raise AssertionError(f"{label} must not be called")
            return _fail

        client.app.state.build_capacities_adapter = forbidden("app adapter seam")
        monkeypatch.setattr(
            cb, "build_capacities_adapter", forbidden("builder")
        )
        monkeypatch.setattr(cb, "read_settings", forbidden("settings read"))
        monkeypatch.setattr(
            cb, "load_capacities_token", forbidden("credential read")
        )
        monkeypatch.setattr(
            ca.CapacitiesRestClient, "__init__", forbidden("provider client")
        )
        monkeypatch.setattr(
            main_mod.external_sources,
            "fetch_capacities_items",
            forbidden("provider fetch"),
        )

        assert client.get("/settings/capacities/source").status_code == 200
        saved = client.post(
            "/settings/capacities/source/save",
            headers=_auth(client),
            json=_body(),
        )
        assert saved.status_code == 200
        assert client.get("/settings/capacities/source").status_code == 200

        assert calls == []


# ---------------------------------------------------------------------------
# U3c-2: explicit selections promote Capacities pool rows on the digest path.
# Both digest-build callers (GET /plan-inputs and POST /digest) share one
# promotion helper; these tests pin its behaviour through the routes.
# ---------------------------------------------------------------------------

TODAY = date(2026, 10, 9)
TAG_A = "5a25370b-f9a0-40cf-bc3a-0cab4744913c"
TAG_B = "0d194525-c5a1-4af5-bb62-202b83006b5e"


def _identity(object_id, *, structure="RootTask", space=SPACE):
    return f"capacities:{space}:{structure}:{object_id}"


def _cap_row(object_id, *, structure="RootTask", assigned=False, tags=()):
    return {
        "id": object_id, "name": object_id,
        "path": f"capacities://{SPACE}/{object_id}",
        "identity": _identity(object_id, structure=structure),
        "source": "capacities",
        "types": [structure],
        "urgency": None, "deadline": None, "priority_score": 0,
        "assigned": assigned, "blocks": 1, "duration": 30, "duration_minutes": 30,
        "capacities_id": object_id, "capacities_space_id": SPACE,
        "capacities_structure_id": structure,
        "capacities_tags": [
            {"space_id": SPACE, "tag_id": tag_id, "title": "tag"} for tag_id in tags
        ],
    }


def _seed_source(client) -> None:
    saved = client.post(
        "/settings/capacities/source/save", headers=_auth(client), json=_body(),
    )
    assert saved.status_code == 200, saved.text


def _seed_selections(*records, space=SPACE) -> None:
    """Seed the selection store directly: ``(identity, rules_revision)`` pairs."""
    sel.save_selections(
        space_id=space,
        expected_revision=0,
        selections=[
            {"identity": identity, "rules_revision": revision, "acknowledged": True}
            for identity, revision in records
        ],
    )


def _digest(client, *, pool=(), assigned=(), today=TODAY) -> dict:
    response = client.post(
        "/digest",
        headers=_auth(client),
        json={
            "today": str(today),
            "pool_items": list(pool),
            "assigned_items": list(assigned),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _names(rows) -> list[str]:
    return [row["name"] for row in rows]


def _notices_for(digest, identity) -> list[str]:
    return [w for w in digest.get("source_warnings", []) if identity in w]


def _write_artifact(rows, today) -> None:
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    sources = {
        "todoist": {
            "status": "ok", "read_at": generated,
            "rows": 0, "dropped": 0, "deferred": 0, "warnings": [],
        },
        "capacities": {
            "status": "ok", "read_at": generated,
            "rows": len(rows), "dropped": 0, "deferred": 0, "warnings": [],
        },
    }
    art.atomic_write_artifact({
        "schema": art.ARTIFACT_SCHEMA,
        "version": art.ARTIFACT_VERSION,
        "generated_at": generated,
        "logical_day": str(today),
        "producer": {"name": "test", "version": "0.1.0", "run_id": "r1"},
        "content_hash": art.compute_content_hash(sources, rows),
        "sources": sources,
        "rows": rows,
        "admission": {"rule_set_hash": "rs-1", "admitted": [], "dropped": []},
    })


class TestSelectionPromotionOnDigest:
    """POST /digest is the second digest-build caller."""

    def test_selected_pool_row_is_promoted_and_leaves_the_pool(self, client, vault):
        _seed_source(client)
        _seed_selections((_identity("keep"), 0))

        digest = _digest(client, pool=[_cap_row("keep"), _cap_row("other")])

        assert _names(digest["assigned"]) == ["keep"]
        assert _names(digest["suggested"]) == ["other"]
        assert digest["assigned_count"] == 1
        assert digest["pool_count"] == 1
        assert "source_warnings" not in digest

    def test_promoted_row_still_hits_the_tag_exclusion_with_an_excluded_notice(
        self, client, vault
    ):
        _seed_source(client)
        es.save_settings(vault, expected_revision=0, exclusions=[
            {"source": "capacities", "space_id": SPACE, "tag_id": TAG_A},
        ])
        _seed_selections((_identity("keep"), 0), (_identity("drop"), 0))

        digest = _digest(client, pool=[
            _cap_row("keep", tags=[TAG_B]),
            _cap_row("drop", tags=[TAG_A]),
        ])

        assert _names(digest["assigned"]) == ["keep"]
        assert "drop" not in _names(digest["suggested"])
        assert _notices_for(digest, _identity("keep")) == []
        notices = _notices_for(digest, _identity("drop"))
        assert len(notices) == 1 and "excluded" in notices[0]

    def test_drop_listed_selection_is_not_promoted_and_says_so(self, client, vault):
        _seed_source(client)
        _seed_selections((_identity("drop"), 0))

        def _drop(state):
            state["dropped"] = [{
                "identity": _identity("drop"),
                "dropped_at": "2026-10-09T09:00:00-07:00",
            }]

        runstate.update_runstate(vault, TODAY, _drop)

        digest = _digest(client, pool=[_cap_row("drop")])

        assert digest["assigned"] == []
        assert digest["suggested"] == []
        notices = _notices_for(digest, _identity("drop"))
        assert len(notices) == 1 and "excluded" in notices[0]

    def test_all_four_notice_codes_surface_on_the_digest_warnings(self, client, vault):
        _seed_source(client)
        es.save_settings(vault, expected_revision=0, exclusions=[
            {"source": "capacities", "space_id": SPACE, "tag_id": TAG_A},
        ])
        _seed_selections(
            (_identity("tagged"), 0),
            (_identity("old", structure="Project"), 0),
            (_identity("gone"), 0),
            (_identity("moved"), 3),
        )

        digest = _digest(client, pool=[
            _cap_row("tagged", tags=[TAG_A]),
            _cap_row("moved"),
        ])

        assert _names(digest["assigned"]) == ["moved"]
        assert len(digest["source_warnings"]) == 4
        tagged = _notices_for(digest, _identity("tagged"))
        old = _notices_for(digest, _identity("old", structure="Project"))
        gone = _notices_for(digest, _identity("gone"))
        moved = _notices_for(digest, _identity("moved"))
        assert len(tagged) == len(old) == len(gone) == len(moved) == 1
        assert "excluded" in tagged[0]
        assert "no longer mapped" in old[0]
        assert "no cached" in gone[0]
        assert "predates" in moved[0]

    def test_empty_selection_store_changes_nothing(self, client, vault):
        _seed_source(client)

        digest = _digest(client, pool=[_cap_row("keep")])

        assert digest["assigned"] == []
        assert _names(digest["suggested"]) == ["keep"]
        assert "source_warnings" not in digest

    def test_foreign_space_selections_change_nothing(self, client, vault):
        _seed_source(client)
        _seed_selections((_identity("keep", space="space-2"), 0), space="space-2")

        digest = _digest(client, pool=[_cap_row("keep")])

        assert digest["assigned"] == []
        assert _names(digest["suggested"]) == ["keep"]
        assert "source_warnings" not in digest

    def test_malformed_selection_store_degrades_to_a_visible_warning(self, client, vault):
        _seed_source(client)
        sel.selections_path().parent.mkdir(parents=True, exist_ok=True)
        sel.selections_path().write_text("{not json", encoding="utf-8")

        digest = _digest(client, pool=[_cap_row("keep")])

        assert digest["assigned"] == []
        assert _names(digest["suggested"]) == ["keep"]
        assert len(digest["source_warnings"]) == 1
        assert "not applied" in digest["source_warnings"][0]

    def test_promoted_capacities_row_still_hits_the_plan_only_commit_refusal(
        self, client, vault
    ):
        today = main_mod.gather.effective_date(datetime.now())
        _seed_source(client)
        _seed_selections((_identity("keep"), 0))
        digest = _digest(client, pool=[_cap_row("keep")], today=today)
        assert _names(digest["assigned"]) == ["keep"]
        runstate.write_digest_index(vault, today, main_mod.build_digest_index(digest))
        before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}

        response = client.post(
            "/commit?mode=shadow",
            headers=_auth(client),
            json={
                "digest": {"assigned": [{
                    "name": "keep",
                    "path": f"capacities://{SPACE}/keep",
                }]},
                "sequence": {"sequence": [{
                    "id": "keep", "start": "09:00", "end": "10:00",
                }]},
                "config": {},
            },
        )

        assert response.status_code == 422
        assert "plan-only" in response.json()["detail"]
        assert "keep" in response.json()["detail"]
        after = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
        assert before == after


class TestSelectionPromotionCallerParity:
    """GET /plan-inputs (first caller) and POST /digest (second caller)."""

    def test_plan_inputs_and_digest_promote_the_same_pool_row(self, client, vault):
        today = main_mod.gather.effective_date(datetime.now())
        _seed_source(client)
        _seed_selections((_identity("keep"), 0))
        _write_artifact([
            {
                "name": "assigned-one", "source": "capacities",
                "path": f"capacities://{SPACE}/assigned-one",
                "identity": _identity("assigned-one"), "assigned": True,
                "capacities_id": "assigned-one",
            },
            {
                "name": "keep", "source": "capacities",
                "path": f"capacities://{SPACE}/keep",
                "identity": _identity("keep"), "assigned": False,
                "capacities_id": "keep",
            },
        ], today)

        plan = client.get("/plan-inputs").json()
        digest = _digest(client, pool=[_cap_row("keep")], today=today)

        for surface in (plan["digest"], digest):
            assert "keep" in _names(surface["assigned"])
            assert "keep" not in _names(surface["suggested"])
            assert _identity("keep") in {r["identity"] for r in surface["assigned"]}
        assert "source_warnings" not in digest

    def test_plan_inputs_drop_listed_selection_is_not_promoted_and_says_so(
        self, client, vault
    ):
        today = main_mod.gather.effective_date(datetime.now())
        _seed_source(client)
        _seed_selections((_identity("drop"), 0))
        _write_artifact([{
            "name": "drop", "source": "capacities",
            "path": f"capacities://{SPACE}/drop",
            "identity": _identity("drop"), "assigned": False,
            "capacities_id": "drop",
        }], today)

        def _drop(state):
            state["dropped"] = [{
                "identity": _identity("drop"),
                "dropped_at": "2026-10-09T09:00:00-07:00",
            }]

        runstate.update_runstate(vault, today, _drop)

        body = client.get("/plan-inputs").json()

        digest = body["digest"]
        assert "drop" not in _names(digest["assigned"]) + _names(digest["suggested"])
        notices = [w for w in body["source_warnings"] if _identity("drop") in w]
        assert len(notices) == 1 and "excluded" in notices[0]

    def test_plan_inputs_surfaces_a_selection_notice_in_source_warnings(
        self, client, vault
    ):
        today = main_mod.gather.effective_date(datetime.now())
        _seed_source(client)
        _seed_selections((_identity("gone"), 0))
        _write_artifact([], today)

        body = client.get("/plan-inputs").json()

        notices = [w for w in body["source_warnings"] if _identity("gone") in w]
        assert len(notices) == 1 and "no cached" in notices[0]
