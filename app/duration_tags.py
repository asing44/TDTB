"""Shared parsing for tags whose primary meaning is a duration.

Duration/classification tags are metadata, not co-scheduling relationships.
The same recognition rules are used by duration resolution, QuickTasks
absorption, and semantic placement so those paths cannot disagree about a
label such as ``🚀10min``.
"""
from __future__ import annotations

import re
from typing import Any


_DUR_RE = re.compile(r"^dur(\d+)$", re.IGNORECASE)
_QUICK_RE = re.compile(r"^🚀\s*(\d+)\s*min$", re.IGNORECASE)


def duration_tag_minutes(value: Any) -> int | None:
    """Return the encoded minutes for a recognized duration tag."""
    text = str(value or "").strip()
    match = _DUR_RE.fullmatch(text) or _QUICK_RE.fullmatch(text)
    return int(match.group(1)) if match else None


def is_duration_tag(value: Any) -> bool:
    """Whether ``value`` is a duration/classification tag."""
    return duration_tag_minutes(value) is not None


def is_quick_task_tag(value: Any) -> bool:
    """Whether ``value`` identifies an item for the QuickTasks block."""
    return _QUICK_RE.fullmatch(str(value or "").strip()) is not None
