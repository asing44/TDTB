"""app_config.py — TDTB app config: tracked repo file + machine-local override.

V1 of the vault removal (docs/plan/2026-10-12-vault-removal): the hand-editable
TDTB config no longer comes from the Obsidian vault note. It comes from two
JSON files, layered in this order:

  1. ``config/tdtb.json`` at the repo root (tracked; ``repo_config_path()``,
     overridable with ``$TDTB_REPO_CONFIG`` for tests), then
  2. ``~/.config/tdtb/config.json`` (``config_path()``, relocatable with
     ``$TDTB_HOME``) as an OPTIONAL machine-local override. Machine settings
     (``sources.mode``, ``sources.vault_enabled``) and anything secret-ish live
     only here.

Layering is recursive for objects (so a local ``sources.mode`` joins a repo
``sources`` object) and replace-wins for everything else, lists included (a
local ``presets`` replaces the repo ``presets`` wholesale).

This module also owns the **home resolver** (`app_home`, `config_path`,
`state_dir`) every app-owned store reuses, and the v1 document schema plus its
translation to the markdown-shaped section dict the rest of the app consumes
(``TdtbConfig.sections``). The document is::

    {
      "version": 1,
      "defaults": {"eod": ..., "anchor": {...}, "buffering": {...}, "caps": {...},
                   "work_allotment_minutes": ...},
      "schedulable_defaults": [...],
      "ranking_criteria": {"within_tier_sort": [...], ...},
      "placement_context": "prose that rides into the sequence prompt",
      "schema_reference": {"todoist_projects": [...], "todoist_filters": [...]},
      "calendar": {"titles": [...], "capacity_classes": [...], "disabled": [...]},
      "ignore": {"todoist_ids": [...], "paths": [...], "names": [...]},
      "presets": [...],
      "anchored_blocks": [...],
      "day_presets": [...],
      "template_blocks": {"Trinoor Hours": [...], ...},
      "overlap_permissions": "verbatim markdown policy text",
      "colors": [...],
      "micro_adventure_pool": [...],
      "micro_adventure_rotation": {"exclude_window_days": 14, ...},
      "habits": {"fallback_minutes_per_habit": 4, "round_to_minutes": 15},
      "todoist": {"read_query": {"assigned": ..., "quick": ...}},
      "sources": {"mode": "live" | "artifact",
                  "artifact": {"max_age_minutes": 240},
                  "vault_enabled": true,
                  "capacities_intake": "legacy" | "direct"}
    }

``sources`` is the artifact-contract pivot's source-selection knob (see
``artifact_source``). It is not markdown-shaped and never appears in
``sections``. ``mode: "both"`` is rejected at load — the read drift trap — so
a file carrying it is ignored as a whole (the other layer still loads).

Record sections carry the old vault config's own row shape verbatim, so the
translation stays lossless and the operator edits the same fields the vault
table exposed. ``habits`` and ``todoist.read_query`` re-home dot-notation
``Defaults`` keys under their own objects. Every key has an in-code fallback
(``config_reader.FALLBACK_*``, ``micro_adventure.FALLBACK_POOL``), so a missing
key never errors.

``load_sections()`` returns ``{}`` — never raises — when neither file is
usable (missing, unreadable, corrupt, or unsupported version).

This module never writes either file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

#: Env var that overrides the app home. Mirrors the vault-free precedent in
#: ``capacities_structure_titles.py`` (``~/.config/tdtb``), one root with one
#: override for every app-owned store.
TDTB_HOME_ENV = "TDTB_HOME"

#: Machine-local, never vault-local. A sibling of the Capacities title cache.
DEFAULT_APP_HOME = Path.home() / ".config" / "tdtb"

CONFIG_FILENAME = "config.json"

#: Env var that relocates the tracked repo config. A test seam: the suite points
#: it at a per-test path so no test ever reads the real ``config/tdtb.json``.
REPO_CONFIG_ENV = "TDTB_REPO_CONFIG"
REPO_CONFIG_FILENAME = "tdtb.json"
REPO_CONFIG_DIRNAME = "config"
STATE_DIRNAME = "state"
CONFIG_VERSION = 1

#: ``sources.mode`` — the artifact-contract pivot's source-selection knob.
#: ``live`` (default) reads Todoist/Capacities live; ``artifact`` consumes the
#: normalized planning artifact and constructs no live client. ``both`` is
#: deliberately NOT a mode: two readers coexisting is the drift trap the
#: operator named, so the whole document is rejected rather than drifted into.
SOURCES_MODE_LIVE = "live"
SOURCES_MODE_ARTIFACT = "artifact"
SOURCES_MODES = frozenset({SOURCES_MODE_LIVE, SOURCES_MODE_ARTIFACT})

#: ``sources.capacities_intake`` — which Capacities intake the planning digest
#: reads. ``legacy`` (the default) keeps the existing read; ``direct`` projects
#: the published direct-refresh cache offline with no provider access. Only the
#: exact ``direct`` value selects it, so a typo can never switch intake by
#: accident: anything else resolves to ``legacy`` and is reported.
SOURCES_CAPACITIES_INTAKE_KEY = "capacities_intake"
CAPACITIES_INTAKE_LEGACY = "legacy"
CAPACITIES_INTAKE_DIRECT = "direct"
CAPACITIES_INTAKE_MODES = frozenset({CAPACITIES_INTAKE_LEGACY, CAPACITIES_INTAKE_DIRECT})
DEFAULT_CAPACITIES_INTAKE = CAPACITIES_INTAKE_LEGACY

#: Keys the ``sources`` section recognizes. Anything else is reported.
SOURCES_KNOWN_KEYS = frozenset({
    "mode", "vault_enabled", "artifact", "max_age_minutes",
    SOURCES_CAPACITIES_INTAKE_KEY,
})

#: Keys the nested ``sources.artifact`` section recognizes.
SOURCES_ARTIFACT_KNOWN_KEYS = frozenset({"max_age_minutes"})

#: Default artifact age ceiling (minutes) before a load reports ``aged``.
DEFAULT_ARTIFACT_MAX_AGE_MINUTES = 240

#: ``sources.vault_enabled`` — the S5 cutover knob. When false the vault gather
#: contributes no rows and a resolvable vault root is no longer required for the
#: digest. The default is **True**: shipping the S5 slice changes nothing, the
#: cutover IS the operator setting this to false, and the rollback is setting it
#: back. S6 deletes the flag and the vault gather together.
DEFAULT_VAULT_ENABLED = True

# ---------------------------------------------------------------------------
# Home resolver — the one reusable helper later slices use
# ---------------------------------------------------------------------------

def app_home() -> Path:
    """Return the app home: ``$TDTB_HOME`` if set and non-empty, else
    ``~/.config/tdtb``. Read at call time so tests and the operator can flip
    it without a reimport."""
    override = os.environ.get(TDTB_HOME_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_APP_HOME


def config_path() -> Path:
    """Path to the canonical ``config.json`` under the app home."""
    return app_home() / CONFIG_FILENAME


def state_dir() -> Path:
    """Path to ``<app_home>/state/`` — the future home of the app-owned stores
    (runstate, exclusions, capacities settings, capacities source, deferrals).

    S0 computes the path only; it does not create the directory or move any
    store."""
    return app_home() / STATE_DIRNAME


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def repo_config_path() -> Path:
    """Path to the tracked repo config, ``<repo root>/config/tdtb.json``
    (``$TDTB_REPO_CONFIG`` overrides it; read at call time)."""
    override = os.environ.get(REPO_CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent / REPO_CONFIG_DIRNAME / REPO_CONFIG_FILENAME


def read_document_file(target: str | Path) -> dict[str, Any] | None:
    """Read and validate ONE config file. Returns the parsed document, or
    ``None`` for a missing / unreadable / corrupt / unsupported-version file or
    one whose ``sources`` section is rejected. Never raises."""
    target = Path(target)
    try:
        if not target.exists():
            return None
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != CONFIG_VERSION:
        return None
    if not _sources_section_ok(data):
        return None
    return data


def _layer(base: Any, overlay: Any) -> Any:
    """Recursive overlay: objects merge key-wise, anything else (lists
    included) is replaced by the overlay value."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            merged[key] = _layer(merged[key], value) if key in merged else value
        return merged
    return overlay


def load_document(path: str | Path | None = None) -> dict[str, Any] | None:
    """The effective config document.

    With an explicit ``path`` that single file is read (see
    :func:`read_document_file`). With none, the tracked repo file is read
    first and ``$TDTB_HOME/config.json`` is layered on top as an optional
    machine-local override; a layer that is missing or unusable is skipped.
    ``None`` when neither layer is usable. Never raises."""
    if path is not None:
        return read_document_file(path)
    repo = read_document_file(repo_config_path())
    local = read_document_file(config_path())
    if repo is None:
        return local
    if local is None:
        return repo
    return _layer(repo, local)


def _sources_section_ok(document: dict[str, Any]) -> bool:
    """Validate the optional ``sources`` section, rejecting ``both``.

    An absent section, or one without a ``mode``, is accepted (the default is
    ``live``). A mode outside :data:`SOURCES_MODES` — most importantly
    ``both`` — rejects the whole document, so ``load_document`` returns None
    and the file is ignored rather than running two readers."""
    sources = document.get("sources")
    if sources is None:
        return True
    if not isinstance(sources, dict):
        return False
    mode = _trimmed_keys(sources).get("mode")
    if mode is None:
        return True
    return mode in SOURCES_MODES


#: The default source mode. Slice A4 flipped this from ``live`` to
#: ``artifact``: the artifact contract is now the primary path and the live
#: readers are what an operator opts back into explicitly.
DEFAULT_SOURCES_MODE = SOURCES_MODE_ARTIFACT


def sources_mode(path: str | Path | None = None) -> str:
    """The active source mode: ``artifact`` (the default) or ``live``.

    Any unusable document (missing, corrupt, unsupported version, or a
    rejected ``sources`` section) resolves to ``artifact`` — an unreadable
    config must never silently re-enable the live readers, because a live
    read is not a fallback (point 7). An operator who wants the live readers
    sets ``sources.mode`` to ``live`` deliberately."""
    document = load_document(path)
    if document is None:
        return DEFAULT_SOURCES_MODE
    sources = _trimmed_keys(document.get("sources"))
    if sources.get("mode") in SOURCES_MODES:
        return str(sources["mode"])
    return DEFAULT_SOURCES_MODE


def vault_enabled(path: str | Path | None = None) -> bool:
    """Whether the vault gather contributes rows to the planning digest.

    Reads ``sources.vault_enabled`` from config.json, accepting a top-level
    ``vault_enabled`` alias for operator convenience (the plan names the flag
    without a section). Absent, or carried by an unusable document, resolves
    to :data:`DEFAULT_VAULT_ENABLED` — **True**. Failing toward "vault
    on" is deliberate, because a malformed config must never silently drop the
    ~96 vault rows the migration exists to triage. Only an explicit false
    disables the gather, per the S5 "loud, not silent" contract.

    ``mode`` and ``vault_enabled`` are independent: an operator can run the
    live source mode with the vault gather off."""
    document = load_document(path)
    if document is None:
        return DEFAULT_VAULT_ENABLED
    sources = _trimmed_keys(document.get("sources"))
    value = sources.get("vault_enabled")
    if value is None:
        value = _trimmed_keys(document).get("vault_enabled")
    parsed = _coerce_bool(value)
    return DEFAULT_VAULT_ENABLED if parsed is None else parsed


def artifact_max_age_minutes(path: str | Path | None = None) -> int:
    """The artifact age ceiling, default 240 minutes.

    Reads ``sources.artifact.max_age_minutes`` (or the flat
    ``sources.max_age_minutes``); anything not a positive integer falls back
    to the default."""
    document = load_document(path)
    if document is None:
        return DEFAULT_ARTIFACT_MAX_AGE_MINUTES
    sources = _trimmed_keys(document.get("sources"))
    artifact = _trimmed_keys(sources.get("artifact"))
    value = artifact.get("max_age_minutes")
    if value is None:
        value = sources.get("max_age_minutes")
    if type(value) is int and value > 0:
        return value
    return DEFAULT_ARTIFACT_MAX_AGE_MINUTES


def capacities_intake(path: str | Path | None = None) -> str:
    """The Capacities intake the planning digest reads: ``legacy`` or ``direct``.

    ``direct`` is selected only by the exact string ``"direct"``. Anything else
    (missing, misspelled, a non-string, or an unusable document) resolves to
    :data:`DEFAULT_CAPACITIES_INTAKE`. :func:`sources_config_warnings` reports a
    value that did not resolve, so a misspelling is never a silent switch.
    """
    document = load_document(path)
    if document is None:
        return DEFAULT_CAPACITIES_INTAKE
    value = _trimmed_keys(document.get("sources")).get(SOURCES_CAPACITIES_INTAKE_KEY)
    if isinstance(value, str) and value in CAPACITIES_INTAKE_MODES:
        return value
    return DEFAULT_CAPACITIES_INTAKE


def sources_config_warnings(path: str | Path | None = None) -> list[str]:
    """Report unrecognized keys in the ``sources`` section of config.json.

    JSON preserves a hand-typed leading/trailing space in a key, so
    ``" vault_enabled"`` looks correct to the operator while missing every
    exact-key lookup. :func:`_trimmed_keys` tolerates that on read, but a key
    the section does not recognize at all is still silently discarded unless
    it is reported here — the 2026-10-09 S5 flip wrote ``" vault_enabled"``
    and the vault stayed on with no warning. This is the verdict that makes
    the tolerance loud: report the key as written (whitespace included) so the
    operator can find it.

    Returns ``[]`` for an unusable document, an absent ``sources`` section,
    or a clean section (every key — including an empty ``sources: {}``, which
    makes no claim — is recognized). Never raises."""
    if path is not None:
        documents = [read_document_file(path)]
    else:
        documents = [
            read_document_file(repo_config_path()),
            read_document_file(config_path()),
        ]
    warnings: list[str] = []
    for document in documents:
        if document is not None:
            warnings += _sources_warnings(document)
    return list(dict.fromkeys(warnings))


def _sources_warnings(document: dict[str, Any]) -> list[str]:
    sources = document.get("sources")
    if not isinstance(sources, dict):
        return []
    warnings = _unrecognized_key_warnings(sources, "sources", SOURCES_KNOWN_KEYS)
    intake = _trimmed_keys(sources).get(SOURCES_CAPACITIES_INTAKE_KEY, DEFAULT_CAPACITIES_INTAKE)
    if not (isinstance(intake, str) and intake in CAPACITIES_INTAKE_MODES):
        warnings.append(
            f"config.json sources.{SOURCES_CAPACITIES_INTAKE_KEY} is {intake!r}, "
            "which is not 'legacy' or 'direct'; using 'legacy' (the Capacities "
            "intake does not switch on an unrecognized value)."
        )
    artifact = sources.get("artifact")
    if isinstance(artifact, dict):
        warnings += _unrecognized_key_warnings(
            artifact, "sources.artifact", SOURCES_ARTIFACT_KNOWN_KEYS
        )
    return warnings


def load_sections(path: str | Path | None = None) -> dict[str, Any]:
    """Return the markdown-shaped sections dict implied by the layered config
    (repo file, then the machine-local override).

    ``{}`` when no layer is usable, which is the signal to
    ``config_reader.read_config`` that there is nothing to read."""
    document = load_document(path)
    if document is None:
        return {}
    return sections_from_document(document)


# ---------------------------------------------------------------------------
# config.json -> markdown-shaped sections
# ---------------------------------------------------------------------------

def sections_from_document(document: dict[str, Any]) -> dict[str, Any]:
    """Translate a config.json v1 document into the section dict the app's
    consumers already read (``TdtbConfig.sections``)."""
    if not isinstance(document, dict):
        return {}
    out: dict[str, Any] = {}

    calendar = document.get("calendar")
    if isinstance(calendar, dict):
        if "titles" in calendar:
            out["Calendar Titles"] = calendar["titles"]
        if "capacity_classes" in calendar:
            out["Calendar Capacity Classes"] = calendar["capacity_classes"]
        if "disabled" in calendar:
            out["Disabled Calendars"] = [
                {"Title": str(value)} for value in _as_list(calendar["disabled"])
            ]

    if "ignore" in document:
        out["Ignore List"] = _ignore_section(document["ignore"])
    if "presets" in document:
        out["Presets"] = document["presets"]
    if "anchored_blocks" in document:
        out["Anchored Lifestyle Blocks"] = document["anchored_blocks"]
    if "schedulable_defaults" in document:
        out["Schedulable Defaults"] = document["schedulable_defaults"]
    if "day_presets" in document:
        out["Day Presets"] = document["day_presets"]
    if isinstance(document.get("template_blocks"), dict):
        out["Template Blocks"] = document["template_blocks"]
    if isinstance(document.get("overlap_permissions"), str):
        # Dict-shaped like the old prose-only section, so the /plan-inputs
        # ``config`` echo keeps its type; ``_body`` is the verbatim policy.
        out["Overlap Permissions"] = {"_body": document["overlap_permissions"]}
    if isinstance(document.get("ranking_criteria"), dict):
        out["Ranking Criteria"] = dict(document["ranking_criteria"])
    if isinstance(document.get("placement_context"), str):
        out["Placement Context"] = {"_body": document["placement_context"]}
    schema_reference = document.get("schema_reference")
    if isinstance(schema_reference, dict):
        ref: dict[str, Any] = {}
        if "todoist_projects" in schema_reference:
            ref["Todoist Projects"] = schema_reference["todoist_projects"]
        if "todoist_filters" in schema_reference:
            ref["Todoist Filters"] = schema_reference["todoist_filters"]
        out["Schema Reference"] = ref
    if "colors" in document:
        out["Color Palette"] = document["colors"]

    micro: dict[str, Any] = {}
    if "micro_adventure_pool" in document:
        micro["Pool"] = document["micro_adventure_pool"]
    rotation = document.get("micro_adventure_rotation")
    if isinstance(rotation, dict) and rotation:
        micro["Rotation"] = _expand_dot_notation(
            {f"rotation.{key}": value for key, value in rotation.items()}
        )
    if micro:
        out["Micro-Adventures"] = micro

    # ``Defaults`` is one flat dotted-key dict (plus a nested mirror); the
    # document spreads it over ``defaults`` (nested), ``habits`` and
    # ``todoist.read_query`` so each reads naturally in the file.
    flat: dict[str, Any] = {}
    if isinstance(document.get("defaults"), dict):
        flat.update(_flatten(document["defaults"]))
    habits = document.get("habits")
    if isinstance(habits, dict):
        flat.update({f"habits.{key}": value for key, value in habits.items()})
    todoist = document.get("todoist")
    read_query = todoist.get("read_query") if isinstance(todoist, dict) else None
    if isinstance(read_query, dict):
        flat.update({f"todoist.read_query.{key}": value for key, value in read_query.items()})
    if flat:
        out["Defaults"] = _expand_dot_notation(flat)

    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trimmed_keys(raw: Any) -> dict[str, Any]:
    """Return ``raw`` with its keys stripped of surrounding whitespace.

    JSON preserves a hand-typed leading/trailing space in a key, so
    ``" vault_enabled"`` looks correct to the operator while missing every
    exact-key lookup. Trimming is deliberate tolerance: the key is
    unambiguous once trimmed, and NOT trimming silently discards an explicit
    operator instruction (the S5 flip of 2026-10-09 wrote ``" vault_enabled"``
    and the vault stayed on with no warning).
    """
    if not isinstance(raw, dict):
        return {}
    return {str(k).strip(): v for k, v in raw.items()}


def _unrecognized_key_warnings(
    section: dict[str, Any], label: str, known: frozenset[str]
) -> list[str]:
    """Messages for keys in ``section`` that are not doing what they look like.

    Two cases, reported separately because they mean OPPOSITE things —
    conflating them would make the warning lie in one direction or the other:

    * **Unrecognized** — the *trimmed* key is not one the section knows, so
      the value is discarded and the operator's setting is silently not in
      effect. This is the case the warning exists for.
    * **Whitespace-padded** — the *trimmed* key IS known, so
      :func:`_trimmed_keys` made the value take effect. Reported as a cleanup
      note only. Claiming such a key was ignored would be false: the
      2026-10-09 key ``" vault_enabled"`` is honoured on read.

    Both report the key as written, so it can be found in the file."""
    unknown: list[str] = []
    padded: list[str] = []
    for key in section:
        written = str(key)
        trimmed = written.strip()
        if trimmed not in known:
            unknown.append(written)
        elif written != trimmed:
            padded.append(written)
    out: list[str] = []
    if unknown:
        written_list = ", ".join(f"'{key}'" for key in unknown)
        known_list = ", ".join(sorted(known))
        out.append(
            f"config.json {label} section has unrecognized key(s): {written_list} — "
            f"known keys are: {known_list}. Unrecognized keys are ignored, so the "
            "setting may not be in effect."
        )
    if padded:
        written_list = ", ".join(f"'{key}'" for key in padded)
        out.append(
            f"config.json {label} section key(s) {written_list} carry surrounding "
            "whitespace — the value was read with the whitespace stripped, so the "
            "setting is in effect, but the key should be renamed to match the "
            "section's other keys."
        )
    return out


def _coerce_bool(value: Any) -> bool | None:
    """Strict-ish boolean coercion for config flags. ``None`` when the value
    is absent or not an unambiguous boolean — callers then apply their default."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return None
    if type(value) is int and value in (0, 1):
        return bool(value)
    return None


def _flatten(nested: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested objects into dot-notation keys (``{"caps": {"deep": 4}}``
    becomes ``{"caps.deep": 4}``); non-object values are leaves."""
    out: dict[str, Any] = {}
    for key, value in nested.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{dotted}."))
        else:
            out[dotted] = value
    return out


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _expand_dot_notation(flat: dict[str, Any]) -> dict[str, Any]:
    """Mirror of ``config_reader._expand_dot_notation`` (kept local to avoid a
    circular import): keep the dotted keys and add a nested-dict view."""
    nested: dict[str, Any] = dict(flat)
    for key, value in flat.items():
        if "." in key:
            parts = key.split(".")
            cursor = nested
            for part in parts[:-1]:
                cursor = cursor.setdefault(part, {})
                if not isinstance(cursor, dict):
                    break
            else:
                cursor[parts[-1]] = value
    return nested


def _ignore_section(ignore: Any) -> dict[str, Any]:
    """Rebuild the ``## Ignore List`` subsection shape from the canonical
    lists, so ``get_ignore_list`` reads it back unchanged."""
    if not isinstance(ignore, dict):
        return {}
    return {
        "Todoist (by ID)": [{"ID": str(v)} for v in _as_list(ignore.get("todoist_ids"))],
        "Obsidian (by path)": [{"Path": str(v)} for v in _as_list(ignore.get("paths"))],
        "Names": [{"Name": str(v)} for v in _as_list(ignore.get("names"))],
    }
