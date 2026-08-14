"""time_engine.py — anchor pin + effective-EOD scan (ui-parity T2).

Pure module, no I/O and no clock reads: the caller injects ``now``. Implements
the skill contract:

- **Anchor (0.2):** live clock rounded UP to the next ``anchor.round_to_minutes``
  boundary; a user override (Phase 1 Start Time edit) wins verbatim.
- **Effective EOD (0.4 hard-stop):** a fixed commitment starting within the
  2-hour window before config ``eod`` pins ``effective_eod`` to its start
  (earliest such start wins); a user EOD override wins and suppresses the scan.
- **Total / short-circuit:** ``total_blocks = floor((eod − anchor) / 30)``;
  ``no_time_left`` when ≤ 0 — Phase 1 must never render its bar on that.

Gate: TDD — tests/test_time_engine.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

BLOCK_MINUTES = 30
EOD_SCAN_WINDOW_MIN = 120  # fixed commitment within 2h before eod ends the day


def to_hhmm(value: Any) -> str | None:
    """Normalize a config time ("11:59 PM", "7:45 AM", "16:20") to 24h HH:MM.
    Junk ("—", empty, None) → None."""
    if not value:
        return None
    text = str(value).strip()
    m = re.match(r"^(\d{1,2}):(\d{2})(?:\s*([AP]M))?$", text, re.IGNORECASE)
    if not m:
        return None
    h, mnt, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    if ampm:
        ampm = ampm.upper()
        if ampm == "PM" and h != 12:
            h += 12
        elif ampm == "AM" and h == 12:
            h = 0
    if not (0 <= h <= 23 and 0 <= mnt <= 59):
        return None
    return f"{h:02d}:{mnt:02d}"


def duration_minutes(dur: Any) -> int | None:
    """Minutes from a Duration value: "80m", "1h20m", "2h", or bare
    int/str minutes. None when absent/unparseable. (G27: the old bare
    ``(\\d+)`` prefix match read "1h30m" as 1 minute. T22: promoted here
    from main.py so shadow's Step E parity can share one parser.)"""
    if dur is None:
        return None
    s = str(dur).strip()
    hm = re.search(r"(\d+)\s*h", s, re.IGNORECASE)
    mm = re.search(r"(\d+)\s*m", s, re.IGNORECASE)
    if hm or mm:
        return (int(hm.group(1)) if hm else 0) * 60 + (
            int(mm.group(1)) if mm else 0
        )
    m = re.match(r"\d+(?:\.\d+)?$", s)
    if m:
        return int(float(m.group(0)))
    return None


def _hhmm_to_min(hhmm: str) -> int:
    h, m = (int(p) for p in hhmm.split(":"))
    return h * 60 + m


def _min_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass
class TimeFrame:
    now: str                 # HH:MM, the injected clock
    anchor: str              # HH:MM, earliest schedulable moment
    effective_eod: str       # HH:MM, hard-stop aware
    eod_note: str | None     # "Real stop HH:MM — title" when scan fired
    config_eod: str
    total_blocks: int        # floor((eod − anchor) / 30); signed, not clamped
    no_time_left: bool       # total_blocks <= 0 → skip Phases 1–5

    def as_dict(self) -> dict[str, Any]:
        return {
            "now": self.now, "anchor": self.anchor,
            "effective_eod": self.effective_eod, "eod_note": self.eod_note,
            "config_eod": self.config_eod, "total_blocks": self.total_blocks,
            "no_time_left": self.no_time_left,
        }


def compute_time_frame(
    now: datetime,
    config_eod: str,
    round_to_minutes: int = 15,
    busy_events: list[dict[str, Any]] | None = None,
    anchor_override: str | None = None,
    eod_override: str | None = None,
) -> TimeFrame:
    """Compute the day frame. ``busy_events`` are fixed commitments as
    ``{"start": "HH:MM", "title": str}`` — caller pre-filters to the
    commitment calendars (Personal / Family / Trinoor), never Blocks."""
    now_min = now.hour * 60 + now.minute

    if anchor_override:
        anchor_min = _hhmm_to_min(anchor_override)
    else:
        step = max(1, round_to_minutes)
        anchor_min = ((now_min + step - 1) // step) * step

    eod_note: str | None = None
    if eod_override:
        eod_min = _hhmm_to_min(eod_override)
    else:
        eod_min = _hhmm_to_min(config_eod)
        window_start = eod_min - EOD_SCAN_WINDOW_MIN
        in_window = sorted(
            (e for e in (busy_events or [])
             if e.get("start") and window_start <= _hhmm_to_min(e["start"]) < eod_min),
            key=lambda e: _hhmm_to_min(e["start"]),
        )
        if in_window:
            hit = in_window[0]
            eod_min = _hhmm_to_min(hit["start"])
            eod_note = f"Real stop {hit['start']} — {hit.get('title', '?')}"

    total = (eod_min - anchor_min) // BLOCK_MINUTES if eod_min > anchor_min else (
        0 if eod_min == anchor_min else -((anchor_min - eod_min) // BLOCK_MINUTES)
    )
    return TimeFrame(
        now=_min_to_hhmm(now_min),
        anchor=_min_to_hhmm(anchor_min),
        effective_eod=_min_to_hhmm(eod_min),
        eod_note=eod_note,
        config_eod=config_eod,
        total_blocks=total,
        no_time_left=total <= 0,
    )
