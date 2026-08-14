"""capacity.py — canonical capacity model (ui-parity T3 / cockpit T18c).

Verbatim port of the skill's `Capacity & Sequencing (canonical)` math
(SKILL.md 757–778) and readout strings. Pure — no I/O.

Invariants:
- Segment order: Fixed → Anchored → Habits → Mint → Selected → Buffer → Free.
- ``buffer = max(0, ceil(max(0, total − fixed − anchored − habits − mint) × pct))``.
- ``free`` is SIGNED and never clamped — OVERASSIGNED (advisory) fires on
  free < 0; only segment *widths* clamp, and widths are a view concern.
- Readout words: "left" / "over", token "blk", legend word "Selected".

Gate: TDD — tests/test_capacity.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


def _fmt_blocks(blocks: int | float) -> str:
    return str(int(blocks)) if float(blocks).is_integer() else f"{blocks:g}"


def _hrs_min(blocks: int | float) -> str:
    m = round(blocks * 30)
    if m < 60:
        return f"{m}min"
    return f"{m // 60}hr" if m % 60 == 0 else f"{m // 60}hr {m % 60}min"


@dataclass
class Capacity:
    total: int
    fixed: int
    anchored: int
    habits: int
    mint: int | float
    selected: int | float
    buffer: int
    free: int | float             # signed — never clamped
    overassigned: bool
    available_for_selection: int | float
    remaining: str                # canonical Remaining readout
    ratio: str                    # "used / total blk"
    legend: str
    counters: str                 # "deep: x / n · mixed: y / n"
    work_busy: int | float = 0
    work_overflow: int | float = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total, "fixed": self.fixed, "anchored": self.anchored,
            "habits": self.habits, "mint": self.mint,
            "selected": self.selected,
            "buffer": self.buffer, "free": self.free,
            "overassigned": self.overassigned,
            "available_for_selection": self.available_for_selection,
            "remaining": self.remaining, "ratio": self.ratio,
            "legend": self.legend, "counters": self.counters,
            "work_busy": self.work_busy, "work_overflow": self.work_overflow,
        }


def compute_capacity(
    total: int,
    fixed: int,
    anchored: int,
    habits: int,
    mint: int | float,
    selected: int | float,
    buffering_pct: float,
    deep_count: int = 0,
    mixed_count: int = 0,
    caps: dict[str, int] | None = None,
    habits_note: str | None = None,
    work_busy: int | float = 0,
    work_overflow: int | float = 0,
) -> Capacity:
    raw_remaining = max(0, total - fixed - anchored - habits - mint)
    buffer = max(0, math.ceil(raw_remaining * buffering_pct))
    available = max(0, raw_remaining - buffer)
    free = total - fixed - anchored - habits - mint - buffer - selected  # signed

    if free > 0:
        remaining = f"⬆ {_hrs_min(free)} left · {_fmt_blocks(free)} blk"
    elif free == 0:
        remaining = "⬆ fully booked · 0 blk left"
    else:
        remaining = f"⚠ {_hrs_min(-free)} over · {_fmt_blocks(-free)} blk"

    legend = (
        f"Fixed {_fmt_blocks(fixed)} · Anchored {_fmt_blocks(anchored)} "
        f"· Habits {_fmt_blocks(habits)} · Mint {_fmt_blocks(mint)} "
        f"· Selected {_fmt_blocks(selected)} "
        f"· Buffer {_fmt_blocks(buffer)} · Free {_fmt_blocks(free)} "
        f"· Total {_fmt_blocks(total)}"
    )
    if habits_note:
        legend += f" ({habits_note})"

    caps = caps or {}
    counters = (f"deep: {deep_count} / {caps.get('deep', 0)} "
                f"· mixed: {mixed_count} / {caps.get('mixed', 0)}")

    return Capacity(
        total=total, fixed=fixed, anchored=anchored, habits=habits, mint=mint,
        selected=selected, buffer=buffer, free=free, overassigned=free < 0,
        available_for_selection=available,
        remaining=remaining, ratio=f"{_fmt_blocks(total - free)} / {total} blk",
        legend=legend, counters=counters,
        work_busy=work_busy, work_overflow=work_overflow,
    )
