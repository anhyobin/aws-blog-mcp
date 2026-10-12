"""Unit tests for ``aws_blog_mcp.markdown`` (conversion, tidying and slicing)."""

from __future__ import annotations

import random
import time

import pytest
from bs4 import BeautifulSoup

from aws_blog_mcp.extract import render_body
from aws_blog_mcp.markdown import (
    HARD_BREAK,
    BlogConverter,
    code_spans,
    iter_blocks,
    slice_markdown,
    tidy,
)

BR = HARD_BREAK

BASE = "https://aws.amazon.com/ko/blogs/tech/"


def convert(html: str) -> str:
    return BlogConverter().convert_soup(BeautifulSoup(html, "lxml"))


def chunks_of(md: str, max_length: int, start: int = 0) -> list[str]:
    """Slice ``md`` from ``start`` to the end, checking the per-call contract along the way."""
    out: list[str] = []
    pos = start
    while True:
        chunk, nxt = slice_markdown(md, pos, max_length)
        assert len(chunk) <= max_length
        assert md[pos : pos + len(chunk)] == chunk
        out.append(chunk)
        if nxt is None:
            assert pos + len(chunk) == len(md)
            return out
        assert nxt == pos + len(chunk)
        assert nxt > pos, "no progress"
        pos = nxt


# --- BlogConverter: code ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('<pre data-lang="python">print(1)\n</pre>', "```python\nprint(1)\n```"),
        ("<pre>no lang</pre>", "```\nno lang\n```"),
        ('<pre data-lang="python">\n\n    indented()\n\n</pre>', "```python\n    indented()\n```"),
        ("<pre>a &lt; b &amp;&amp; c</pre>", "```\na < b && c\n```"),
        ("<pre>  \n \n</pre>", ""),
        ("<pre></pre>", ""),
    ],
)
def test_code_fences(html: str, expected: str) -> None:
    assert convert(html) == expected


@pytest.mark.parametrize(
    ("code", "fence"),
    [
        ("plain", "```"),
        ("`x`", "```"),
        ("``x``", "```"),
        ("a ``` b", "````"),
        ("a ``` b ````` c", "``````"),
        ("```\nnested fence\n```", "````"),
    ],
)
def test_code_fence_is_longer_than_any_backtick_run(code: str, fence: str) -> None:
    assert convert(f'<pre data-lang="md">{code}</pre>') == f"{fence}md\n{code}\n{fence}"


def test_code_survives_conversion_and_tidy_byte_for_byte() -> None:
    code = 'pip install x \\\n  --upgrade   \n\n\n\techo "a  b"  \\\nend'
    html = f'<p>Run:</p><pre data-lang="bash">{code}\n</pre><p>Done.</p>'
    expected = f"Run:\n\n```bash\n{code}\n```\n\nDone."
    assert convert(html) == expected
    assert tidy(convert(html)) == expected + "\n"


@pytest.mark.parametrize(
    ("html", "lang"),
    [
        ('<pre class="lang-python">x = 1</pre>', "python"),
        ('<pre class="language-YAML">a: 1</pre>', "yaml"),
        ('<pre><code class="language-JavaScript">let a</code></pre>', "javascript"),
        ('<pre><code class="lang-c++">int a;</code></pre>', "c++"),
        ('<div class="hide-language"><pre class="lang-sql">select 1</pre></div>', "sql"),
        ('<pre class="brush: bash">ls</pre>', ""),
        ("<pre>plain</pre>", ""),
    ],
)
def test_language_from_classes_via_render_body(html: str, lang: str) -> None:
    md = render_body(f'<section class="blog-post-content">{html}</section>', BASE)
    assert md.startswith(f"```{lang}\n")
    assert md.endswith("\n```\n")


# --- BlogConverter: tables --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (
            "<table><tr><th>Name</th><th>Value</th></tr>"
            "<tr><td>a|b</td><td>x &lt;br&gt; &lt;br&gt;  y</td></tr></table>",
            "| Name | Value |\n| --- | --- |\n| a\\|b | x<br>y |",
        ),
        (
            "<table><tr><th>H</th></tr><tr><td>&lt;br&gt; lead</td></tr>"
            "<tr><td>trail &lt;br&gt;</td></tr><tr><td>multi\nline\ncell</td></tr></table>",
            "| H |\n| --- |\n| lead |\n| trail |\n| multi line cell |",
        ),
        (
            "<table><tr><th>A</th><th>B</th><th>C</th></tr><tr><td colspan='3'>wide</td></tr>"
            "<tr><td colspan='x'>a</td><td colspan='0'>b</td><td colspan='-2'>c</td></tr></table>",
            "| A | B | C |\n| --- | --- | --- |\n| wide | | |\n| a | b | c |",
        ),
        (
            "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>",
            "| a | b |\n| --- | --- |\n| c | d |",
        ),
        (
            "<table><tr><th>A|B|C</th></tr><tr><td>&lt;br&gt;&lt;br&gt;</td></tr></table>",
            "| A\\|B\\|C |\n| --- |\n|  |",
        ),
    ],
)
def test_table_cells(html: str, expected: str) -> None:
    assert convert(html) == expected


def test_table_colspan_is_capped() -> None:
    first_row = convert("<table><tr><td colspan='5000'>c</td></tr></table>").split("\n")[0]
    assert first_row.count("|") == 1 + 1000


@pytest.mark.parametrize("colspan", ["五", "x", "", "-1"])
def test_non_digit_colspan_counts_as_one(colspan: str) -> None:
    html = (
        f"<table><tr><th colspan='{colspan}'>A</th><th>B</th></tr>"
        f"<tr><td colspan='{colspan}'>c</td><td>d</td></tr></table>"
    )
    assert convert(html) == "| A | B |\n| --- | --- |\n| c | d |"


@pytest.mark.parametrize(
    ("colspan", "row"), [("²", "header"), ("²", "body"), ("٣", "header"), ("２", "header")]
)
def test_unicode_digit_colspan_counts_as_one(colspan: str, row: str) -> None:
    head = f"<th colspan='{colspan}'>A</th>" if row == "header" else "<th>A</th>"
    cell = f"<td colspan='{colspan}'>c</td>" if row == "body" else "<td>c</td>"
    html = f"<table><tr>{head}<th>B</th></tr><tr>{cell}<td>d</td></tr></table>"
    assert convert(html) == "| A | B |\n| --- | --- |\n| c | d |"


def test_hard_break_placeholder_is_nul() -> None:
    assert HARD_BREAK == "\x00"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<h2>Title<br>Sub</h2>", "## Title Sub\n"),
        ("<h3><br>Lead</h3>", "### Lead\n"),
        ("<p>a<br>b<br><br>c</p>", "a\\\nb\\\nc\n"),
    ],
)
def test_line_breaks_in_headings_and_paragraphs(html: str, expected: str) -> None:
    assert tidy(convert(html)) == expected


def test_misc_conversion_settings() -> None:
    html = "<h2>Title</h2><p>text<br>more a_b *c*</p><ul><li>one</li><li>two</li></ul>"
    assert convert(html) == f"## Title\n\ntext{BR}\nmore a_b *c*\n\n- one\n- two"
    assert tidy(convert(html)) == "## Title\n\ntext\\\nmore a_b *c*\n\n- one\n- two\n"


# --- iter_blocks / code_spans -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("md", "blocks"),
    [
        ("", [(False, "")]),
        ("just prose\n\nmore", [(False, "just prose\n\nmore")]),
        (
            "intro\n\n```py\nx\n\ny\n```\n\nafter",
            [(False, "intro\n"), (True, "```py\nx\n\ny\n```"), (False, "\nafter")],
        ),
        ("```\na\n```", [(True, "```\na\n```")]),
        ("```\na\n```\n", [(True, "```\na\n```"), (False, "")]),
        ("```\na\n```\n```\nb\n```", [(True, "```\na\n```"), (True, "```\nb\n```")]),
        # The closing fence must be the same run; a shorter one is content.
        ("````\n```\ninner\n```\n````", [(True, "````\n```\ninner\n```\n````")]),
        # Closing fences may be indented or have trailing spaces.
        ("```\nx\n  ```  \ntail", [(True, "```\nx\n  ```  "), (False, "tail")]),
        # Fences inside list items.
        (
            "- ```bash\n  ls\n  ```\n1. ```\n   y\n   ```",
            [
                (True, "- ```bash\n  ls\n  ```"),
                (True, "1. ```\n   y\n   ```"),
            ],
        ),
        # An unclosed fence is prose.
        ("```\nnever closed\n\nstill prose", [(False, "```\nnever closed\n\nstill prose")]),
        ("text ```inline``` text", [(False, "text ```inline``` text")]),
        ("``\nnot a fence\n``", [(False, "``\nnot a fence\n``")]),
    ],
)
def test_iter_blocks(md: str, blocks: list[tuple[bool, str]]) -> None:
    result = list(iter_blocks(md))
    assert result == blocks
    assert "\n".join(text for _, text in result) == md


@pytest.mark.parametrize(
    ("md", "spans"),
    [
        ("", []),
        ("prose only", []),
        ("```\na\n```", [(0, 9)]),
        ("intro\n\n```py\nx\n\ny\n```\n\nafter", [(7, 21)]),
        ("```\na\n```\n```\nb\n```", [(0, 9), (10, 19)]),
        ("p\n```\na\n```\nq\n\n````\n```\n````\n", [(2, 11), (15, 28)]),
        ("```\nunclosed", []),
    ],
)
def test_code_spans(md: str, spans: list[tuple[int, int]]) -> None:
    assert code_spans(md) == spans
    for a, b in spans:
        block = md[a:b]
        assert block.lstrip(" -*+0123456789.)").startswith("```")
        assert block.rstrip().endswith("```")


# --- tidy -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("md", "expected"),
    [
        ("", ""),
        ("   \n\n\n", ""),
        ("text", "text\n"),
        ("\n\n\ntext\n\n\n", "text\n"),
        ("para one  \n\n\n\npara two\t\nnext", "para one\n\npara two\nnext\n"),
        (f"line {BR}\nnext", "line\\\nnext\n"),  # space before a hard break
        (f"{BR}\nfoo", "foo\n"),  # a line that is only a hard break
        (f"a\n{BR}\nb", "a\nb\n"),
        (f"a {BR}\n\nb", "a\n\nb\n"),  # hard break right before a blank line
        (f"end {BR}\n", "end\n"),  # hard break at the very end
        (f"> quote {BR}\n>\n> more", "> quote\n>\n> more\n"),
        (f"> a{BR}\n> {BR}\n> b", "> a\\\n> b\n"),  # quoted break-only line is dropped
        ("C:\\Users\\", "C:\\Users\\\n"),  # literal backslashes are not hard breaks
        ("line \\\nnext", "line \\\nnext\n"),
        ("end \\\n\nnext", "end \\\n\nnext\n"),  # not dropped before a blank line
        ("\\\nfoo", "\\\nfoo\n"),  # a line that is only a backslash is kept
        ("> q \\\n>\n> r", "> q \\\n>\n> r\n"),
        ("trailing \\", "trailing \\\n"),  # not dropped at the end either
        (f"a{BR}b", "a b\n"),  # a stray placeholder without its newline becomes a space
    ],
)
def test_tidy_prose(md: str, expected: str) -> None:
    assert tidy(md) == expected


@pytest.mark.parametrize(
    "code",
    [
        "```bash\necho a \\\n  --flag   \n\n\n\nend \\\n```",
        "```\n\n\n\n```",
        "```\ntrailing\t\t\n   \n```",
        "````\n```\ninner  \n\n\n```\n````",
        "- ```python\n  x = 1   \n\n\n  y = 2 \\\n  ```",
    ],
)
def test_tidy_keeps_code_byte_identical(code: str) -> None:
    md = f"intro  \n\n\n\n{code}\n\n\n\nafter {BR}\n"
    assert tidy(md) == f"intro\n\n{code}\n\nafter\n"


def test_literal_backslashes_survive_render_body() -> None:
    html = "<p>C:\\path\\</p><p>regex \\d+\\</p><p>next<br>line</p>"
    md = render_body(f'<section class="blog-post-content">{html}</section>', BASE)
    assert md == "C:\\path\\\n\nregex \\d+\\\n\nnext\\\nline\n"


@pytest.mark.parametrize(
    "code",
    [
        "> ```bash\n> code   \n>\n>\n>   indented \\\n> ```",
        ">> ```\n>> deep  \n>>\n>> ```",
        "> - ```py\n>   x = 1  \n>\n>\n>   ```",
    ],
    ids=["quote", "nested-quote", "list-in-quote"],
)
def test_quoted_fenced_code_is_untouched(code: str) -> None:
    md = f"> intro  \n>\n{code}\n\n\n\nafter  \n"
    blocks = list(iter_blocks(md))
    assert (True, code) in blocks
    assert code in tidy(md)
    assert tidy(md).startswith("> intro\n")


def test_quoted_fence_closer_must_match_the_opener() -> None:
    md = "> ````\n> ```\n> inner\n> ```\n> ````\nafter"
    assert list(iter_blocks(md)) == [
        (True, "> ````\n> ```\n> inner\n> ```\n> ````"),
        (False, "after"),
    ]


@pytest.mark.parametrize(
    "line", ["```bash", "```", "> ```py", "- ```"], ids=["lang", "bare", "quoted", "list"]
)
def test_iter_blocks_is_linear_for_many_unclosed_fences(line: str) -> None:
    # Bare fences pair up; the others never close. Either way every line is visited once.
    md = "\n".join([line] * 8000)
    started = time.perf_counter()
    blocks = list(iter_blocks(md))
    tidy(md)
    code_spans(md)
    slice_markdown(md, 0, 1000)
    elapsed = time.perf_counter() - started
    assert "\n".join(text for _, text in blocks) == md
    assert elapsed < 0.5, f"{elapsed:.2f}s"


def test_tidy_unclosed_fence_is_tidied_as_prose() -> None:
    assert tidy("```\nunclosed  \n\n\n\nfence") == "```\nunclosed\n\nfence\n"


@pytest.mark.parametrize(
    "md",
    [
        "a  \n\n\n\nb \\\nc\n\n```\nx  \n\n\ny \\\n```\n\n\nd \\\n",
        "> q \\\n>\n> r\n\n- item \\\n  more\n",
        "\n\n```\n\n```\n\n",
        "",
    ],
)
def test_tidy_is_idempotent(md: str) -> None:
    once = tidy(md)
    assert tidy(once) == once


@pytest.mark.parametrize(
    ("md", "expected"),
    [
        (f"para {BR}\n```\ncode\n```", "para\n\n```\ncode\n```\n"),  # chunk ends before a fence
        (f"a\n\nDone {BR}", "a\n\nDone\n"),  # document ends without a newline
    ],
)
def test_tidy_final_hard_break_leaves_no_trailing_space(md: str, expected: str) -> None:
    assert tidy(md) == expected


@pytest.mark.parametrize(
    "html",
    [
        "<blockquote><p>a<br><br>b</p></blockquote>",
        "<blockquote><p>a</p><p><br>b</p></blockquote>",
        "<blockquote>a<br>\n<br>\nb</blockquote>",
    ],
)
def test_tidy_quote_hard_break_line_does_not_nest_quote(html: str) -> None:
    md = tidy(convert(html))
    assert ">>" not in md
    assert md.splitlines()[0] in ("> a", "> a\\")
    assert md.splitlines()[-1] == "> b"


# --- slice_markdown: basics -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("md", "start"),
    [("", 0), ("", 5), ("abc", 3), ("abc", 4), ("abc", 1000)],
)
def test_slice_start_at_or_beyond_end(md: str, start: int) -> None:
    assert slice_markdown(md, start, 10) == ("", None)


def test_slice_rest_fits() -> None:
    md = "Para one.\n\nPara two."
    assert slice_markdown(md, 0, len(md)) == (md, None)
    assert slice_markdown(md, 0, 10_000) == (md, None)
    assert slice_markdown(md, 11, 9) == ("Para two.", None)


PARAS = "Para one is here.\n\nPara two is here.\n\nPara three."


@pytest.mark.parametrize(
    ("max_length", "expected"),
    [
        (5, ("Para ", 5)),  # no line break at all: hard cut
        (10, ("Para one i", 10)),
        (18, ("Para one is here.\n", 18)),  # a line break but no full paragraph break
        (19, ("Para one is here.\n\n", 19)),
        (36, ("Para one is here.\n\n", 19)),
        (37, ("Para one is here.\n\nPara two is here.\n", 37)),  # break would be in first half
        (38, ("Para one is here.\n\nPara two is here.\n\n", 38)),
        (47, ("Para one is here.\n\nPara two is here.\n\n", 38)),
    ],
)
def test_slice_cut_points(max_length: int, expected: tuple[str, int]) -> None:
    assert slice_markdown(PARAS, 0, max_length) == expected


def test_slice_from_middle() -> None:
    assert slice_markdown(PARAS, 19, 20) == ("Para two is here.\n\n", 38)
    assert slice_markdown(PARAS, 38, 5) == ("Para ", 43)


# --- slice_markdown: fences -------------------------------------------------------------------

FENCED_INTRO = "P" * 30 + "\n\n"
FENCED = FENCED_INTRO + "```\n" + "x\n\n" * 10 + "```\n\nafter"


@pytest.mark.parametrize("max_length", range(33, 62))
def test_slice_does_not_cut_inside_code_when_a_paragraph_break_exists(max_length: int) -> None:
    assert slice_markdown(FENCED, 0, max_length) == (FENCED_INTRO, len(FENCED_INTRO))


def test_slice_after_code_block() -> None:
    end_of_code = FENCED.index("```\n\nafter") + 3
    chunk, nxt = slice_markdown(FENCED, len(FENCED_INTRO), end_of_code - len(FENCED_INTRO) + 2)
    assert chunk == FENCED[len(FENCED_INTRO) : end_of_code + 2]
    assert chunk.endswith("```\n\n")
    assert nxt == end_of_code + 2


def test_slice_falls_back_to_line_break_inside_long_code() -> None:
    md = "```\n" + "line\n\n" * 50 + "```\n"
    chunk, nxt = slice_markdown(md, 0, 40)
    # No paragraph break outside code: cut at the last line break in the window.
    assert chunk == md[:40]
    assert chunk.endswith("\n")
    assert nxt == 40


# --- slice_markdown: headings -----------------------------------------------------------------

HEADED = "Intro paragraph text.\n\n## Heading\n\nBody paragraph that is long enough to spill."


@pytest.mark.parametrize("max_length", range(35, 44))
def test_slice_moves_trailing_heading_to_next_chunk(max_length: int) -> None:
    chunk, nxt = slice_markdown(HEADED, 0, max_length)
    assert (chunk, nxt) == ("Intro paragraph text.\n\n", 23)
    assert HEADED[23:].startswith("## Heading\n\nBody")


def test_slice_keeps_heading_when_it_opens_the_chunk() -> None:
    md = "## Heading\n\nBody text that is quite long here.\n\nMore."
    # The heading is the only block before the cut, so there is nowhere to move it.
    assert slice_markdown(md, 0, 49) == ("## Heading\n\nBody text that is quite long here.\n\n", 48)


def test_slice_moves_stacked_headings_to_next_chunk() -> None:
    md = (
        "First paragraph.\n\nSecond paragraph.\n\n## Section\n\n### Sub\n\n"
        "Body text long enough to not fit in the window at all here."
    )
    # A break after "Second paragraph." (index 35) is past the window's first half (30).
    assert slice_markdown(md, 0, 60) == ("First paragraph.\n\nSecond paragraph.\n\n", 37)


# --- slice_markdown: round trip ---------------------------------------------------------------


def _sample_document() -> tuple[str, list[tuple[int, int]]]:
    """A tidy-looking document plus the offsets of its code blocks, built independently."""
    parts: list[tuple[bool, str]] = [
        (False, "# 제목\n\n쿠버네티스 클러스터를 운영하는 방법을 설명합니다."),
        (False, "## 준비\n\n다음 도구가 필요합니다:\n\n- kubectl\n- eksctl\n- helm"),
        (True, "```bash\neksctl create cluster \\\n  --name demo\n\n\n# 주석\necho done\n```"),
        (False, "| 이름 | 값 |\n| --- | --- |\n| a\\|b | x<br>y |"),
        (False, "### 세부 사항\n\n" + "긴 문장입니다. " * 25),
        (True, "````markdown\n```\nnested\n```\n\n````"),
        (False, "> 참고 \\\n> 인용문입니다."),
        (True, "```python\ndef f():\n\n    return 1\n```"),
        (False, "끝."),
    ]
    md = ""
    spans: list[tuple[int, int]] = []
    for i, (is_code, text) in enumerate(parts):
        if i:
            md += "\n\n"
        if is_code:
            spans.append((len(md), len(md) + len(text)))
        md += text
    return md + "\n", spans


SAMPLE, SAMPLE_SPANS = _sample_document()


def test_sample_code_spans() -> None:
    assert code_spans(SAMPLE) == SAMPLE_SPANS


@pytest.mark.parametrize("max_length", [1, 2, 3, 7, 16, 50, 99, 100, 128, 333, 1000])
def test_slice_round_trip_fixed_lengths(max_length: int) -> None:
    assert "".join(chunks_of(SAMPLE, max_length)) == SAMPLE


def test_slice_round_trip_every_length_and_some_starts() -> None:
    for max_length in range(1, len(SAMPLE) + 3):
        for start in (0, 1, 37, len(SAMPLE) // 2, len(SAMPLE) - 1):
            assert "".join(chunks_of(SAMPLE, max_length, start)) == SAMPLE[start:]


def test_slice_round_trip_random_documents() -> None:
    rng = random.Random(20261009)  # noqa: S311 - reproducible test data, not crypto
    pieces = [
        "word",
        "단어",
        " ",
        "\n",
        "\n\n",
        "```\ncode\n\nmore\n```",
        "## H",
        "- item",
        "|a|b|",
    ]
    for _ in range(200):
        md = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 80)))
        max_length = rng.randint(1, max(1, len(md) + 5))
        start = rng.randint(0, len(md))
        assert "".join(chunks_of(md, max_length, start)) == md[start:]


def _cuts(md: str, max_length: int) -> list[tuple[int, int, int]]:
    """``(start, cut, limit)`` for every non-final chunk."""
    out: list[tuple[int, int, int]] = []
    pos = 0
    while True:
        _, nxt = slice_markdown(md, pos, max_length)
        if nxt is None:
            return out
        out.append((pos, nxt, pos + max_length))
        pos = nxt


@pytest.mark.parametrize("max_length", range(20, 400, 7))
def test_slice_never_cuts_inside_code_when_avoidable(max_length: int) -> None:
    def in_code(pos: int) -> bool:
        return any(a < pos < b for a, b in SAMPLE_SPANS)

    for start, cut, limit in _cuts(SAMPLE, max_length):
        floor = start + max_length // 2
        outside_breaks = [
            p for p in range(floor, limit - 1) if SAMPLE.startswith("\n\n", p) and not in_code(p)
        ]
        if outside_breaks:
            assert not in_code(cut), (start, cut)
            assert SAMPLE[:cut].endswith("\n\n")


def test_bold_only_paragraph_is_not_left_dangling_at_a_cut() -> None:
    md = "Intro paragraph text.\n\n" + "x" * 30 + "\n\n**Phase A**\n\n" + "y" * 200
    chunk, nxt = slice_markdown(md, 0, 70)
    assert nxt is not None
    assert not chunk.rstrip().endswith("**Phase A**")
    assert md[nxt:].startswith("**Phase A**")
