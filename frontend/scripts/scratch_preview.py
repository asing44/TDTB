#!/usr/bin/env python3
"""T10 scratch preview server — the scratch_integration harness, held open.

Identical safety envelope to scratch_integration.py (synthetic vault, faked
read clients, canned judgment — nothing billed, dead commit surfaces, frozen
08:00 clock, refuses a busy :8790, NEVER :8746) but instead of running the
Vitest suite and exiting, it serves until killed so a browser can drive the
production cockpit bundle at http://127.0.0.1:8790/static/cockpit/.

    ../app/.venv/bin/python scripts/scratch_preview.py
"""
from __future__ import annotations

import socket
import sys
import tempfile
import time
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent
APP = FRONTEND.parent / "app"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "gather"))
sys.path.insert(0, str(FRONTEND / "scripts"))

HOST, PORT = "127.0.0.1", 8790

import capture_contract_fixtures as cf  # noqa: E402
import judgment  # noqa: E402
import main as main_mod  # noqa: E402
import shadow as shadow_mod  # noqa: E402
import uvicorn  # noqa: E402

from scratch_integration import (  # noqa: E402
    FrozenDateTime,
    WriterStore,
    WriterTodoist,
    canned_live_state,
    canned_propose,
)


def main() -> int:
    with socket.socket() as probe:
        if probe.connect_ex((HOST, PORT)) == 0:
            print(f"port {PORT} already in use — refusing to reuse a foreign server", file=sys.stderr)
            return 2

    tmp = Path(tempfile.mkdtemp(prefix="tdtb-scratch-t10-"))
    vault = cf.build_vault(tmp)
    from datetime import date

    daily = vault / "30 - Daily" / f"{date.today().isoformat()}.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("---\ntype: daily\n---\n\n# Notes\n", encoding="utf-8")

    app = main_mod.create_app(vault_root=vault)
    app.state.build_read_clients = lambda v, cfg: (cf.todoist_fake(), cf.store_fake())
    # T22: in-memory writers, matching scratch_integration — dead (None, None)
    # clients would 422 the live path now that anchored Step E calendar rows
    # are always planned.
    app.state.build_commit_clients = lambda v, cfg: (WriterTodoist(), WriterStore())
    judgment.propose_sequence = canned_propose
    shadow_mod.gather_live_state = canned_live_state
    main_mod.datetime = FrozenDateTime

    print(f"scratch preview on http://{HOST}:{PORT}/static/cockpit/ (vault: {vault})")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
