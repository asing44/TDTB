"""bake_in_diff.py — T18b: the bake-in verdict lens over a shadow diff.

Reuses ``shadow.diff_against_live`` (T13) verbatim — this module does **not**
reimplement diff logic, it only *reinterprets* a ``ShadowDiff`` the way the
bake-in protocol (``../bake-in-protocol.md`` § 2.1) requires. Two pure
functions:

  1. ``classify_bakein`` — remaps every ``ShadowDiffEntry`` classification
     (``no-op`` / ``would-create`` / ``would-update`` / ``conflict`` /
     ``unavailable``) onto the three bake-in verdicts (``AGREE`` /
     ``UNEXPLAINED`` / ``INCONCLUSIVE``), per the § 2.1 table.
  2. ``day_verdict`` — folds a ``BakeInVerdict`` plus the driver's T14 commit
     report (``recon_fail``, ``wrong_surface``) into the day-level enum
     (``KILL`` / ``DIFF`` / ``INCONCLUSIVE`` / ``PASS``) per § 2.2–2.3.

The one non-obvious rule (protocol § 2.1, the ⚠ callout) is the **Step B
carve-out**: ``diff_against_live`` *always* marks the daily-note patch
(Step B, ``action == "patch"``) as ``would-update`` once the plan section
already exists in the note (shadow.py:394, "section exists, will be
replaced") — post-commit, that section always exists, so this is expected
agreement, not drift. Without the carve-out every bake-in day would carry
at least one diff and the bar (protocol § 3) could never be met. The
carve-out is kept deliberately narrow: only ``would-update`` + Step B. A
Step B ``conflict`` ("daily note not found") is a real problem and stays
UNEXPLAINED — the carve-out never touches non-``would-update`` classes.

Nothing here does I/O; ``bake_in_run.py`` wires this against a real
manifest/live-state and appends the log row.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shadow import (
    CONFLICT,
    CREATE,
    NOOP,
    UNAVAILABLE,
    UPDATE,
    ManifestEntry,
    ShadowDiff,
)

# Bake-in verdict vocabulary (protocol § 2.1).
AGREE = "AGREE"
UNEXPLAINED = "UNEXPLAINED"
INCONCLUSIVE = "INCONCLUSIVE"

# Day-level verdict vocabulary (protocol § 2.3). SKIP is set by the runner
# when there's no driver for the day — never returned by day_verdict itself.
KILL = "KILL"
DIFF = "DIFF"
PASS = "PASS"

_SURFACE_LABEL = {"todoist": "Todoist", "calendar": "the calendar", "vault": "the vault"}


def _is_step_b_patch(manifest: ManifestEntry) -> bool:
    """The narrow carve-out predicate: Step B AND action == 'patch'. Checking
    both (rather than step alone) matches the protocol's "keep it narrow"
    instruction — a future step relabeled "B" for an unrelated action must
    not silently inherit the carve-out."""
    return manifest.step == "B" and manifest.action == "patch"


@dataclass(frozen=True)
class BakeInRow:
    """One manifest row's bake-in verdict. ``shadow_class`` preserves the
    original ``ShadowDiffEntry.classification`` so a row is traceable back to
    its raw diff; ``reason`` is written for humans adjudicating a DIFF day
    months later, not for the differ itself (the differ never self-marks a
    diff "explained" — protocol § 2.3)."""
    manifest: ManifestEntry
    shadow_class: str
    verdict: str
    reason: str


@dataclass(frozen=True)
class BakeInVerdict:
    rows: list[BakeInRow]

    def agree(self) -> int:
        return sum(1 for r in self.rows if r.verdict == AGREE)

    def unexplained(self) -> int:
        return sum(1 for r in self.rows if r.verdict == UNEXPLAINED)

    def inconclusive(self) -> int:
        return sum(1 for r in self.rows if r.verdict == INCONCLUSIVE)

    def unexplained_notes(self) -> list[str]:
        """One adjudicable line per UNEXPLAINED row, e.g.
        ``Step A 'Guitar': created, not in committed plan`` — names the step
        and item so the log's ``notes`` cell stands on its own without
        re-running the diff."""
        return [r.reason for r in self.rows if r.verdict == UNEXPLAINED]


def _reason_for(manifest: ManifestEntry, shadow_class: str, detail: dict[str, Any], verdict: str) -> str:
    step, name = manifest.step, manifest.name
    detail = detail or {}

    if verdict == AGREE:
        if shadow_class == NOOP:
            return f"Step {step} {name!r}: matches what was committed (no-op)"
        # would-update + Step B carve-out — the only other AGREE path.
        return f"Step {step} {name!r}: daily-note section replaced as expected (Step B carve-out)"

    if shadow_class == CREATE:
        where = _SURFACE_LABEL.get(manifest.system, manifest.system)
        extra = detail.get("reason")
        suffix = f" ({extra})" if extra else ""
        return f"Step {step} {name!r}: created in {where}, not in committed plan{suffix}"

    if shadow_class == UPDATE:
        due = detail.get("due_time")
        if isinstance(due, dict):
            return (
                f"Step {step} {name!r}: app wants due {due.get('new')}, "
                f"committed shows {due.get('old')}"
            )
        assigned = detail.get("assigned")
        if isinstance(assigned, dict):
            return (
                f"Step {step} {name!r}: app wants assigned={assigned.get('new')}, "
                f"committed shows {assigned.get('old')}"
            )
        return f"Step {step} {name!r}: differs from what was committed ({detail})"

    if shadow_class == CONFLICT:
        reason = detail.get("reason", "live state contradicts the manifest")
        return f"Step {step} {name!r}: conflict — {reason}"

    if shadow_class == UNAVAILABLE:
        reason = detail.get("reason", "surface unavailable")
        return f"Step {step} {name!r}: inconclusive — {reason}"

    return f"Step {step} {name!r}: unrecognized shadow classification {shadow_class!r}"  # pragma: no cover


def classify_bakein(diff: ShadowDiff) -> BakeInVerdict:
    """Reinterpret every ``ShadowDiffEntry`` through the bake-in lens
    (protocol § 2.1 table):

      no-op                          -> AGREE
      would-update + Step B patch    -> AGREE   (the carve-out)
      would-create                   -> UNEXPLAINED
      would-update (everything else) -> UNEXPLAINED
      conflict                       -> UNEXPLAINED  (incl. Step B conflict)
      unavailable                    -> INCONCLUSIVE
    """
    rows: list[BakeInRow] = []
    for entry in diff.entries:
        m = entry.manifest
        cls = entry.classification

        if cls == NOOP:
            verdict = AGREE
        elif cls == UPDATE and _is_step_b_patch(m):
            verdict = AGREE
        elif cls in (CREATE, UPDATE, CONFLICT):
            verdict = UNEXPLAINED
        elif cls == UNAVAILABLE:
            verdict = INCONCLUSIVE
        else:  # pragma: no cover — diff_against_live only emits the five above
            verdict = UNEXPLAINED

        reason = _reason_for(m, cls, entry.detail, verdict)
        rows.append(BakeInRow(manifest=m, shadow_class=cls, verdict=verdict, reason=reason))

    return BakeInVerdict(rows=rows)


def day_verdict(v: BakeInVerdict, *, recon_fail: int, wrong_surface: bool) -> str:
    """Day-level verdict (protocol § 2.2–2.3, § 4 kill-switch). Precedence,
    highest first: ``KILL`` > ``DIFF`` > ``INCONCLUSIVE`` > ``PASS``.

    - ``KILL`` — a reconciliation failure or wrong-surface write fired on the
      driver's T14 commit report. Blunt and unconditional: no partial credit
      even if every bake-in row otherwise AGREEs (§ 4).
    - ``DIFF`` — at least one UNEXPLAINED row.
    - ``INCONCLUSIVE`` — no UNEXPLAINED rows, but at least one surface
      degraded (INCONCLUSIVE row present). Neither passes nor fails the day
      (§ 3.3) — the runner is responsible for not advancing the pass-bar
      count on this verdict.
    - ``PASS`` — zero UNEXPLAINED, zero INCONCLUSIVE, no kill signal.

    ``SKIP`` is not returned here — it's the runner's call for a day with no
    driver at all (protocol § 1), outside what a diff can determine.
    """
    if recon_fail > 0 or wrong_surface:
        return KILL
    if v.unexplained() > 0:
        return DIFF
    if v.inconclusive() > 0:
        return INCONCLUSIVE
    return PASS
