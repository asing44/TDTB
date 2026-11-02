"""capacity.py — canonical capacity model (ui-parity T3 / cockpit T18c).

Verbatim port of the skill's `Capacity & Sequencing (canonical)` math
(SKILL.md 757–778) and readout strings. Pure — no I/O.

Invariants:
- Segment order: Fixed → Anchored → Habits → Mint → Selected → Buffer → Free.
- ``buffer = max(0, ceil(max(0, total − fixed − anchored − habits − mint + overlap) × pct))``.
- ``free`` is SIGNED and never clamped — OVERASSIGNED (advisory) fires on
  free < 0; only segment *widths* clamp, and widths are a view concern.
- ``overlap`` is the one clock-time credit: minutes claimed by more than one
  fixed-time segment (calendar vs anchors, meeting vs meeting) are counted
  once. Segments keep their own sizes; overlap returns the shared amount to
  free and task room. Never negative, never larger than what it credits.
- Readout words: "left" / "over", token "blk", legend word "Selected".

Gate: TDD — tests/test_capacity.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


Interval = tuple[int, int]


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Sorted, non-overlapping copy of (start, end) minute intervals.

    Empty and inverted spans drop out, touching spans join, so the result is
    the wall-clock footprint once — the basis of every overlap count."""
    out: list[Interval] = []
    for a, b in sorted(i for i in intervals if i[1] > i[0]):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def interval_minutes(intervals: list[Interval]) -> int:
    """Minutes covered by the union of ``intervals`` — shared time counted once."""
    return sum(b - a for a, b in merge_intervals(intervals))


def free_spans(span: Interval, busy: list[Interval]) -> list[Interval]:
    """The stretches of ``span`` that ``busy`` leaves unclaimed, in clock order."""
    start, end = span
    if end <= start:
        return []
    out: list[Interval] = []
    cursor = start
    for a, b in merge_intervals(busy):
        if b <= cursor:
            continue
        if a >= end:
            break
        if a > cursor:
            out.append((cursor, min(a, end)))
        cursor = max(cursor, b)
        if cursor >= end:
            break
    if cursor < end:
        out.append((cursor, end))
    return [(a, b) for a, b in out if b > a]


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
    overlap: int | float = 0      # shared clock time, already re-added to free

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
            "overlap": self.overlap,
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
    overlap: int | float = 0,
) -> Capacity:
    # Overlap is clock time two fixed segments both claimed; it returns to the
    # pool once instead of being deducted twice.
    credit = max(0, overlap)
    raw_remaining = max(0, total - fixed - anchored - habits - mint + credit)
    buffer = max(0, math.ceil(raw_remaining * buffering_pct))
    available = max(0, raw_remaining - buffer)
    free = total - fixed - anchored - habits - mint - buffer - selected + credit

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
    if credit:
        legend += f" · Shared {_fmt_blocks(credit)}"
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
        work_busy=work_busy, work_overflow=work_overflow, overlap=credit,
    )
