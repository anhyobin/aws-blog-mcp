"""Small text helpers shared by the index and the page extractor."""

from __future__ import annotations

import html
import re
import unicodedata

EXCERPT_MAX_CHARS = 300
_DEGREE_SUFFIX_RE = re.compile(r"^(?:ph\.?\s?d\.?|phd|jr\.?|sr\.?|m\.?d\.?|m\.?s\.?|mba)$", re.I)


def clean_text(text: str) -> str:
    """Unescape entities, NFC-normalise and collapse all whitespace into single spaces.

    NFC matters because some titles arrive as decomposed Hangul jamo; collapsing line breaks
    guarantees a single line, so values cannot break the structure of tool output.
    """
    return " ".join(unicodedata.normalize("NFC", html.unescape(text)).split())


def split_authors(names: str) -> tuple[str, ...]:
    """Split a comma/"and"-separated author list, keeping degree suffixes with their name.

    ``"Kyutae Park, Ph.D, Changwoo In"`` -> ``("Kyutae Park, Ph.D", "Changwoo In")``;
    ``"A, B, and C"`` -> ``("A", "B", "C")``. Duplicates (case-insensitive) are dropped.
    """
    parts: list[str] = []
    for raw in re.split(r",|\s+and\s+", clean_text(names)):
        part = re.sub(r"^and\s+", "", raw.strip())
        if not part:
            continue
        if parts and _DEGREE_SUFFIX_RE.match(part):
            parts[-1] = f"{parts[-1]}, {part}"
        else:
            parts.append(part)
    return dedupe_names(parts)


def dedupe_names(names: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Drop case-insensitive duplicates, keeping the first spelling and order."""
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(name)
    return tuple(unique)


def clean_excerpt(text: str, limit: int = EXCERPT_MAX_CHARS) -> str:
    """Normalise whitespace, replace the trailing ``[...]`` with an ellipsis and cap the length."""
    text = clean_text(text)
    text = re.sub(r"\s*\[(?:\.\.\.|…)\]\s*$", "…", text)
    if len(text) > limit:
        head = text[:limit]
        if text[limit] != " " and " " in head:
            head = head.rsplit(" ", 1)[0]  # do not end on a partial word
        text = head.rstrip(" ,.;:…") + "…"
    return text
