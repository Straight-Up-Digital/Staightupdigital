"""Short display-safe text formatting for audit reports."""
from __future__ import annotations

import re


_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def trim_snippet(value: str, limit: int = 260) -> str:
    text = " ".join(str(value or "").split())
    if limit <= 0 or len(text) <= limit:
        return text
    boundary = None
    for match in _SENTENCE_END.finditer(text[: limit + 1]):
        if match.end() >= limit * 0.55:
            boundary = match.end()
    if boundary is None:
        boundary = text.rfind(" ", 0, limit)
    if boundary < 1:
        boundary = limit
    return text[:boundary].rstrip(" ,;:-") + "..."
