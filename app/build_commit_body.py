#!/usr/bin/env python3
"""build_commit_body.py — T19 §5 helper: emit a /commit-shaped request body.

The T19 §5 acceptance step wants to POST ``{digest, sequence, config}`` to the
real ``/commit`` HTTP route (shadow then live). That payload was meant to come
from the browser's commit-review view — but that view is T16 (frontend phase 2),
gated *after* T19, so it does not exist yet. This script is the headless
substitute: it runs the same deterministic gather -> digest -> sequence pipeline
``shadow_run.py`` / ``commit_run.py`` drive, then writes the exact JSON body the
UI would have POSTed.

    .venv/bin/python build_commit_body.py                       # -> /tmp/commit-body.json
    .venv/bin/python build_commit_body.py --out /tmp/body.json
    .venv/bin/python build_commit_body.py --sequence-file seq.json

Vault root: ``$TDTB_VAULT_ROOT`` (same env var main.py reads, never hardcoded).

Sequence: absent ``--sequence-file``, a deterministic trivial back-to-back
sequence over the digest's Assigned items is synthesized — identical to the
sequence ``commit_run.py`` used for the T14 write-verify gate. It is NOT a real
judgment timeline (no zone/capacity awareness); it exists to exercise every
commit writer end-to-end with real vault data. Drop in ``--sequence-file`` with
a real ``/sequence`` response (``{"sequence":[{id,start,end,zone},...]}``) for a
faithful timeline. Writes NOTHING to any live surface — this only emits JSON.

``config`` in the body is ``TdtbConfig.sections`` — a plain nested dict of
coerced scalars/lists/dicts, JSON-serializable by construction (config_reader
coerces every table cell to a JSON-native scalar). This is the same dict the
``/commit`` route reads ``body.config`` as; ``plan_writes`` only ever ``.get()``s
section keys off it, so the plain dict and the rich object are interchangeable.

One key that is NOT a parsed config section but the manifest still reads off
``config`` is ``micro_adventure`` — a *run-state selection* ({id, idea, category})
that drives the SKILL.md Step E Live→Todoist reroute (shadow.build_plan_manifest
reads ``config["micro_adventure"]``). Because it is a selection, not a section,
``TdtbConfig.sections`` structurally cannot carry it; we side-load today's
selection from the dated run-state note and merge it in (see
``inject_micro_adventure``) so the headless body matches what the T16 UI would
have POSTed. Absent a selection, config is emitted unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "gather"))

import tdtb_gather as gather  # noqa: E402
import main as main_mod  # noqa: E402
import runstate as runstate_mod  # noqa: E402  (dated run-state note path)
import shadow_run  # noqa: E402  (reuse its gather/digest/sequence helpers verbatim)

VAULT_ROOT_ENV = "TDTB_VAULT_ROOT"


def _load_runstate_micro_adventure(vault: Path, today: date) -> dict | None:
    """Read today's run-state ``micro_adventure`` selection, if one was made.

    Reads the *exact-date* run-state note (``tdtb-runstate-<today>.md``), unlike
    ``gather.load_runstate`` which returns the strictly-prior note for the diff
    base. Reuses gather's ``_extract_json_block`` primitive so the read matches
    the note's write format contract verbatim. Missing note or absent key -> None
    (the Live reroute then silently stays a Step E calendar block).
    """
    rs_path = vault / runstate_mod.runstate_rel_path(today)
    if not rs_path.is_file():
        return None
    data = gather._extract_json_block(rs_path.read_text(encoding="utf-8", errors="replace"))
    ma = (data or {}).get("micro_adventure")
    return ma if ma else None


def _coerce_micro_adventure(value: str | None) -> dict | None:
    """Coerce a CLI ``--micro-adventure`` value to a selection dict.

    Accepts a JSON object (``{id,idea,category}`` — used verbatim) or a bare idea
    string (wrapped as ``{"idea": <str>}``). Mirrors shadow.py's own tolerance
    (``.get("idea") if dict else str(...)``), so no new shape contract is added.
    ``None`` -> ``None`` (no override).
    """
    if value is None:
        return None
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return {"idea": str(value)}


def inject_micro_adventure(
    config: dict, vault: Path, today: date, override: dict | None = None
) -> dict:
    """Return ``config`` with a ``micro_adventure`` selection merged in.

    Precedence: explicit ``override`` (from the ``--micro-adventure`` CLI flag)
    wins; otherwise today's exact-date run-state selection. Pure — never mutates
    ``config`` (it is ``TdtbConfig.sections``, shared across the process). When no
    selection exists from either source, returns ``config`` as-is.

    This is the ISS-2 seam: without it, the emitted body's config can never carry
    ``micro_adventure`` and the SKILL.md Step E Live→Todoist reroute is unreachable
    through the headless path. The ``override`` is what lets §5 acceptance actually
    *exercise* the reroute on a day with no selection in the vault.
    """
    micro = override if override is not None else _load_runstate_micro_adventure(vault, today)
    if not micro:
        return config
    return {**config, "micro_adventure": micro}


def build_body(
    vault: Path, sequence_file: str | None, micro_adventure: dict | None = None
) -> dict:
    """Assemble the {digest, sequence, config} body from live vault data.

    Mirrors ``commit_run.py``'s pipeline up to the manifest, but stops at the
    three inputs the /commit route needs and returns them as a plain dict.
    """
    today = gather.effective_date(datetime.now())

    pool_notes, assigned_notes = shadow_run._gather_pool_and_assigned(vault, today)
    run_data = gather.build_run_data(pool_notes, assigned_notes, today)

    order = list(main_mod.config_reader.FALLBACK_RANKING_CRITERIA["within_tier_sort"])
    config_result = main_mod.config_reader.read_config(vault)
    if config_result.config is not None:
        value = config_result.config.get_ranking_criterion("within_tier_sort").value
        if isinstance(value, str):
            order = [p.strip() for p in value.split(",") if p.strip()]
        elif isinstance(value, list):
            order = value

    digest = main_mod.build_digest(
        run_data["pool_items"], run_data["assigned_items"], today, order
    )

    if sequence_file:
        sequence = json.loads(Path(sequence_file).read_text(encoding="utf-8"))
    else:
        sequence = shadow_run._synthesize_trivial_sequence(digest["assigned"])

    config = config_result.config.sections if config_result.config is not None else {}
    config = inject_micro_adventure(config, vault, today, override=micro_adventure)
    return {"digest": digest, "sequence": sequence, "config": config}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="/tmp/commit-body.json",
                        help="Where to write the commit body JSON (default: /tmp/commit-body.json)")
    parser.add_argument("--sequence-file", default=None,
                        help="Path to a real /sequence-shaped JSON file (else synthesize trivial)")
    parser.add_argument("--micro-adventure", default=None,
                        help="Inject a Live micro-adventure to exercise the SKILL.md Step E "
                             "Live->Todoist reroute. JSON object {id,idea,category} or a bare "
                             "idea string. Overrides today's run-state selection.")
    args = parser.parse_args()

    root = os.environ.get(VAULT_ROOT_ENV)
    if not root:
        print(f"ERROR: {VAULT_ROOT_ENV} not set", file=sys.stderr)
        return 1
    vault = Path(root).expanduser()
    if not vault.is_dir():
        print(f"ERROR: vault root not found: {vault}", file=sys.stderr)
        return 1

    micro = _coerce_micro_adventure(args.micro_adventure)
    body = build_body(vault, args.sequence_file, micro_adventure=micro)
    Path(args.out).write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")

    seq_rows = len(body["sequence"].get("sequence", []))
    assigned = body["digest"].get("assigned_count", "?")
    cfg_sections = len(body["config"])
    src = args.sequence_file or "synthesized trivial back-to-back"
    print(f"Wrote {args.out}")
    print(f"  digest: {assigned} assigned, {body['digest'].get('pool_count', '?')} pool")
    print(f"  sequence: {seq_rows} rows ({src})")
    print(f"  config: {cfg_sections} sections")
    injected = body["config"].get("micro_adventure")
    if injected:
        idea = injected.get("idea") if isinstance(injected, dict) else injected
        origin = "CLI override" if micro is not None else "run-state"
        print(f"  micro-adventure: '{idea}' ({origin}) — Live block will reroute to Todoist")
    if seq_rows == 0:
        print("  ⚠ 0 sequence rows — no Assigned items today; commit will be a near-noop.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
