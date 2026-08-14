#!/usr/bin/env python3
"""t12a_frame_filter_proof.py — T12a: prove the out-of-frame filter on a real
commit path (scratch vault, scratch port).

The 2026-07-26 T12 shakedown's most dangerous finding was that anchored blocks
which had ALREADY ELAPSED still published as `create-event` — a 21:45 run would
have back-dated five events into the real calendar. `shadow._starts_before_frame`
fixes it and `app/tests/test_shadow.py::TestOutOfFrameAnchoredBlocks` covers the
pure builder, but those tests call `build_plan_manifest` directly. They never
exercise `main._frame_for_writes` — the route-level wiring that computes the day
frame and hands it to the builder. A frame that never reaches the builder would
leave every unit test green and the calendar still back-dated.

So this harness drives the WIRING, over real HTTP:

  - a REAL scratch vault on disk (throwaway `TemporaryDirectory`) whose dated
    `tdtb-runstate-<today>.md` carries the anchor override — the same surface
    Day Setup writes, so the frame is derived exactly as it is in production;
  - a REAL app instance, `create_app(vault_root=<scratch>)`, served by uvicorn
    on :8790 (the scratch-port precedent) — never :8746;
  - a REAL `POST /commit?mode=shadow` with a MOCKED sequence in the body, so
    zero billed judgment calls are made and no write ever happens;
  - `shadow.gather_live_state` stubbed to an empty live snapshot, so no Todoist
    or EventKit call leaves the process and every manifest row classifies as
    `would-create` — i.e. the response entries ARE the "Exact writes" list the
    cockpit renders.

Scenarios:

  A — LATE anchor (21:45, the shakedown's own shape). Six anchored blocks, four
      elapsed and two ahead. Exact writes must contain ONLY the two ahead.
  B — NORMAL anchor (07:00, an ordinary morning). The same six blocks, same
      request otherwise. All six must publish — the filter must not cost a
      normal day anything. Includes the boundary case start == anchor.
  C — an elapsed anchored block carried INSIDE the sequence payload. This is
      the path an AUTO-SEQUENCED day actually takes, and the first fix missed
      it: `judgment.py`'s prompt requires every anchored_block it is handed to
      appear in the proposal, `main._judged_anchored` drops only off/skip_today
      blocks, and `sequence.validate_sequence` demoted a pre-anchor row to a
      soft `placement_past` warning — so the block rides the commit payload as
      an ordinary row and reaches the FIRST loop of `build_plan_manifest`,
      which had no frame check. Found by this harness on 2026-07-26 and fixed
      in the same change. Exact writes must exclude it.
  D — an elapsed block the sequencer MOVED FORWARD into the frame. The filter
      reads the proposed start, not the configured one, so this must publish.

Run:  app/.venv/bin/python t12a_frame_filter_proof.py
      app/.venv/bin/python t12a_frame_filter_proof.py --keep   # keep scratch vault
Exit: 0 = A and B held; 1 = a check failed (prints which).

Bundled T12a artifact, not part of the pytest suite: its value is that it runs
the route, not the function.
"""
from __future__ import annotations

import argparse
import contextlib
import sys
import tempfile
import threading
import time
from datetime import date, datetime
from pathlib import Path

_APP = Path(__file__).parent / "app"
for _p in (str(_APP), str(_APP / "gather")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import httpx  # noqa: E402
import uvicorn  # noqa: E402

import main as main_mod  # noqa: E402
import runstate  # noqa: E402
import shadow  # noqa: E402

PORT = 8790
BASE = f"http://127.0.0.1:{PORT}"

# Six anchored lifestyle blocks spanning the day. Against a 21:45 anchor the
# first four have elapsed; only Wind Down and Late Read are still ahead.
ANCHORED_BLOCKS = [
    {"id": "Sudsing", "time": "07:00", "Duration": 30, "on": True},
    {"id": "Breakfast", "time": "08:00", "Duration": 30, "on": True},
    {"id": "Press", "time": "12:00", "Duration": 60, "on": True},
    {"id": "Dinner", "time": "18:00", "Duration": 60, "on": True},
    {"id": "Wind Down", "time": "22:00", "Duration": 60, "on": True},
    {"id": "Late Read", "time": "23:00", "Duration": 30, "on": True},
]
ELAPSED_AT_2145 = {"Sudsing", "Breakfast", "Press", "Dinner"}
AHEAD_AT_2145 = {"Wind Down", "Late Read"}

CONFIG = {
    "Defaults": {"eod": "23:59", "anchor.round_to_minutes": 15},
    "anchored_blocks": ANCHORED_BLOCKS,
}

# One movable work row, placed inside every scenario's frame. Mocked — this is
# what POST /sequence would have returned, so no judgment call is billed.
DIGEST = {
    "assigned": [
        {"name": "Garage Buildout",
         "path": "50 - Operations/Projects/Garage Buildout.md",
         "blocks": 2},
    ]
}
SEQUENCE = {"sequence": [
    {"id": "Garage Buildout", "start": "22:00", "end": "23:00", "zone": "any"},
]}

EMPTY_LIVE_STATE = {
    "todoist_tasks": [],
    "calendar_events": [],
    "vault_frontmatter": {},
    "daily_note_text": None,
}


def build_scratch_vault(root: Path, anchor: str) -> None:
    """A scratch vault whose dated runstate carries the anchor override —
    the exact surface Day Setup writes and `_frame_for_writes` reads."""
    proj = root / "50 - Operations" / "Projects"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "Garage Buildout.md").write_text(
        "---\ntype: project\nassigned: true\n---\nbody\n", encoding="utf-8"
    )
    state = runstate.build_runstate({"anchor": anchor, "eod": "23:59"})
    runstate.write_runstate(root, date.today(), state)


class ScratchServer:
    """uvicorn on :8790 against a scratch-vault app. Context-managed."""

    def __init__(self, vault: Path):
        self.app = main_mod.create_app(vault_root=vault)
        self.token = self.app.state.token
        cfg = uvicorn.Config(self.app, host="127.0.0.1", port=PORT, log_level="warning")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "ScratchServer":
        self.thread.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError(f"scratch server never came up on :{PORT}")

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def shadow_preview(self, sequence: dict) -> dict:
        r = httpx.post(
            f"{BASE}/commit?mode=shadow",
            headers={"X-TDTB-Token": self.token},
            json={"digest": DIGEST, "sequence": sequence, "config": CONFIG},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()


def exact_writes(diff: dict) -> list[dict]:
    """The rows the cockpit renders as "Exact writes" — every manifest entry
    in the shadow diff, each one a write this commit would perform."""
    return [e["manifest"] for e in diff["entries"]]


def calendar_names(diff: dict) -> set[str]:
    return {m["name"] for m in exact_writes(diff) if m["system"] == "calendar"}


def _report(diff: dict, label: str) -> None:
    print(f"\n  {label} — exact writes ({len(diff['entries'])} rows):")
    for m in exact_writes(diff):
        print(f"    [{m['step']:>2}] {m['system']:<8} {m['action']:<16} "
              f"{str(m['time'] or '--:--'):>5}  {m['name']}")


def scenario_a(vault: Path) -> list[str]:
    """LATE anchor 21:45 — only the two blocks still ahead may publish."""
    failures: list[str] = []
    build_scratch_vault(vault, anchor="21:45")
    with ScratchServer(vault) as srv:
        diff = srv.shadow_preview(SEQUENCE)
    _report(diff, "A: anchor 21:45 (shakedown shape)")

    names = calendar_names(diff)
    leaked = names & ELAPSED_AT_2145
    if leaked:
        failures.append(
            f"A: elapsed blocks reached the write contract: {sorted(leaked)} — "
            "these would be back-dated calendar events"
        )
    missing = AHEAD_AT_2145 - names
    if missing:
        failures.append(f"A: in-frame blocks were wrongly dropped: {sorted(missing)}")
    if not failures:
        print(f"    OK — only {sorted(names)} publish; "
              f"{sorted(ELAPSED_AT_2145)} filtered out")
    return failures


def scenario_b(vault: Path) -> list[str]:
    """NORMAL anchor 07:00 — a normal morning loses nothing, including the
    boundary block whose start EQUALS the anchor."""
    failures: list[str] = []
    build_scratch_vault(vault, anchor="07:00")
    with ScratchServer(vault) as srv:
        diff = srv.shadow_preview(SEQUENCE)
    _report(diff, "B: anchor 07:00 (normal morning)")

    names = calendar_names(diff)
    expected = ELAPSED_AT_2145 | AHEAD_AT_2145
    missing = expected - names
    if missing:
        failures.append(
            f"B: the filter cost a normal morning its blocks: {sorted(missing)}"
        )
    if "Sudsing" not in names:
        failures.append("B: boundary case failed — start == anchor must not be filtered")
    if not failures:
        print(f"    OK — all {len(expected)} blocks publish, incl. the "
              "start == anchor boundary")
    return failures


def scenario_c(vault: Path) -> list[str]:
    """An elapsed anchored block riding INSIDE the sequence payload — the
    auto-sequenced day's real path, unguarded until this harness found it."""
    failures: list[str] = []
    build_scratch_vault(vault, anchor="21:45")
    seq = {"sequence": list(SEQUENCE["sequence"]) + [
        {"id": "Press", "start": "12:00", "end": "13:00", "zone": "any"},
    ]}
    with ScratchServer(vault) as srv:
        diff = srv.shadow_preview(seq)
    _report(diff, "C: elapsed 'Press' carried in the sequence payload")

    if "Press" in calendar_names(diff):
        failures.append(
            "C: an elapsed anchored block inside the sequence payload still "
            "publishes a back-dated create-event — the first loop of "
            "build_plan_manifest is unguarded"
        )
    else:
        print("    OK — the in-sequence path is filtered too; no second "
              "back-dating route")
    return failures


def scenario_d(vault: Path) -> list[str]:
    """An elapsed block the sequencer moved FORWARD into the frame is a
    legitimate write — the filter reads the proposed start, not the spec's."""
    failures: list[str] = []
    build_scratch_vault(vault, anchor="21:45")
    seq = {"sequence": list(SEQUENCE["sequence"]) + [
        {"id": "Press", "start": "22:30", "end": "23:30", "zone": "any"},
    ]}
    with ScratchServer(vault) as srv:
        diff = srv.shadow_preview(seq)
    _report(diff, "D: elapsed 'Press' moved forward to 22:30")

    row = next((m for m in exact_writes(diff) if m["name"] == "Press"), None)
    if row is None:
        failures.append(
            "D: a block moved forward into the frame was wrongly filtered — "
            "the filter must read the proposed start, not the configured one"
        )
    elif row["time"] != "22:30":
        failures.append(f"D: 'Press' published at {row['time']}, expected 22:30")
    else:
        print("    OK — publishes at its moved-forward 22:30 start")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the scratch vaults")
    args = ap.parse_args()

    # No live surface leaves this process: every manifest row then classifies
    # as would-create, which is precisely the "Exact writes" list.
    shadow.gather_live_state = lambda config, vault_root: dict(EMPTY_LIVE_STATE)

    print(f"T12a — frame-filter proof on a real commit path  ({datetime.now():%H:%M:%S})")
    print(f"  scratch port :{PORT} · mocked sequence · shadow only · no live surfaces")

    failures: list[str] = []
    with contextlib.ExitStack() as stack:
        for fn in (scenario_a, scenario_b, scenario_c, scenario_d):
            if args.keep:
                root = Path(tempfile.mkdtemp(prefix=f"t12a-{fn.__name__}-"))
                print(f"\n  scratch vault kept: {root}")
            else:
                root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            failures += fn(root)

    print()
    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS — the day frame reaches build_plan_manifest through the shadow "
          "route; elapsed anchored blocks never enter the write contract, and a "
          "normal morning is unaffected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
