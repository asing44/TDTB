"""U7: the admitted rows and read order survive ANY read source.

The refresh phase reads content from one shared, ordered queue that a REST
worker and (optionally) an MCP worker drain together. R1 says which source read
an object cannot change what gets planned, R2 says listing stays REST, R11 says
a worker's completion order never reaches admission, and R13 says an MCP
throttle or transport failure is source loss rather than a read retry. This
module proves all four over four runs of the same cold data:

1. REST only (the reference run);
2. the REST + MCP split, with both sources provably serving a read;
3. the split where MCP dies on a later claim, so every id was still read
   exactly once overall (AE3);
4. a tight ``mcp_content`` policy that forces paced waits while REST stays
   fast (AE1, AE6).

It also supplies two fixtures U0's 42-object sample did not contain: an
empty-Status Project under the ``active`` mode (the 7d74ace ``status_unknown``
drop) and entity-typed reference properties, where MCP answers with a
per-element ``title`` that REST does not send. Both must admit identically
through either source, and the entity case must leave an IDENTICAL
``capacities_adapter._fingerprint``, because that hash is the completion-write
guard (R12).

Everything here is offline: a deterministic REST double, a fake MCP double, an
injected monotonic clock and sleeper (no real ``time.sleep``), and for the
entity case the REAL ``capacities_mcp.McpProvider`` over a fake session
factory, so the three normalization rules are exercised rather than mirrored.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from datetime import date
from pathlib import Path

import pytest
from mcp import types
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

sys.path.insert(0, str(Path(__file__).parent.parent))

import capacities_adapter as ca  # noqa: E402
import capacities_assignment as cas  # noqa: E402
import capacities_mcp as cpm  # noqa: E402
import capacities_mcp_auth as cpma  # noqa: E402
import capacities_refresh as rr  # noqa: E402
import capacities_settings as cs  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from capacities_refresh_helpers import (  # noqa: E402
    PRIMARY,
    SPACE,
    TYPES,
    Clock,
    FakeMcp,
    FakeProvider,
    Sleeper,
    SplitProvider,
    _content,
    _mapping,
    _row,
    _store,
    _structures,
    gate_rest_behind_mcp,
    split_provider,
)

#: The logical day every run is pinned to, so the admitted-row comparison can
#: never depend on the calendar day the suite happens to run on.
TODAY = date(2026, 11, 5)

#: The parity fixture: cross-type reads, admitted as A/B/C/D. The first type
#: holds three ids so the two workers have enough work for a second MCP claim to
#: happen inside one batch (see the mid-run failure run below).
PARITY_OBJECTS = {PRIMARY: ["a", "b", "c"], "T2": ["d"]}
PARITY_IDS = ["a", "b", "c", "d"]

#: A second, non-native type: the status-only ``active`` mode needs a type whose
#: status vocabulary is the only admission signal.
PROJECT = "Project"

#: Fake credential text. No job field, warning, or raised message may carry it.
FAKE_ACCESS = "fake-access-token-DO-NOT-LEAK"

METADATA = {
    "issuer": "https://api.capacities.io",
    "authorization_endpoint": "https://api.capacities.io/oauth/authorize",
    "token_endpoint": "https://api.capacities.io/oauth/token",
    "response_types_supported": ["code"],
}


# ---------------------------------------------------------------------------
# One run's evidence
# ---------------------------------------------------------------------------


class _Recording:
    """Delegates to a read provider, recording the order reads are RELEASED.

    R11 is about the order results reach the read loop, not the order workers
    finished, so this records ``get_object`` returns exactly as the coordinator
    consumes them. The completion re-read is recorded separately because it must
    never travel the queue (KTD1).
    """

    def __init__(self, inner) -> None:
        self.inner = inner
        self.reads: list[str] = []
        self.completion_reads: list[str] = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def get_object(self, object_id, **kwargs):
        value = self.inner.get_object(object_id, **kwargs)
        if kwargs.get("for_completion"):
            self.completion_reads.append(object_id)
        else:
            self.reads.append(object_id)
        return value


def _coordinator(
    tmp_path,
    provider,
    *,
    mappings=TYPES,
    type_order=TYPES,
    clock=None,
    sleeper=None,
    policies=None,
    settings=None,
    assignment_settings=None,
):
    clock = clock or Clock()
    sleeper = sleeper if sleeper is not None else Sleeper(clock)
    return (
        rr.RefreshCoordinator(
            root=tmp_path / "state",
            store=_store(tmp_path),
            provider=provider,
            space_id=SPACE,
            mappings=tuple(
                m if isinstance(m, ca.StructureMapping) else _mapping(m)
                for m in mappings
            ),
            revision_supplier=lambda: 1,
            scope_key="all",
            clock=clock,
            sleeper=sleeper,
            settings=settings,
            assignment_settings=assignment_settings,
            type_order=type_order,
            policies=policies,
        ),
        sleeper,
    )


def _run(
    tmp_path,
    provider,
    *,
    mappings=TYPES,
    type_order=TYPES,
    policies=None,
    settings=None,
    assignment_settings=None,
    clock=None,
    sleeper=None,
) -> dict:
    """One cold run plus the evidence a read-source change must not move."""
    recorder = _Recording(provider)
    coordinator, sleeper = _coordinator(
        tmp_path,
        recorder,
        mappings=mappings,
        type_order=type_order,
        policies=policies,
        settings=settings,
        assignment_settings=assignment_settings,
        clock=clock,
        sleeper=sleeper,
    )
    coordinator.logical_day = TODAY
    if isinstance(provider, SplitProvider):
        provider.bind(coordinator.pacer, coordinator.cancel_event)
    coordinator.start()
    status = coordinator.wait(timeout=20)
    assert status["phase"] == "complete", status
    assert status["outcome"] == "published", status

    snapshot = coordinator.store.load_snapshot("all")
    assert snapshot is not None
    merged = []
    cached = {}
    for member in snapshot.members:
        content = coordinator.store.get(member.object_id, member.type_key)
        assert content is not None
        cached[member.object_id] = (member.type_key, content.content)
        merged.append({**_row(member.object_id, member.type_key), **content.content})
    admitted = coordinator.adapter.items_for_day_from_objects(
        coordinator.logical_day, merged
    )
    order = [
        str(item["id"] if isinstance(item, dict) else item.id)
        for item in admitted.items
    ]
    return {
        "coordinator": coordinator,
        "status": status,
        "metrics": status["progress"]["metrics"],
        "cached": cached,
        "members": sorted(member.object_id for member in snapshot.members),
        "items": sorted(order),
        "order": order,
        "reads": list(recorder.reads),
        "completion_reads": list(recorder.completion_reads),
        "fingerprints": {
            object_id: ca._fingerprint(content) for object_id, (_, content) in cached.items()
        },
        "partial": snapshot.partial,
        "unchecked": tuple(snapshot.unchecked_types),
        "warnings": tuple(status["warnings"]),
        "sleeper": sleeper,
    }


def _rest_run(tmp_path, *, objects=None, contents=None, structures=None) -> dict:
    """One REST-only reference run over the parity fixture or a custom shape."""
    if contents is None:
        provider = FakeProvider(
            structures if structures is not None else _structures(*TYPES),
            {
                (type_id, None): {
                    "objects": [_row(i, type_id) for i in ids],
                    "next_cursor": None,
                }
                for type_id, ids in (objects or PARITY_OBJECTS).items()
            },
            {
                object_id: _content(object_id, type_id)
                for type_id, ids in (objects or PARITY_OBJECTS).items()
                for object_id in ids
            },
        )
    else:
        provider = FakeProvider(
            structures if structures is not None else _structures(*TYPES),
            {
                (type_id, None): {
                    "objects": [_row(i, type_id) for i in ids],
                    "next_cursor": None,
                }
                for type_id, ids in objects.items()
            },
            contents,
        )
    run = _run(tmp_path, provider)
    run["provider"] = provider
    return run


def _gate_rest_until_mcp_reads(rest, mcp, count: int = 2) -> None:
    """Hold the REST worker's first read until MCP has CLAIMED ``count`` ids.

    ``gate_rest_behind_mcp`` releases after one MCP read, which is all a parity
    run needs. A run that must see MCP fail on a LATER claim needs MCP to get
    past its first one, and holding REST inside its own claim is what guarantees
    the remaining ids are still there for MCP to take. The wait is bounded, so
    a run that never reaches MCP still finishes.
    """
    enough = threading.Event()

    def note(object_id):
        if len(mcp.calls) >= count:
            enough.set()

    mcp.on_get = note
    rest.on_get = lambda object_id: enough.wait(2.0)


def _assert_same_plan(reference: dict, run: dict, label: str) -> None:
    """R1/AE1: members, admitted ids, admission order, and read order are equal."""
    assert run["members"] == reference["members"], label
    assert run["items"] == reference["items"], label
    assert run["order"] == reference["order"], label
    assert run["reads"] == reference["reads"], label
    assert run["partial"] is False, label
    assert run["unchecked"] == (), label


# ---------------------------------------------------------------------------
# AE1 / AE3 / AE6 - four read sources, one plan
# ---------------------------------------------------------------------------


def test_the_admitted_rows_and_read_order_survive_any_read_source(tmp_path):
    """R1/R2/R11/R13: no read source can change the plan or the read order."""
    # (1) REST only: the reference the other three runs are compared against.
    reference = _rest_run(tmp_path / "rest")
    rest = reference["provider"]
    assert reference["items"] == ["A", "B", "C", "D"], reference
    assert reference["reads"] == PARITY_IDS, reference
    assert reference["metrics"]["read_source"] == "rest"

    # (2) The split, with both sources provably serving a read. Without the
    # gate a fast REST worker could drain the batch and make this vacuous.
    split, split_rest, split_mcp = split_provider(objects=PARITY_OBJECTS)
    gate_rest_behind_mcp(split_rest, split_mcp)
    split_run = _run(tmp_path / "split", split)
    metrics = split_run["metrics"]
    assert metrics["read_source"] == "split", metrics
    assert metrics["read_source_fell_back"] is False, metrics
    assert metrics["mcp_reads"] > 0, metrics
    assert metrics["rest_reads"] > 0, metrics
    assert metrics["mcp_reads"] + metrics["rest_reads"] == len(PARITY_IDS), metrics
    assert len(split_mcp.ids) == metrics["mcp_reads"]
    assert sorted(split_rest.get_calls + split_mcp.ids) == PARITY_IDS
    _assert_same_plan(reference, split_run, "split")
    assert split.splitter.queue.live_worker_count == 0
    # Listing never moves to MCP: the double's listing lives on REST, and the
    # MCP surface has no listing call at all (R2).
    assert split_rest.list_calls, "listing did not go to REST"
    assert not hasattr(split_mcp, "list_objects"), "MCP grew a listing surface"

    # (3) MCP dies on a later claim: source loss, not a retry, and every id is
    # still read exactly once overall (AE3, R13).
    failing, fail_rest, fail_mcp = split_provider(
        objects=PARITY_OBJECTS,
        behaviors=[None, ca.CapacitiesReadSourceUnavailable("", retry_after=None)],
    )
    _gate_rest_until_mcp_reads(fail_rest, fail_mcp)
    fail_run = _run(tmp_path / "failing", failing)
    assert len(fail_mcp.ids) == 2, fail_mcp.ids
    served, refused = fail_mcp.ids
    # The refused claim is not a read: REST re-reads that id exactly once.
    assert refused in fail_rest.get_calls
    assert served not in fail_rest.get_calls
    assert sorted([served] + fail_rest.get_calls) == PARITY_IDS
    metrics = fail_run["metrics"]
    assert metrics["read_source"] == "split", metrics
    assert metrics["read_source_fell_back"] is True, metrics
    assert metrics["mcp_reads"] == 1, metrics
    assert metrics["rest_reads"] == len(PARITY_IDS) - 1, metrics
    # Source loss is neither a rate limit nor a read retry (R13/AE6).
    assert metrics["rate_limited"] == 0, metrics
    assert metrics["retried_reads"] == 0, metrics
    assert metrics["retry_recovered"] == 0, metrics
    assert not [w for w in fail_run["warnings"] if w.startswith("Capacities object ")]
    _assert_same_plan(reference, fail_run, "mcp-failed")

    # (4) A tight mcp_content policy: MCP waits, REST does not. REST is given a
    # wide-open content policy so every recorded wait is provably an MCP wait.
    clock = Clock()
    sleeper = Sleeper(clock)
    paced, paced_rest, paced_mcp = split_provider(objects=PARITY_OBJECTS)
    gate_rest_behind_mcp(paced_rest, paced_mcp)
    paced_run = _run(
        tmp_path / "paced",
        paced,
        clock=clock,
        sleeper=sleeper,
        policies={
            rr.ENDPOINT_MCP_CONTENT: rr.EndpointPolicy(limit=1, window=60.0),
            rr.ENDPOINT_CONTENT: rr.EndpointPolicy(limit=100, window=60.0),
        },
    )
    assert sleeper.calls, "the tight mcp_content policy never forced a wait"
    assert all(call == rr.MCP_CONTENT_WINDOW_SECONDS for call in sleeper.calls), (
        sleeper.calls
    )
    assert paced_mcp.ids, "the MCP worker never served a read under pacing"
    _assert_same_plan(reference, paced_run, "paced")

    # A guard that compared empty results would prove nothing.
    assert reference["items"] == ["A", "B", "C", "D"], reference
    assert reference["reads"] == PARITY_IDS, reference
    assert rest.get_calls == PARITY_IDS, rest.get_calls


def test_the_completion_reread_stays_on_rest(tmp_path):
    """KTD1/R2: the optimistic-concurrency re-read is REST, never the queue."""
    split, rest, mcp = split_provider(objects=PARITY_OBJECTS)
    gate_rest_behind_mcp(rest, mcp)
    run = _run(tmp_path, split)
    assert run["completion_reads"] == [], run["completion_reads"]

    before_rest = len(rest.get_calls)
    before_mcp = len(mcp.calls)
    split.get_object("a", for_completion=True)

    assert len(mcp.calls) == before_mcp, "the completion re-read used MCP"
    assert len(rest.get_calls) == before_rest + 1, rest.get_calls
    assert rest.get_calls[-1] == "a"
    assert run["completion_reads"] == [], "the re-read reached the queue"
    # R2: the MCP surface has no listing call at all; listing stays REST.
    assert not hasattr(mcp, "list_objects")


# ---------------------------------------------------------------------------
# U0's missing fixtures - both admit identically through either source
# ---------------------------------------------------------------------------


def _active_settings():
    """The persisted policy plus its evaluator projection.

    Project is status-only ``active``: its status vocabulary is the ONLY
    admission signal, which is what the undecidable-status rule keys off. Both
    objects are needed: the persistence document is what the adapter's
    duck-typed status vocabulary reads, and ``AssignmentSettings.from_v2`` is
    the evaluator seam the projection calls.
    """
    settings = cs.CapacitiesSettings(
        revision=3,
        statuses=cs.StatusVocabulary(
            open=frozenset({"active"}), completed=frozenset({"done"})
        ),
        types={
            # Schema v2 makes an undeclared structure ineligible, so the native
            # type is declared on its own assignment marker.
            PRIMARY: cs.TypeDeclaration(mode="assigned", property_id="assigned"),
            PROJECT: cs.TypeDeclaration(mode="active"),
        },
    )
    return settings, cas.AssignmentSettings.from_v2(settings)


def _project_content(object_id: str, status_members) -> dict:
    """A Project whose status property carries ``status_members``.

    ``status_members=[]`` is the live empty-Status payload U0 never sampled: a
    status-only type has no admission signal for it at all.
    """
    return {
        "id": object_id,
        "structureId": PROJECT,
        "properties": {
            "title": {"type": "title", "title": {"value": object_id.title()}},
            "status": {"type": "label", "label": list(status_members)},
        },
    }


def test_an_empty_status_project_is_refused_identically_by_both_sources(tmp_path):
    """7d74ace: an undecidable status-only row follows the REST-only outcome.

    The assertion is equality with the REST-only run, not a hard-coded verdict:
    whatever the REST-only traversal decides for this row (drop it, keep it, and
    with exactly which warning), the split read must decide the same.
    """
    objects = {PRIMARY: ["a"], PROJECT: ["p-empty", "p-open"]}
    contents = {
        "a": _content("a", PRIMARY),
        "p-empty": _project_content("p-empty", []),
        "p-open": _project_content("p-open", [{"id": "active", "name": "Active"}]),
    }
    structures = _structures(PRIMARY, PROJECT)
    # A status-only Project: no assignment marker, so the status is the only
    # admission signal and an empty one is undecidable (7d74ace).
    project_mapping = ca.StructureMapping(
        structure_id=PROJECT,
        assignment_property=None,
        assignment_values=frozenset(),
        open_status_property="status",
        open_status_values=frozenset({"active"}),
        title_property="title",
    )
    mappings = (PRIMARY, project_mapping)
    settings, assignment_settings = _active_settings()

    reference = _run(
        tmp_path / "rest",
        FakeProvider(
            structures,
            {
                (type_id, None): {
                    "objects": [_row(i, type_id) for i in ids],
                    "next_cursor": None,
                }
                for type_id, ids in objects.items()
            },
            contents,
        ),
        mappings=mappings,
        assignment_settings=assignment_settings,
        settings=settings,
    )
    assert reference["members"] == ["a", "p-empty", "p-open"], reference

    split, rest, mcp = split_provider(objects=objects, structures=structures)
    # Both doubles must serve the same payloads, or the comparison is moot.
    mcp._contents.update(contents)
    rest._objects.update(contents)
    gate_rest_behind_mcp(rest, mcp)
    split_run = _run(
        tmp_path / "split",
        split,
        mappings=mappings,
        assignment_settings=assignment_settings,
        settings=settings,
    )

    assert mcp.ids, "the MCP worker served no read"
    _assert_same_plan(reference, split_run, "empty-status")
    # The warning wording must match too, not merely its presence: the bounded
    # review line is what the operator acts on.
    assert split_run["warnings"] == reference["warnings"], split_run["warnings"]
    assert reference["warnings"], "the status-only fixture reached no warning"
    assert any("status_unknown" in w for w in reference["warnings"]), reference[
        "warnings"
    ]
    # The decidable row is admitted; the undecidable one is not.
    assert "P-Open" in reference["items"], reference["items"]
    assert "P-Empty" not in reference["items"], reference["items"]


# -- entity-typed reference lists: the real McpProvider normalization (U3) ----


def _entity_rest_content(object_id: str) -> dict:
    """REST's payload: entity references carry ``{id}`` only."""
    return {
        "id": object_id,
        "structureId": PRIMARY,
        "properties": {
            "title": {"type": "title", "title": {"value": object_id.title()}},
            "assigned": {"type": "boolean", "boolean": True},
            "status": {"type": "label", "label": [{"id": "active", "name": "Active"}]},
            "people": {
                "type": "entity",
                "entity": [{"id": "person-1"}, {"id": "person-2"}],
            },
            "projects": {
                "type": "entity",
                "entities": [{"id": "project-1"}],
            },
        },
    }


def _entity_mcp_payload(object_id: str) -> dict:
    """MCP's payload for the same object: references carry a per-element title.

    ``structureId`` is deliberately absent and ``objectType`` says ``Task`` while
    ``objectTypeId`` says ``RootTask``, so only normalization rule (i) can make
    the two sources agree.
    """
    payload = _entity_rest_content(object_id)
    payload.pop("structureId")
    payload.update(
        {
            "objectType": "Task",
            "objectTypeId": PRIMARY,
            "title": object_id.title(),
            "properties": {
                **payload["properties"],
                "people": {
                    "type": "entity",
                    "entity": [
                        {"id": "person-1", "title": "Ada"},
                        {"id": "person-2", "title": "Grace"},
                    ],
                },
                "projects": {
                    "type": "entity",
                    "entities": [{"id": "project-1", "title": "Alpha"}],
                },
            },
        }
    )
    return payload


class _ContentSession:
    """The two ``ClientSession`` calls ``McpProvider`` makes."""

    def __init__(self, payloads, served=None) -> None:
        self.payloads = payloads
        self.served = served
        self.calls: list[str] = []

    async def list_tools(self):
        return types.ListToolsResult(
            tools=[types.Tool(name="capacities_getObjectContent", inputSchema={})]
        )

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        object_id = dict(arguments or {})["id"]
        self.calls.append(object_id)
        if self.served is not None:
            self.served.set()
        return types.CallToolResult(
            content=[], structuredContent=self.payloads[object_id]
        )


class _ContentSessionFactory:
    def __init__(self, payloads, served=None) -> None:
        self.payloads = payloads
        self.served = served
        self.sessions: list[_ContentSession] = []

    def __call__(self, auth):
        session = _ContentSession(self.payloads, self.served)
        self.sessions.append(session)

        class _Context:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *exc_info):
                return False

        return _Context()

    @property
    def reads(self) -> list[str]:
        return [object_id for session in self.sessions for object_id in session.calls]


def _custody(tmp_path):
    """A custody document the operator's authorize step would have written."""
    storage = cpma.FileTokenStorage(tmp_path / "custody" / "oauth.json")
    asyncio.run(
        storage.set_client_info(
            OAuthClientInformationFull(
                redirect_uris=[cpm.REDIRECT_URI],
                token_endpoint_auth_method="none",
                client_id="client-1",
            )
        )
    )
    asyncio.run(
        storage.set_tokens(
            OAuthToken(
                access_token=FAKE_ACCESS,
                refresh_token="fake-refresh-token-DO-NOT-LEAK",
                expires_in=3600,
            )
        )
    )
    return storage


def test_entity_reference_titles_normalize_to_an_identical_fingerprint(tmp_path):
    """R1/R12 (rule iii + rule i): MCP's extra titles cannot move the guard.

    U0's sample carried no entity-typed properties, so nothing proved that the
    per-element ``title`` MCP sends is stripped before admission and before
    ``_fingerprint``. A difference here would break the completion write guard
    for exactly the objects that carry references.
    """
    object_ids = ["task-1", "task-2"]
    rest_payloads = {object_id: _entity_rest_content(object_id) for object_id in object_ids}
    mcp_payloads = {object_id: _entity_mcp_payload(object_id) for object_id in object_ids}
    structures = _structures(PRIMARY)

    reference = _run(
        tmp_path / "rest",
        FakeProvider(
            structures,
            {
                (PRIMARY, None): {
                    "objects": [_row(i, PRIMARY) for i in object_ids],
                    "next_cursor": None,
                }
            },
            rest_payloads,
        ),
        mappings=(PRIMARY,),
    )
    assert reference["members"] == object_ids, reference

    # Hold the first REST read until MCP has served one, so the split is real.
    served = threading.Event()
    factory = _ContentSessionFactory(mcp_payloads, served)
    rest_double = FakeProvider(
        structures,
        {
            (PRIMARY, None): {
                "objects": [_row(i, PRIMARY) for i in object_ids],
                "next_cursor": None,
            }
        },
        rest_payloads,
    )
    rest_double.on_get = lambda object_id: served.wait(2.0)
    mcp = cpm.McpProvider(
        SPACE,
        _custody(tmp_path),
        session_factory=factory,
        metadata_loader=lambda: METADATA,
    )
    provider = SplitProvider(rest_double, mcp)
    split_run = _run(tmp_path / "split", provider, mappings=(PRIMARY,))
    provider.close()

    assert factory.reads, "the real McpProvider served no read"
    _assert_same_plan(reference, split_run, "entity-refs")
    # The completion guard's own hash must be identical object by object.
    assert sorted(split_run["fingerprints"]) == object_ids
    for object_id in object_ids:
        assert (
            split_run["fingerprints"][object_id]
            == reference["fingerprints"][object_id]
        ), object_id
    # Rule (i): the literal native structure id is what gets stamped, even
    # though MCP reports the display name ``Task``.
    for object_id in object_ids:
        assert split_run["cached"][object_id][0] == PRIMARY
        assert split_run["cached"][object_id][1]["structureId"] == PRIMARY
    # Rule (iii): no per-element title survived, and nothing else was rewritten.
    split_people = split_run["cached"]["task-1"][1]["properties"]["people"]["entity"]
    assert split_people == [{"id": "person-1"}, {"id": "person-2"}], split_people
    assert (
        split_run["cached"]["task-1"][1]["properties"]
        == reference["cached"]["task-1"][1]["properties"]
    )
    assert FAKE_ACCESS not in repr(split_run["status"])
