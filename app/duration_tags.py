"""Shared parsing for tags whose primary meaning is a duration.

Duration/classification tags are metadata, not co-scheduling relationships.
The same recognition rules are used by duration resolution, QuickTasks
absorption, and semantic placement so those paths cannot disagree about a
label such as ``🚀10min``.

Recognition is a tri-state:

- not a duration tag at all (``None``);
- a duration tag with deterministic minutes (``(label, minutes)``);
- a duration tag that carries NO minutes (``(label, None)``), such as
  ``🐢 Multi-hour``. That label is duration/classification metadata — it must
  stay out of related-group formation — but it encodes no value, so duration
  resolution falls through to remembered memory and then the default instead
  of guessing.
"""
from __future__ import annotations

import re
from typing import Any


_DUR_RE = re.compile(r"^dur(\d+)$", re.IGNORECASE)
_QUICK_RE = re.compile(r"^🚀\s*(\d+)\s*min$", re.IGNORECASE)

# Word-based operator labels. The optional prefix accepts an emoji (or any
# other non-word, non-space run such as ``#``) plus spacing, so emoji-form
# variance — a dropped ZWJ or variation selector on ``🏃‍♂️`` — cannot silently
# break recognition. Every pattern stays fully anchored, and they are checked
# most-specific-first so ``Multi-hour`` can never resolve as ``Hour``.
_LABEL_PREFIX = r"[^\w\s]*\s*"
_LABEL_PATTERNS: tuple[tuple[re.Pattern[str], int | None], ...] = (
    (re.compile(rf"^{_LABEL_PREFIX}multi[-\s]?hour$", re.IGNORECASE), None),
    (re.compile(rf"^{_LABEL_PREFIX}half[-\s]?hour$", re.IGNORECASE), 30),
    (re.compile(rf"^{_LABEL_PREFIX}hour$", re.IGNORECASE), 60),
)


def recognize_duration_tag(value: Any) -> tuple[str, int | None] | None:
    """Recognize ``value`` as a duration/classification label.

    Returns ``(label, minutes)`` where ``label`` is the stripped source text.
    ``minutes is None`` means the label is duration metadata that encodes no
    deterministic minutes (``🐢 Multi-hour``); resolution must treat it as no
    tag source and fall through. Returns ``None`` when ``value`` is not a
    duration tag at all.
    """
    text = str(value or "").strip()
    for pattern, minutes in _LABEL_PATTERNS:
        if pattern.fullmatch(text):
            return text, minutes
    match = _DUR_RE.fullmatch(text) or _QUICK_RE.fullmatch(text)
    if match:
        return text, int(match.group(1))
    return None


def duration_tag_minutes(value: Any) -> int | None:
    """Return the encoded minutes for a recognized duration tag.

    ``None`` covers both "not a duration tag" and a recognized label that
    encodes no minutes; use :func:`recognize_duration_tag` to distinguish.
    """
    recognized = recognize_duration_tag(value)
    return recognized[1] if recognized is not None else None


def is_duration_tag(value: Any) -> bool:
    """Whether ``value`` is a duration/classification tag."""
    return recognize_duration_tag(value) is not None


def is_quick_task_tag(value: Any) -> bool:
    """Whether ``value`` identifies an item for the QuickTasks block."""
    return _QUICK_RE.fullmatch(str(value or "").strip()) is not None
