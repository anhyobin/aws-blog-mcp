"""Unit tests for ``aws_blog_mcp.text``."""

from __future__ import annotations

import pytest

from aws_blog_mcp.text import (
    EXCERPT_MAX_CHARS,
    clean_excerpt,
    clean_text,
    dedupe_names,
    split_authors,
)

# --- clean_text -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ("  lead and trail  ", "lead and trail"),
        ("a\n\tb \r\n  c", "a b c"),
        ("A &amp; B &lt;tag&gt; &quot;q&quot; &#39;s&#39;", "A & B <tag> \"q\" 's'"),
        ("non&nbsp;breaking", "non breaking"),
        ("&hellip;", "…"),
        ("", ""),
        ("   ", ""),
        # Decomposed input (NFD jamo, combining accents) comes out composed (NFC).
        ("\u1100\u1161\u11a8 \u1112\u1161\u11ab", "각 한"),
        ("cafe\u0301", "caf\xe9"),
        ("&#x1100;&#x1161;", "가"),  # composed after entities are unescaped
        ("ＡＷＳ", "ＡＷＳ"),  # NFC, not NFKC: full-width forms are kept
    ],
)
def test_clean_text(raw: str, expected: str) -> None:
    assert clean_text(raw) == expected


# --- split_authors ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Test Author", ("Test Author",)),
        ("Kyutae Park, Ph.D, Changwoo In", ("Kyutae Park, Ph.D", "Changwoo In")),
        ("Kyutae Park, Ph.D", ("Kyutae Park, Ph.D",)),
        ("Kyutae Park, Ph.D.", ("Kyutae Park, Ph.D.",)),
        ("Kyutae Park, Ph. D.", ("Kyutae Park, Ph. D.",)),
        ("Kyutae Park, PhD, Changwoo In", ("Kyutae Park, PhD", "Changwoo In")),
        ("Kyutae Park, ph.d, Changwoo In", ("Kyutae Park, ph.d", "Changwoo In")),
        (
            "Kyutae Park, Ph.D, Changwoo In, Ph.D.",
            ("Kyutae Park, Ph.D", "Changwoo In, Ph.D."),
        ),
        ("John Smith, Jr., Jane Doe", ("John Smith, Jr.", "Jane Doe")),
        ("Jane Doe, MBA, Kim Lee, M.D", ("Jane Doe, MBA", "Kim Lee, M.D")),
        ("A, B, and C", ("A", "B", "C")),
        ("A, B and C", ("A", "B", "C")),
        ("A and B", ("A", "B")),
        ("A, and B", ("A", "B")),
        ("A,\n  B", ("A", "B")),
        ("A,, B,", ("A", "B")),
        # "and" inside a name is not a separator.
        ("Andrew Anderson and Sandy Brandon", ("Andrew Anderson", "Sandy Brandon")),
        ("김철수, 이영희", ("김철수", "이영희")),
        ("Kim &amp; Lee", ("Kim & Lee",)),
        # A degree with no name before it stays on its own.
        ("Ph.D, Alice", ("Ph.D", "Alice")),
        ("", ()),
        ("  ,  , ", ()),
    ],
)
def test_split_authors(raw: str, expected: tuple[str, ...]) -> None:
    assert split_authors(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Alice Kim, alice kim, Bob Lee", ("Alice Kim", "Bob Lee")),
        ("alice kim and ALICE KIM", ("alice kim",)),
        ("Bob, Alice, Bob", ("Bob", "Alice")),
        ("Kyutae Park, Ph.D, kyutae park, ph.d", ("Kyutae Park, Ph.D",)),
        # The same name with and without a degree are different strings and both kept.
        ("Kyutae Park, Kyutae Park, Ph.D", ("Kyutae Park", "Kyutae Park, Ph.D")),
    ],
)
def test_split_authors_dedupes_case_insensitively(raw: str, expected: tuple[str, ...]) -> None:
    assert split_authors(raw) == expected


def test_dedupe_names() -> None:
    assert dedupe_names(["B", "a", "A", "b", "c"]) == ("B", "a", "c")
    assert dedupe_names(("Straße", "STRASSE")) == ("Straße",)
    assert dedupe_names([]) == ()


# --- clean_excerpt ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Hello world [...]", "Hello world…"),
        ("Hello world […]", "Hello world…"),
        ("Hello world [&hellip;]", "Hello world…"),
        ("Hello world[...]", "Hello world…"),
        ("Hello world [...]   \n", "Hello world…"),
        ("  Hello \n\t world  ", "Hello world"),
        ("Hello [...] world", "Hello [...] world"),  # only a trailing marker is replaced
        ("Hello [..]", "Hello [..]"),
        ("[...]", "…"),
        ("", ""),
        ("&lt;b&gt; tag &amp; text", "<b> tag & text"),
    ],
)
def test_clean_excerpt_marker_and_whitespace(raw: str, expected: str) -> None:
    assert clean_excerpt(raw) == expected


@pytest.mark.parametrize(
    ("raw", "limit", "expected"),
    [
        ("abcde", 5, "abcde"),  # exactly the limit: untouched
        ("abc def ghi", 5, "abc…"),  # cut back to the last word boundary
        ("abc def", 7, "abc def"),
        ("abc def ghi", 8, "abc def…"),
        ("abcdefghij", 5, "abcde…"),  # no space: hard cut
        ("abc, def ghi", 5, "abc…"),  # trailing punctuation before the ellipsis is dropped
        ("abc. def ghi", 6, "abc…"),
        ("가나다라마바사아", 4, "가나다라…"),
        ("가나 다라 마바", 6, "가나 다라…"),
    ],
)
def test_clean_excerpt_length_cap(raw: str, limit: int, expected: str) -> None:
    assert clean_excerpt(raw, limit=limit) == expected


def test_clean_excerpt_default_limit() -> None:
    assert EXCERPT_MAX_CHARS == 300
    text = "word " * 100
    expected = " ".join(["word"] * 60) + "…"
    assert clean_excerpt(text) == expected
    assert len(expected) == 300
    assert clean_excerpt("x" * 300) == "x" * 300
    assert clean_excerpt("x" * 301) == "x" * 300 + "…"


def test_clean_excerpt_marker_and_cap_give_one_ellipsis() -> None:
    text = "word " * 70 + "[...]"
    assert clean_excerpt(text) == " ".join(["word"] * 60) + "…"
    assert clean_excerpt("abc def […]", limit=8) == "abc def…"


@pytest.mark.parametrize("limit", [1, 2, 5, 17, 50, 299])
def test_clean_excerpt_never_exceeds_limit_plus_ellipsis(limit: int) -> None:
    text = "Synthetic excerpt, with: punctuation; and words. " * 20 + "[...]"
    result = clean_excerpt(text, limit=limit)
    assert len(result) <= limit + 1
    assert result.endswith("…")
    assert result.count("…") == 1
    assert not result.endswith((" …", ",…", ".…", ";…", ":…"))


def test_clean_excerpt_keeps_word_ending_at_limit() -> None:
    # "abc def" fits in 7 characters and is followed by a space, so no word is cut.
    assert clean_excerpt("abc def ghi", limit=7) == "abc def…"


@pytest.mark.parametrize(
    ("raw", "limit", "expected"),
    [
        ("abc def ghi", 6, "abc…"),  # "def" would be cut: drop it
        ("abc def ghi", 8, "abc def…"),  # the limit falls on the space after "def"
        ("abc defghi", 7, "abc…"),
        ("abcdefghij", 5, "abcde…"),  # a single long word is cut hard
        ("가나다 라마바 사아", 7, "가나다 라마바…"),
    ],
)
def test_clean_excerpt_word_boundaries(raw: str, limit: int, expected: str) -> None:
    assert clean_excerpt(raw, limit=limit) == expected


def test_clean_excerpt_is_nfc_normalised() -> None:
    assert clean_excerpt("\u1100\u1161\u11a8 [...]") == "각…"
