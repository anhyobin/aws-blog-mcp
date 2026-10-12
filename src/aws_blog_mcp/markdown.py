"""HTML-to-markdown conversion tuned for AWS blog bodies, plus fence-aware tidying and slicing."""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterator

from bs4 import Tag
from markdownify import MarkdownConverter

HARD_BREAK = "\x00"
"""Placeholder the converter emits for ``<br>``. Tidying works on it instead of on backslashes,
so literal backslashes in prose are never mistaken for line breaks; :func:`tidy` turns the
survivors into markdown hard breaks (a backslash + newline)."""

_QUOTE_PREFIX = r"[ \t]*(?:>[ \t]?)*"
_OPEN_FENCE_RE = re.compile(rf"^{_QUOTE_PREFIX}[ \t]*(?:(?:[-*+]|[0-9]+[.)])[ \t]+)?(`{{3,}})")
_QUOTE_PREFIX_RE = re.compile(rf"^{_QUOTE_PREFIX}")
_BACKTICKS_RE = re.compile(r"`{3,}")
# A short paragraph that is entirely bold works as a heading in many posts.
_HEADING_LIKE_RE = re.compile(r"^(?:#{1,6}[ \t]|\*\*[^*\n]{1,120}\*\*[ \t]*$)")


class BlogConverter(MarkdownConverter):
    """markdownify converter with code-language fences and table-safe cells."""

    def __init__(self) -> None:
        super().__init__(
            heading_style="ATX",
            bullets="-",
            strong_em_symbol="*",
            escape_asterisks=False,
            escape_underscores=False,
            escape_misc=False,
            newline_style="backslash",
            keep_inline_images_in=["td", "th", "a", "li", "p", "em", "strong", "b", "i"],
            table_infer_header=True,
            wrap=False,
        )

    def convert_td(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        """Keep each cell on one line and escape pipes."""
        text = text.strip().replace("\n", " ").replace("|", r"\|")
        text = re.sub(r"(\s*<br>\s*)+", "<br>", text).strip()
        text = re.sub(r"^(?:<br>)+|(?:<br>)+$", "", text)
        colspan = 1
        raw = el.get("colspan")
        if isinstance(raw, str) and raw.isascii() and raw.isdigit():
            colspan = max(1, min(1000, int(raw)))
        return " " + text + " |" * colspan

    convert_th = convert_td

    def convert_tr(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        """Ignore non-ASCII-digit colspans, which markdownify would ``int()`` (``'²'``)."""
        for cell in el.find_all(["td", "th"], recursive=False):
            raw = cell.get("colspan")
            if isinstance(raw, str) and not (raw.isascii() and raw.isdigit()):
                del cell["colspan"]
        return super().convert_tr(el, text, parent_tags)  # pyright: ignore[reportAttributeAccessIssue]

    def convert_br(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        """Emit the :data:`HARD_BREAK` placeholder instead of a backslash."""
        if "_inline" in parent_tags:
            return text + " " if text else " "
        return HARD_BREAK + "\n" + text

    def convert_pre(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        """Fence code with its language and a fence longer than any backtick run inside."""
        if not text.strip():
            return ""
        text = text.strip("\n")
        longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
        fence = "`" * max(3, longest + 1)
        lang = el.get("data-lang")
        return f"\n\n{fence}{lang if isinstance(lang, str) else ''}\n{text}\n{fence}\n\n"


def iter_blocks(md: str) -> Iterator[tuple[bool, str]]:
    """Yield ``(is_code, text)`` chunks, splitting fenced code blocks from prose.

    Fences may be indented (inside list items) or quoted (inside blockquotes); the closing fence
    is the next line that, without its quote prefix and whitespace, equals the opening fence.
    """
    lines = md.split("\n")
    closers: dict[str, list[int]] = {}
    for j, line in enumerate(lines):
        bare = _QUOTE_PREFIX_RE.sub("", line.replace(HARD_BREAK, "")).strip()
        if _BACKTICKS_RE.fullmatch(bare):
            closers.setdefault(bare, []).append(j)
    prose: list[str] = []
    i = 0
    while i < len(lines):
        match = _OPEN_FENCE_RE.match(lines[i])
        if match:
            candidates = closers.get(match.group(1), [])
            k = bisect.bisect_right(candidates, i)
            j = candidates[k] if k < len(candidates) else len(lines)
            if j < len(lines):
                if prose:
                    yield False, "\n".join(prose)
                    prose = []
                yield True, "\n".join(lines[i : j + 1])
                i = j + 1
                continue
        prose.append(lines[i])
        i += 1
    if prose:
        yield False, "\n".join(prose)


_BR = re.escape(HARD_BREAK)


def _tidy_prose(md: str) -> str:
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(rf"[ \t]+{_BR}", HARD_BREAK, md)
    md = re.sub(rf"(?m)^[ \t]*(?:>[ \t]*)*{_BR}\n", "", md)  # lines that are only a hard break
    md = re.sub(rf"{_BR}\n([ \t]*(?:>[ \t]*)*\n)", r"\n\1", md)  # hard break before a blank line
    md = re.sub(rf"{_BR}\n?$", "", md)  # hard break at the very end of a chunk
    md = re.sub(rf"{_BR}(?!\n)", " ", md)  # stray placeholders (e.g. collapsed inside headings)
    md = md.replace(HARD_BREAK, "\\")
    md = re.sub(r"[ \t]+\n", "\n", md)
    return re.sub(r"\n{3,}", "\n\n", md)


def tidy(md: str) -> str:
    """Clean whitespace in prose while leaving fenced code byte-for-byte intact.

    Hard breaks must be given as :data:`HARD_BREAK` (as :class:`BlogConverter` emits them); they
    are dropped where they would be redundant and written as a backslash + newline elsewhere.
    """
    out = ""
    for is_code, chunk in iter_blocks(md):
        if is_code:
            # Code never carries hard breaks; a placeholder here came from a <br> in a paragraph
            # that only looked like a fence.
            chunk = chunk.replace(HARD_BREAK + "\n", "\n").replace(HARD_BREAK, "").strip("\n")
        else:
            chunk = _tidy_prose(chunk).strip("\n")
        if not chunk.strip():
            continue
        if out:
            # Inside a blockquote, a bare blank line would end the quote: keep quoting it.
            quoted = _is_quoted(out.rsplit("\n", 1)[-1]) and _is_quoted(chunk.split("\n", 1)[0])
            if quoted:
                out = re.sub(r"(?:\n[ \t]*>[ \t]*)+$", "", out)
                chunk = re.sub(r"^(?:[ \t]*>[ \t]*\n)+", "", chunk)
            out += "\n>\n" if quoted else "\n\n"
        out += chunk
    return out + "\n" if out else ""


def _is_quoted(line: str) -> bool:
    return line.lstrip(" \t").startswith(">")


def code_spans(md: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` character offsets of fenced code blocks."""
    spans: list[tuple[int, int]] = []
    offset = 0
    for is_code, chunk in iter_blocks(md):
        if is_code:
            spans.append((offset, offset + len(chunk)))
        offset += len(chunk) + 1  # the newline consumed by the split
    return spans


def slice_markdown(md: str, start: int, max_length: int) -> tuple[str, int | None]:
    """Return ``(chunk, next_start)`` where ``chunk`` is at most ``max_length`` characters.

    The cut is placed at the last paragraph break outside fenced code within the window (but not
    in its first half); failing that at the last line break; failing that at ``max_length``.
    ``next_start`` is ``None`` when the chunk reaches the end. Concatenating all chunks gives back
    ``md`` exactly.
    """
    if start >= len(md):
        return "", None
    limit = start + max_length
    if limit >= len(md):
        return md[start:], None
    spans = code_spans(md)
    span_starts = [a for a, _ in spans]

    def code_start(pos: int) -> int | None:
        """Start of the code block containing ``pos``, if any."""
        k = bisect.bisect_right(span_starts, pos) - 1
        if k >= 0 and spans[k][0] < pos < spans[k][1]:
            return spans[k][0]
        return None

    def paragraph_break_before(end: int, lo: int) -> int:
        pos = md.rfind("\n\n", lo, end)
        while pos != -1 and (block := code_start(pos)) is not None:
            pos = md.rfind("\n\n", lo, block)
        return pos

    floor = start + max_length // 2
    cut = paragraph_break_before(limit, floor)
    if cut != -1:
        # Do not leave headings dangling at the end of a chunk; move them to the next one.
        while True:
            previous = paragraph_break_before(cut, start)
            if previous == -1:
                break
            if not _HEADING_LIKE_RE.match(md[previous + 2 : cut].lstrip("\n")):
                break
            cut = previous
        cut += 2
    else:
        newline = md.rfind("\n", floor, limit)
        cut = newline + 1 if newline != -1 else limit
    return md[start:cut], cut
