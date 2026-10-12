"""Tests for :mod:`aws_blog_mcp.extract` (``parse_post_page`` and ``render_body``).

Every HTML input is synthetic. The files under ``tests/fixtures/html/`` reproduce markup patterns
seen on real AWS Korea tech blog pages (2019, 2022 and 2026 templates) with invented text.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from aws_blog_mcp.errors import NotABlogPostError, UpstreamError
from aws_blog_mcp.extract import ImageMode, PostPage, parse_post_page, render_body
from tests.fakes import BASE, post_page

FIXTURES = Path(__file__).parent / "fixtures" / "html"
URL = BASE + "sample-post/"
KST = timezone(timedelta(hours=9))
TITLE_TAG = '<h1 class="lb-h2 blog-post-title" property="name headline">'
CONTENT_TAG = '<section class="blog-post-content lb-rtxt" property="articleBody">'


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def page_html(
    *, url: str = URL, title: str = "Sample title", body: str = "<p>Body</p>", **kwargs: Any
) -> str:
    """A synthetic post page with sensible defaults."""
    return post_page(url=url, title=title, body=body, **kwargs)


def replace(html: str, old: str, new: str, count: int = 1) -> str:
    """``str.replace`` that fails loudly if the template no longer contains ``old``."""
    assert html.count(old) >= count, f"{old!r} not found in the template"
    return html.replace(old, new, count)


def render(body: str, *, images: ImageMode = "markdown", base: str = URL) -> str:
    return render_body(f'<section class="blog-post-content">{body}</section>', base, images=images)


# --------------------------------------------------------------------------------------------
# Fixture pages (full template)
# --------------------------------------------------------------------------------------------

MODERN_MARKDOWN = """\
## 들어가며

가상의 스튜디오가 [이전 글](https://aws.amazon.com/ko/blogs/tech/older-post/)을 확장합니다.

*그림 1. 업로드 실수로 깨진 이미지*

![전체 아키텍처](https://cdn.example.invalid/2026/03/05/architecture.png)

*그림 2. 전체 아키텍처*

### 1단계: 규칙 파일 작성

먼저 저장소 루트에 규칙 파일을 만듭니다.

````markdown
# Review rules
```python
assert True
```
````

다음 명령으로 검증합니다.

```bash
make lint && make test
```

#### 결과 비교

| 항목 | 이전 | 이후 |
| --- | --- | --- |
| 리뷰 시간 | 2일 | 4시간 |
| A\\|B 테스트 | 수동 | 자동<br>매일 실행 |

##### 참고 사항

- 첫 번째 항목
- 두 번째 항목

읽어 주셔서 감사합니다.
"""

LEGACY_MARKDOWN = """\
이 예제는 가상의 직원 테이블을 갱신합니다.

![](https://cdn.example.invalid/2022/06/13/Picture1-6.png)

### 준비 단계

[![](https://cdn.example.invalid/2022/06/13/launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=ap-northeast-2#/stacks/new?stackName=sample&templateURL=https://example.invalid/template.yaml)

1. 쿼리 편집기를 엽니다.

   ```sql
   select * from "sample_db"."employees" limit 10;
   ```

   결과에서 **직원 번호**를 확인합니다.

   ![](https://cdn.example.invalid/2022/06/13/Picture27.png)
2. 테이블을 갱신합니다.

| 컬럼 | 설명 |
| --- | --- |
| emp_no | 직원 ***고유*** *번호* |
| dept | 부서 코드<br>예: A\\|B |

다음 쿼리를 실행합니다.

```sql
UPDATE employees SET dept = 'B' WHERE emp_no = 1;
```

이상입니다.
"""

TAGGED_MARKDOWN = """\
오늘 가상의 샘플 서비스 요금을 인하합니다.

[Video: Sample launch video](https://www.youtube.com/watch?v=Ab3_dE-fG12)

> **참고:** 요금은 리전마다 다릅니다.

- [블로그] [관련 글](https://aws.amazon.com/ko/blogs/tech/synthetic-modern-post/)
- [동영상] [소개 영상](https://www.youtube.com/watch?v=Zz9_yY8-xX7) – 홍길동

– 홍길동
"""


def test_modern_fixture_metadata() -> None:
    page = parse_post_page(fixture("post_modern.html"), BASE + "requested/")

    assert page.url == BASE + "synthetic-modern-post/"
    assert page.title == "가상 게임 스튜디오의 코드 리뷰 자동화 여정"
    # Byline authors first, then the extra co-author from the in-body author box.
    assert page.authors == ("Alex Example", "Jamie Sample", "김예시")
    assert page.published == datetime(2026, 3, 5, 23, 30, tzinfo=KST)
    assert page.modified == datetime(2026, 3, 6, 0, 10, tzinfo=KST)
    assert page.categories == ("Artificial Intelligence", "Developer Tools", "Sample Agent")
    assert page.tags == ()
    assert page.language == "ko-KR"
    assert page.body_html.startswith('<section class="blog-post-content lb-rtxt"')
    assert "blog-share-dialog" not in page.body_html
    assert "Jamie Sample is a fictional engineer." not in page.body_html


def test_modern_fixture_markdown() -> None:
    page = parse_post_page(fixture("post_modern.html"), BASE + "requested/")
    assert render_body(page.body_html, page.url) == MODERN_MARKDOWN


def test_modern_fixture_image_modes() -> None:
    page = parse_post_page(fixture("post_modern.html"), BASE + "requested/")
    with_alt = render_body(page.body_html, page.url, images="alt")
    without = render_body(page.body_html, page.url, images="none")

    image = "![전체 아키텍처](https://cdn.example.invalid/2026/03/05/architecture.png)"
    assert with_alt == MODERN_MARKDOWN.replace(image, "[Image: 전체 아키텍처]")
    assert without == MODERN_MARKDOWN.replace(image + "\n\n", "")


def test_legacy_fixture_metadata() -> None:
    page = parse_post_page(fixture("post_legacy.html"), BASE + "requested/")

    assert page.url == BASE + "synthetic-legacy-post/"
    assert page.title == "샘플 데이터레이크에서 테이블 갱신하기"
    assert page.authors == ("Taylor Test, Ph.D", "Jamie Sample", "Alex Example")
    assert page.published == datetime(2022, 7, 8, 8, 15, 18, tzinfo=KST)
    assert page.modified == datetime(2023, 7, 13, 1, 57, 32, tzinfo=KST)
    assert page.categories == ("Analytics", "Sample Glue")  # duplicate link dropped
    assert page.tags == ()
    assert page.language == "ko-KR"


def test_legacy_fixture_markdown() -> None:
    page = parse_post_page(fixture("post_legacy.html"), BASE + "requested/")
    markdown = render_body(page.body_html, page.url)

    assert markdown == LEGACY_MARKDOWN
    assert "Polly" not in markdown
    assert "tracking" not in markdown


def test_legacy_fixture_alt_mode_drops_images_without_alt_and_their_links() -> None:
    page = parse_post_page(fixture("post_legacy.html"), BASE + "requested/")
    expected = (
        LEGACY_MARKDOWN.replace(
            "![](https://cdn.example.invalid/2022/06/13/Picture1-6.png)\n\n", ""
        )
        .replace("\n\n   ![](https://cdn.example.invalid/2022/06/13/Picture27.png)", "")
        .replace(
            "[![](https://cdn.example.invalid/2022/06/13/launch-stack.png)](https://console.aws."
            "amazon.com/cloudformation/home?region=ap-northeast-2#/stacks/new?stackName=sample&"
            "templateURL=https://example.invalid/template.yaml)\n\n",
            "",
        )
    )

    assert render_body(page.body_html, page.url, images="alt") == expected
    assert render_body(page.body_html, page.url, images="none") == expected


def test_tagged_fixture_metadata() -> None:
    page = parse_post_page(fixture("post_tagged.html"), BASE + "requested/")

    assert page.url == BASE + "synthetic-tagged-post/"
    assert page.title == "샘플 서비스 요금 인하 안내"
    assert page.authors == ("Riley Writer (홍길동)",)
    # No <time> element: the date comes from article:published_time.
    assert page.published == datetime(2019, 1, 9, 0, 25, 41, tzinfo=KST)
    assert page.modified == page.published
    assert page.categories == ("Sample Compute", "Price Reduction")
    assert page.tags == ("서울 리전 소식", "Price")  # links without rel="tag" are ignored
    assert page.language == "ko-KR"


def test_tagged_fixture_markdown() -> None:
    page = parse_post_page(fixture("post_tagged.html"), BASE + "requested/")
    markdown = render_body(page.body_html, page.url)

    assert markdown == TAGGED_MARKDOWN
    assert "\xa0" not in markdown


def test_listing_fixture_is_rejected() -> None:
    with pytest.raises(NotABlogPostError):
        parse_post_page(fixture("listing_page.html"), BASE)


# --------------------------------------------------------------------------------------------
# parse_post_page: page markers
# --------------------------------------------------------------------------------------------


def _two_articles() -> str:
    html = page_html()
    article = html[html.index("<article") : html.index("</article>") + len("</article>")]
    return replace(html, "</main>", article + "</main>")


NOT_POSTS = {
    "listing body class": page_html(single=False),
    "error page": "<html><body class='error'>Not found</body></html>",
    "empty document": "",
    "json": '{"items": []}',
    "no content section": replace(page_html(), CONTENT_TAG, '<section class="blog-post-excerpt">'),
    "section outside article": replace(
        replace(page_html(), '<article class="blog-post"', '<div class="blog-post"'),
        "</article>",
        "</div>",
    ),
    "two sections in one article": replace(
        page_html(), "<p>Body</p>", '<section class="blog-post-content"><p>Nested</p></section>'
    ),
    "two articles": _two_articles(),
}


@pytest.mark.parametrize("html", NOT_POSTS.values(), ids=NOT_POSTS.keys())
def test_non_post_pages_are_rejected(html: str) -> None:
    with pytest.raises(NotABlogPostError) as info:
        parse_post_page(html, URL)
    assert str(info.value) == (
        f"{URL} is not a single blog post page. Pass the URL of an individual post "
        "(for example one returned by search_posts)."
    )


def test_not_a_blog_post_error_is_an_upstream_error() -> None:
    with pytest.raises(UpstreamError):
        parse_post_page(page_html(single=False), URL)


def test_minimal_valid_page_is_accepted() -> None:
    html = (
        '<html><body class="aws-blogs-page-single"><article class="blog-post">'
        '<section class="blog-post-content"><p>Only body</p></section></article></body></html>'
    )
    page = parse_post_page(html, URL)

    assert page == PostPage(
        url=URL,
        title="",
        authors=(),
        published=None,
        modified=None,
        categories=(),
        tags=(),
        language=None,
        body_html='<section class="blog-post-content"><p>Only body</p></section>',
    )


# --------------------------------------------------------------------------------------------
# parse_post_page: metadata
# --------------------------------------------------------------------------------------------


def test_canonical_url_wins_over_fetched_url() -> None:
    page = parse_post_page(page_html(url=BASE + "canonical-slug/"), BASE + "old-slug/")
    assert page.url == BASE + "canonical-slug/"


def test_fetched_url_is_used_without_canonical_link() -> None:
    html = replace(page_html(), f'<link rel="canonical" href="{URL}">', "")
    assert parse_post_page(html, BASE + "fetched/").url == BASE + "fetched/"


def with_canonical(href: str) -> str:
    return replace(
        page_html(), f'<link rel="canonical" href="{URL}">', f'<link rel="canonical" href="{href}">'
    )


@pytest.mark.parametrize(
    "href",
    [
        "https://aws.amazon.com/blogs/aws/other-post/",
        "https://evil.example/ko/blogs/tech/other-post/",
        "https://aws.amazon.com.evil.example/ko/blogs/tech/other-post/",
        "https://aws.amazon.com:8443/ko/blogs/tech/other-post/",
        BASE + "category/compute/",
        BASE,
        BASE + "../",
        BASE + "%2e%2e/",
        "/ko/blogs/tech/relative-post/",
        "javascript:alert(1)",
        "",
        BASE + "x/\n- Authors: Injected",
    ],
    ids=[
        "other-blog",
        "foreign-host",
        "lookalike-host",
        "odd-port",
        "listing",
        "blog-home",
        "dot-dot",
        "encoded-dot-dot",
        "relative",
        "javascript",
        "empty",
        "newline-injection",
    ],
)
def test_untrusted_canonical_url_is_ignored(href: str) -> None:
    page = parse_post_page(with_canonical(href), BASE + "fetched/")
    assert page.url == BASE + "fetched/"


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        (f"  {BASE}spaced/  ", BASE + "spaced/"),
        ("http://www.aws.amazon.com/ko/blogs/tech/www-form/index.html?x=1#y", BASE + "www-form/"),
        (BASE + "line\nbreak/", BASE + "linebreak/"),  # URL parsers drop raw line breaks
    ],
)
def test_canonical_url_is_normalised(href: str, expected: str) -> None:
    page = parse_post_page(with_canonical(href), BASE + "fetched/")
    assert page.url == expected
    assert "\n" not in page.url


def test_canonical_url_must_belong_to_the_fetched_blog() -> None:
    fetched = "https://aws.amazon.com/blogs/aws/fetched/"  # not a supported blog
    page = parse_post_page(with_canonical(BASE + "canonical/"), fetched)
    assert page.url == fetched


def test_title_is_unescaped_and_whitespace_collapsed() -> None:
    html = replace(page_html(title="A & B"), "A &amp; B</h1>", "A &amp;\n   B  </h1>")
    assert parse_post_page(html, URL).title == "A & B"


@pytest.mark.parametrize(
    ("og_title", "expected"),
    [
        ("가상의 제목 | Amazon Web Services", "가상의 제목"),
        ("Part 1 | Part 2 | Amazon Web Services", "Part 1 | Part 2"),
        ("Title without suffix", "Title without suffix"),
        ("Spaces   |   Amazon Web Services   ", "Spaces"),
    ],
)
def test_title_falls_back_to_og_title_without_suffix(og_title: str, expected: str) -> None:
    html = page_html(title="Original")
    html = replace(html, 'content="Original | Amazon Web Services"', f'content="{og_title}"')
    html = replace(html, f"{TITLE_TAG}Original</h1>", f"{TITLE_TAG} </h1>")
    assert parse_post_page(html, URL).title == expected


def test_title_falls_back_to_og_title_when_heading_is_missing() -> None:
    html = replace(page_html(title="Original"), f"{TITLE_TAG}Original</h1>", "")
    assert parse_post_page(html, URL).title == "Original"


def test_title_from_unclassed_h1() -> None:
    html = replace(page_html(title="Original"), TITLE_TAG, "<h1>")
    html = replace(html, 'content="Original | Amazon Web Services"', 'content="Other"')
    assert parse_post_page(html, URL).title == "Original"


def test_title_is_empty_without_heading_or_og_title() -> None:
    html = replace(page_html(title="Original"), f"{TITLE_TAG}Original</h1>", "")
    html = replace(html, '<meta property="og:title" content="Original | Amazon Web Services">', "")
    assert parse_post_page(html, URL).title == ""


@pytest.mark.parametrize(
    ("byline", "expected"),
    [
        ("Alex Example", ("Alex Example",)),
        ("Alex Example and Jamie Sample", ("Alex Example", "Jamie Sample")),
        (
            "Alex Example, Jamie Sample, and Riley Extra",
            ("Alex Example", "Jamie Sample", "Riley Extra"),
        ),
        (
            "Alex Example, Jamie Sample and Riley Extra",
            ("Alex Example", "Jamie Sample", "Riley Extra"),
        ),
        ("Taylor Test, Ph.D and Jamie Sample", ("Taylor Test, Ph.D", "Jamie Sample")),
        (
            "Taylor Test, Ph.D, Jamie Sample, and Alex Example",
            ("Taylor Test, Ph.D", "Jamie Sample", "Alex Example"),
        ),
        ("Alex Example and alex example", ("Alex Example",)),
        ("홍길동, 김예시", ("홍길동", "김예시")),
        ("Riley Writer (홍길동)", ("Riley Writer (홍길동)",)),
    ],
)
def test_byline_variants(byline: str, expected: tuple[str, ...]) -> None:
    assert parse_post_page(page_html(byline=byline), URL).authors == expected


def test_byline_with_schema_markup_and_uppercase_prefix() -> None:
    html = replace(
        page_html(),
        "<span>by Test Author</span>",
        '<span>By <span property="author" typeof="Person"><span property="name">Alex Example'
        '</span></span>\n and <span property="author" typeof="Person"><span property="name">'
        "Jamie Sample</span></span></span>",
    )
    assert parse_post_page(html, URL).authors == ("Alex Example", "Jamie Sample")


def test_body_and_footer_author_boxes_are_merged_and_deduplicated() -> None:
    body = (
        "<p>Body</p><footer>"
        '<div class="blog-author-box"><h3 class="lb-h4">김예시</h3><p>Guest bio.</p></div>'
        "</footer>"
    )
    html = page_html(
        byline="Alex Example and Jamie Sample",
        body=body,
        author_boxes=["Alex Example", "JAMIE SAMPLE", "Riley Extra", "김예시"],
    )
    assert parse_post_page(html, URL).authors == (
        "Alex Example",
        "Jamie Sample",
        "김예시",
        "Riley Extra",
    )


def test_author_box_name_sources() -> None:
    boxes = (
        '<div class="blog-author-box"><h4>H4 Name</h4></div>'
        '<div class="blog-author-box"><p><span property="name">Property  Name</span></p></div>'
        '<div class="blog-author-box"><h3> </h3><p>No name here.</p></div>'
        '<div class="blog-author-box"><p>Only a bio.</p></div>'
    )
    html = replace(
        page_html(byline="Alex Example"), "<footer></footer>", f"<footer>{boxes}</footer>"
    )
    assert parse_post_page(html, URL).authors == ("Alex Example", "H4 Name", "Property Name")


def test_page_without_meta_footer() -> None:
    html = page_html(author_boxes=["Box Only"])
    start = html.index('<footer class="blog-post-meta">')
    end = html.index("</footer>", start) + len("</footer>")
    page = parse_post_page(html[:start] + html[end:], URL)

    assert page.authors == ("Box Only",)
    assert page.categories == ()
    # Without <time>, the published date falls back to the meta tag.
    assert page.published == datetime(2026, 1, 1, 9, 0, tzinfo=KST)


def test_meta_footer_without_spans_has_no_byline() -> None:
    html = page_html(author_boxes=["Box Only"])
    start = html.index('<footer class="blog-post-meta">')
    end = html.index("</footer>", start) + len("</footer>")
    html = html[:start] + '<footer class="blog-post-meta">by Loose Text</footer>' + html[end:]
    assert parse_post_page(html, URL).authors == ("Box Only",)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("<span>by Test Author</span> ", ""),  # no byline span: the date span comes first
        ("<span>by Test Author</span>", "<span>by </span>"),  # empty byline
        ("<span>by Test Author</span>", "<span>Byung Example</span>"),  # not a "by" prefix
        ("<span>by Test Author</span>", "<span>bylines: none</span>"),
    ],
    ids=["missing-byline", "empty-byline", "name-starting-with-by", "word-starting-with-by"],
)
def test_meta_footer_without_byline_has_no_bogus_author(old: str, new: str) -> None:
    html = replace(page_html(author_boxes=["Box Only"]), old, new)
    assert parse_post_page(html, URL).authors == ("Box Only",)


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        ("2026-03-05T23:30:00+09:00", datetime(2026, 3, 5, 23, 30, tzinfo=KST)),
        ("2026-03-05T14:30:00Z", datetime(2026, 3, 5, 14, 30, tzinfo=UTC)),
        (" 2026-03-05T23:30:00+09:00 ", datetime(2026, 3, 5, 23, 30, tzinfo=KST)),
        ("2026-03-05T23:30:00", datetime(2026, 3, 5, 23, 30)),
        ("not a date", None),
        ("", None),
    ],
)
def test_published_and_modified_dates(published: str, expected: datetime | None) -> None:
    page = parse_post_page(page_html(published=published), URL)
    assert page.published == expected
    assert page.modified == expected  # post_page uses the published value for modified too


def test_time_element_wins_over_meta_published_time() -> None:
    html = replace(
        page_html(published="2026-01-01T09:00:00+09:00"),
        '<meta property="article:published_time" content="2026-01-01T09:00:00+09:00">',
        '<meta property="article:published_time" content="2020-01-01T00:00:00+09:00">',
    )
    assert parse_post_page(html, URL).published == datetime(2026, 1, 1, 9, 0, tzinfo=KST)


def test_modified_date_comes_from_meta() -> None:
    html = page_html(published="2026-01-01T09:00:00+09:00", modified="2026-02-03T04:05:06+09:00")
    page = parse_post_page(html, URL)
    assert page.published == datetime(2026, 1, 1, 9, 0, tzinfo=KST)
    assert page.modified == datetime(2026, 2, 3, 4, 5, 6, tzinfo=KST)


def test_dates_are_none_without_any_source() -> None:
    html = replace(
        page_html(),
        '<time property="datePublished" datetime="2026-01-01T09:00:00+09:00">x</time>',
        "",
    )
    html = re.sub(r'<meta property="article:(?:published|modified)_time"[^>]*>', "", html)
    page = parse_post_page(html, URL)
    assert page.published is None
    assert page.modified is None


def test_categories_keep_order_and_drop_duplicates_and_blanks() -> None:
    cats = [
        ("database", "Database"),
        ("analytics/sample-glue", "Sample  Glue"),
        ("database", "Database"),
        ("blank", " "),
        ("compute", "Compute"),
    ]
    page = parse_post_page(page_html(categories=cats), URL)
    assert page.categories == ("Database", "Sample Glue", "Compute")


def test_tags_from_tag_list() -> None:
    tag_list = (
        '<footer><div class="blog-tag-list">TAGS: <span property="keywords">'
        '<a href="/ko/blogs/tech/tag/a/" rel="tag">Tag &amp; A</a>, '
        '<a href="/ko/blogs/tech/tag/b/" rel="tag"> </a>, '
        '<a href="/ko/blogs/tech/tag/c/" rel="tag">태그 C</a></span></div></footer>'
    )
    html = replace(page_html(), "</article>", tag_list + "</article>")
    assert parse_post_page(html, URL).tags == ("Tag & A", "태그 C")


def test_language_is_none_without_meta() -> None:
    html = replace(page_html(), '<meta property="inLanguage" content="ko-KR">', "")
    assert parse_post_page(html, URL).language is None


def test_body_html_is_only_the_content_section() -> None:
    page = parse_post_page(page_html(body="<p>Hello</p>", author_boxes=["Alex Example"]), URL)
    assert page.body_html == (
        '<section class="blog-post-content lb-rtxt" property="articleBody"><p>Hello</p></section>'
    )
    assert render_body(page.body_html, page.url) == "Hello\n"


# --------------------------------------------------------------------------------------------
# render_body: removal of non-content elements
# --------------------------------------------------------------------------------------------

REMOVED = {
    "script": "<script>var x = 1;</script>",
    "style": "<style>p { color: red; }</style>",
    "noscript": "<noscript>Enable JavaScript</noscript>",
    "template": "<template><p>Template</p></template>",
    "form": '<form><input value="v"><select><option>o</option></select>'
    "<textarea>t</textarea><button>Go</button></form>",
    "svg": "<svg><text>svg text</text></svg>",
    "canvas": "<canvas>canvas text</canvas>",
    "audio": '<audio src="https://cdn.example.invalid/a.mp3">audio text</audio>',
    "video": '<video src="https://cdn.example.invalid/v.mp4">video text</video>',
    "object": '<object data="x.swf"><param name="movie" value="x.swf">object text</object>',
    "closed embed": '<embed src="x.swf" type="application/x-shockwave-flash"></embed>',
    "self-closed embed": '<embed src="x.swf"/>',
    "share dialog": (
        '<div class="blog-share-dialog"><ul><li><a href="https://x.invalid/">Share</a></li></ul>'
        "</div>"
    ),
    "polly table": '<div id="amazon-polly-audio-table"><p>Listen to this post</p></div>',
    "polly prefix": '<div id="amazon-polly-label-tab"><p>Voiced by Amazon Polly</p></div>',
    "ai player": '<div id="amazon-ai-player-container"><p>Player</p></div>',
    "mbox": '<div class="js-mbox" data-mbox="ko_blog_post_comments">Comments</div>',
}


@pytest.mark.parametrize("snippet", REMOVED.values(), ids=REMOVED.keys())
def test_non_content_elements_are_removed(snippet: str) -> None:
    assert render(f"<p>Before</p>{snippet}<p>After</p>") == "Before\n\nAfter\n"


def test_content_after_bare_embed_is_kept() -> None:
    assert render('<p>Before</p><embed src="x.swf"><p>After</p>') == "Before\n\nAfter\n"


def test_author_box_in_body_footer_is_removed_with_its_footer() -> None:
    body = (
        '<p>Body</p><footer><div class="blog-author-box"><div class="blog-author-image">'
        '<img alt="Kim" src="https://cdn.example.invalid/kim.jpg"></div>'
        '<h3 class="lb-h4">Kim</h3><p>Bio of Kim.</p></div></footer><p>After</p>'
    )
    assert render(body) == "Body\n\nAfter\n"


def test_author_box_directly_in_body_is_removed() -> None:
    body = '<p>a</p><div class="blog-author-box"><h3>Kim</h3><p>Bio.</p></div><p>b</p>'
    assert render(body) == "a\n\nb\n"


def test_footer_with_other_content_keeps_that_content() -> None:
    body = '<footer><div class="blog-author-box"><h3>Kim</h3></div><p>Kept footer text</p></footer>'
    assert render(body) == "Kept footer text\n"


# --------------------------------------------------------------------------------------------
# render_body: lists
# --------------------------------------------------------------------------------------------


def test_continuation_item_is_merged_into_previous_item() -> None:
    body = (
        "<ol><li>First</li>"
        '<li style="list-style-type: none"><p>More of first</p></li>'
        '<li style="list-style-type:none;"><pre><code class="lang-bash">run</code></pre></li>'
        "<li>Second</li></ol>"
    )
    assert render(body) == (
        "1. First\n\n   More of first\n\n   ```bash\n   run\n   ```\n\n2. Second\n"
    )


def test_continuation_item_with_nested_list_becomes_a_sublist() -> None:
    body = (
        '<ul><li>Parent</li><li style="list-style-type: none;"><ul><li>Child</li></ul></li>'
        "<li>Next</li></ul>"
    )
    assert render(body) == "- Parent\n  - Child\n- Next\n"


def test_leading_continuation_item_with_only_a_nested_list_is_unwrapped() -> None:
    body = '<ul><li style="list-style-type: none;"><ul><li>Indented</li></ul></li></ul>'
    assert render(body) == "- Indented\n"


def test_leading_continuation_item_with_block_content_is_unwrapped() -> None:
    body = '<ol><li style="list-style-type: none"><p>Lead paragraph</p></li><li>Second</li></ol>'
    assert render(body) == "Lead paragraph\n\n1. Second\n"


def test_leading_continuation_item_with_inline_text_stays_separate() -> None:
    out = render('<ul><li style="list-style-type: none">Orphan</li><li>Second</li></ul>')
    assert "Orphan-" not in out
    assert re.search(r"(?m)^(?:- )?Orphan$", out)
    assert re.search(r"(?m)^- Second$", out)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            '<ul><li style="list-style-type: none">Orphan</li><li>Second</li></ul>',
            "- Orphan\n- Second\n",
        ),
        (
            '<ol><li style="list-style-type: none"><strong>Bold</strong></li><li>Second</li></ol>',
            "1. **Bold**\n2. Second\n",
        ),
        (
            '<ul><li style="list-style-type: none"><p>Para</p> trailing <em>text</em></li>'
            "<li>Second</li></ul>",
            "- Para\n\n  trailing *text*\n- Second\n",
        ),
        (
            '<ol><li style="list-style-type: none">\n<ul><li>x</li></ul>\n<p>y</p></li></ol>',
            "- x\n\ny\n",
        ),
    ],
    ids=["inline-text", "inline-tag", "block-then-inline", "blocks-and-whitespace"],
)
def test_leading_continuation_item_is_hoisted_only_when_all_block(body: str, expected: str) -> None:
    assert render(body) == expected


# --------------------------------------------------------------------------------------------
# render_body: iframes
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("iframe", "expected"),
    [
        (
            '<iframe src="https://www.youtube.com/embed/AbCdEf12345?rel=0" title="Demo video">'
            "</iframe>",
            "[Video: Demo video](https://www.youtube.com/watch?v=AbCdEf12345)\n",
        ),
        (
            '<iframe data-src="https://www.youtube-nocookie.com/embed/Ab3_dE-fG12"></iframe>',
            "[Video: YouTube](https://www.youtube.com/watch?v=Ab3_dE-fG12)\n",
        ),
        (
            '<iframe src="https://player.example.com/v/1 2" title="  "></iframe>',
            "[Embedded content: player.example.com](https://player.example.com/v/1%202)\n",
        ),
        (
            '<iframe src="https://player.example.com/v/9" title="Sample  widget"></iframe>',
            "[Embedded content: Sample widget](https://player.example.com/v/9)\n",
        ),
        (
            '<iframe src="/media/embed?id=(3)" title="Widget"></iframe>',
            "[Embedded content: Widget](https://aws.amazon.com/media/embed?id=%283%29)\n",
        ),
    ],
    ids=["youtube", "youtube-nocookie-data-src", "other-untitled", "other-titled", "relative"],
)
def test_iframes_become_links(iframe: str, expected: str) -> None:
    assert render(iframe) == expected


def test_iframe_inside_paragraph() -> None:
    body = '<p>Watch:</p><p><iframe src="https://www.youtube.com/embed/AbCdEf12345"></iframe></p>'
    assert (
        render(body) == "Watch:\n\n[Video: YouTube](https://www.youtube.com/watch?v=AbCdEf12345)\n"
    )


# --------------------------------------------------------------------------------------------
# render_body: links and images
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        (
            "/ko/blogs/tech/files/My Pic (1)\u202f.png",
            "https://aws.amazon.com/ko/blogs/tech/files/My%20Pic%20%281%29%E2%80%AF.png",
        ),
        ("//cdn.example.invalid/p.png", "https://cdn.example.invalid/p.png"),
        ("https://cdn.example.invalid/a%20b.png", "https://cdn.example.invalid/a%20b.png"),
        (
            "https://cdn.example.invalid/한글 이미지.png",
            "https://cdn.example.invalid/%ED%95%9C%EA%B8%80%20%EC%9D%B4%EB%AF%B8%EC%A7%80.png",
        ),
        (
            "  https://cdn.example.invalid/x.png?w=1&h=2  ",
            "https://cdn.example.invalid/x.png?w=1&h=2",
        ),
        ("data:image/png;base64,AAAA", "data:image/png;base64,AAAA"),
    ],
)
def test_image_urls_are_resolved_and_encoded(src: str, expected: str) -> None:
    body = f'<p><img src="{src}" alt="Pic" class="aligncenter" width="800" style="border: 1px"></p>'
    assert render(body) == f"![Pic]({expected})\n"


def test_lazy_image_uses_data_src() -> None:
    assert render('<p><img data-src="/img/lazy.png" alt=" Lazy  pic "></p>') == (
        "![Lazy pic](https://aws.amazon.com/img/lazy.png)\n"
    )


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("../other post/", "https://aws.amazon.com/ko/blogs/tech/other%20post/"),
        (
            "/ko/blogs/tech/한글-슬러그/",
            "https://aws.amazon.com/ko/blogs/tech/%ED%95%9C%EA%B8%80-%EC%8A%AC%EB%9F%AC%EA%B7%B8/",
        ),
        ("child/", "https://aws.amazon.com/ko/blogs/tech/sample-post/child/"),
        ("https://docs.aws.amazon.com/a_(b).html", "https://docs.aws.amazon.com/a_%28b%29.html"),
        ("https://example.com/q?a=1&b=%2F#frag", "https://example.com/q?a=1&b=%2F#frag"),
        ("#section-2", "#section-2"),
    ],
)
def test_link_urls_are_resolved_and_encoded(href: str, expected: str) -> None:
    body = f'<p><a href="{href}" target="_blank" rel="noopener">Link</a></p>'
    assert render(body) == f"[Link]({expected})\n"


@pytest.mark.parametrize(
    "anchor", ['<a href="javascript:void(0)">Click</a>', "<a>Click</a>", '<a href=" ">Click</a>']
)
def test_links_without_usable_href_are_unwrapped(anchor: str) -> None:
    assert render(f"<p>Please {anchor} here</p>") == "Please Click here\n"


@pytest.mark.parametrize(
    "body",
    [
        '<p><img src="Picture1.png" alt="Bare"></p><p>Text</p>',
        '<p><img src="images/diagram.png" alt="Relative"></p><p>Text</p>',
        '<p><img alt="No source"></p><p>Text</p>',
        '<p><a href="Picture1.png"><img src="Picture1.png" alt="Linked"></a></p><p>Text</p>',
    ],
    ids=["bare-file-name", "relative-path", "no-src", "linked-bare"],
)
def test_bare_relative_images_are_dropped_with_their_empty_paragraph(body: str) -> None:
    assert render(body) == "Text\n"


def test_bare_relative_image_inside_text_keeps_the_text() -> None:
    out = render('<p>Look <img src="Picture1.png" alt="Bare"> here</p>')
    assert " ".join(out.split()) == "Look here"
    assert "Picture1" not in out


@pytest.mark.parametrize(
    "body",
    [
        # Link to the same URL as the image.
        '<p><a href="https://cdn.example.invalid/x/pic">'
        '<img src="https://cdn.example.invalid/x/pic" alt="P"></a></p>',
        # WordPress: thumbnail linking to the full-size file.
        '<p><a href="https://cdn.example.invalid/x/pic.PNG?v=2">'
        '<img src="https://cdn.example.invalid/x/pic" alt="P"></a></p>',
    ],
    ids=["same-url", "full-size-file"],
)
def test_link_wrapping_its_own_image_is_unwrapped(body: str) -> None:
    assert render(body) == "![P](https://cdn.example.invalid/x/pic)\n"


def test_link_from_image_to_elsewhere_is_kept() -> None:
    body = (
        '<p><a class="thickbox" href="https://console.aws.amazon.com/cloudformation/home#/stacks/new">'
        '<img src="https://cdn.example.invalid/launch.png" alt="Launch"></a></p>'
    )
    assert render(body) == (
        "[![Launch](https://cdn.example.invalid/launch.png)]"
        "(https://console.aws.amazon.com/cloudformation/home#/stacks/new)\n"
    )


def test_link_with_image_and_text_is_kept() -> None:
    body = (
        '<p><a href="https://cdn.example.invalid/x.png">'
        '<img src="https://cdn.example.invalid/x.png" alt="">Download</a></p>'
    )
    assert (
        render(body)
        == "[![](https://cdn.example.invalid/x.png)Download](https://cdn.example.invalid/x.png)\n"
    )


# --------------------------------------------------------------------------------------------
# render_body: captions
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            '<div class="wp-caption aligncenter" style="width: 810px"><img alt="Arch" '
            'class="aligncenter" src="https://cdn.example.invalid/arch.png" width="800">'
            '<p class="wp-caption-text"><em>Figure 1.  Architecture</em></p></div>',
            "![Arch](https://cdn.example.invalid/arch.png)\n\n*Figure 1. Architecture*\n",
        ),
        (
            '<div class="wp-caption"><a href="https://cdn.example.invalid/full.png"><img alt="A" '
            'src="https://cdn.example.invalid/thumb.png"></a>'
            '<p class="wp-caption-text">Cap</p></div>',
            "![A](https://cdn.example.invalid/thumb.png)\n\n*Cap*\n",
        ),
        (
            '<div class="wp-caption"><a href="https://example.com/page"><img alt="A" '
            'src="https://cdn.example.invalid/thumb.png"></a>'
            '<p class="wp-caption-text">Cap</p></div>',
            "[![A](https://cdn.example.invalid/thumb.png)](https://example.com/page)\n\n*Cap*\n",
        ),
        (
            '<div class="wp-caption aligncenter"><img alt="" src="Picture1.png">'
            '<p class="wp-caption-text"><em>Only caption</em></p></div>',
            "*Only caption*\n",
        ),
        (
            '<div class="wp-caption"><img alt="NoCap" src="https://cdn.example.invalid/n.png">'
            '<p class="wp-caption-text"> </p></div>',
            "![NoCap](https://cdn.example.invalid/n.png)\n",
        ),
        (
            '<figure><img alt="F" src="https://cdn.example.invalid/f.png"><figcaption>Fig cap'
            "</figcaption></figure>",
            "![F](https://cdn.example.invalid/f.png)\n\n*Fig cap*\n",
        ),
    ],
    ids=[
        "wp-caption",
        "linked-to-file",
        "linked-elsewhere",
        "broken-image",
        "empty-caption",
        "figure",
    ],
)
def test_captions_become_image_and_italic_caption(body: str, expected: str) -> None:
    assert render(body) == expected


@pytest.mark.parametrize(
    ("body", "kept"),
    [
        (
            '<figure class="wp-block-table"><table><tr><th>Col</th></tr><tr><td>Cell</td></tr>'
            "</table><figcaption>Table 1</figcaption></figure>",
            "| Cell |",
        ),
        (
            '<figure class="wp-block-embed"><div class="wp-block-embed__wrapper">'
            '<iframe src="https://www.youtube.com/embed/AbCdEf12345"></iframe></div></figure>',
            "https://www.youtube.com/watch?v=AbCdEf12345",
        ),
        (
            '<div class="wp-caption"><img src="https://cdn.example.invalid/a.png" alt="A">'
            '<img src="https://cdn.example.invalid/b.png" alt="B">'
            '<p class="wp-caption-text">Cap</p></div>',
            "![B](https://cdn.example.invalid/b.png)",
        ),
    ],
    ids=["figure-table", "figure-embed", "caption-two-images"],
)
def test_caption_flattening_keeps_other_content(body: str, kept: str) -> None:
    assert kept in render(body)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            '<figure class="callout"><p><strong>Note:</strong> keep this text</p>'
            "<figcaption>Callout</figcaption></figure>",
            "> **Note:** keep this text\n>\n> *Callout*\n",  # callout boxes become quotes
        ),
        (
            '<div class="wp-caption"><p>Text only callout</p>'
            '<p class="wp-caption-text">Caption</p></div>',
            "Text only callout\n\n*Caption*\n",
        ),
        (
            '<div class="wp-caption"><img src="https://cdn.example.invalid/a.png" alt="A">'
            '<p>Explanatory paragraph</p><p class="wp-caption-text">Cap</p></div>',
            "![A](https://cdn.example.invalid/a.png)\n\nExplanatory paragraph\n\n*Cap*\n",
        ),
        (
            '<figure class="wp-block-table"><table><tr><th>Col</th></tr><tr><td>Cell</td></tr>'
            "</table><figcaption>Table 1</figcaption></figure>",
            "| Col |\n| --- |\n| Cell |\n\n*Table 1*\n",
        ),
        (
            '<figure class="wp-block-embed"><div class="wp-block-embed__wrapper">'
            '<iframe src="https://www.youtube.com/embed/AbCdEf12345" title="Demo"></iframe></div>'
            "<figcaption>Watch</figcaption></figure>",
            "[Video: Demo](https://www.youtube.com/watch?v=AbCdEf12345)\n\n*Watch*\n",
        ),
        (
            "<figure><blockquote><p>Quoted words</p></blockquote>"
            "<figcaption>Someone</figcaption></figure>",
            "> Quoted words\n\n*Someone*\n",
        ),
        (
            "<figure><p>No caption at all</p></figure>",
            "No caption at all\n",
        ),
        (
            '<figure><pre class="lang-bash">echo hi</pre><figcaption> </figcaption></figure>',
            "```bash\necho hi\n```\n",
        ),
    ],
    ids=[
        "callout-figure",
        "text-wp-caption",
        "image-plus-paragraph",
        "table-figure",
        "embed-figure",
        "quote-figure",
        "figure-without-caption",
        "code-figure-blank-caption",
    ],
)
def test_non_image_figures_are_unwrapped_with_caption_paragraph(body: str, expected: str) -> None:
    assert render(body) == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            '<figure><img src="https://cdn.example.invalid/i.png" alt="I">Loose text'
            "<figcaption>Cap</figcaption></figure>",
            "![I](https://cdn.example.invalid/i.png)Loose text\n\n*Cap*\n",
        ),
        (
            '<div class="wp-caption"><p><img src="https://cdn.example.invalid/i.png" alt="I"> '
            'Extra words</p><p class="wp-caption-text">Cap</p></div>',
            "![I](https://cdn.example.invalid/i.png) Extra words\n\n*Cap*\n",
        ),
    ],
    ids=["loose-text", "image-paragraph-with-text"],
)
def test_image_figures_with_extra_text_keep_the_text(body: str, expected: str) -> None:
    assert render(body) == expected


GALLERY = (
    '<figure class="wp-block-gallery">'
    '<figure class="wp-block-image"><img src="https://cdn.example.invalid/a.png" alt="A">{cap_a}'
    "</figure>"
    '<figure class="wp-block-image"><img src="https://cdn.example.invalid/b.png" alt="B">'
    "</figure>"
    '<figcaption class="blocks-gallery-caption">Gallery</figcaption></figure>'
)


def test_gallery_without_image_captions() -> None:
    assert render(GALLERY.format(cap_a="")) == (
        "![A](https://cdn.example.invalid/a.png)\n\n"
        "![B](https://cdn.example.invalid/b.png)\n\n"
        "*Gallery*\n"
    )


def test_gallery_caption_stays_italic_when_images_have_captions() -> None:
    assert render(GALLERY.format(cap_a="<figcaption>Cap A</figcaption>")) == (
        "![A](https://cdn.example.invalid/a.png)\n\n*Cap A*\n\n"
        "![B](https://cdn.example.invalid/b.png)\n\n"
        "*Gallery*\n"
    )


# --------------------------------------------------------------------------------------------
# render_body: code
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pre", "expected"),
    [
        (
            '<pre><code class="lang-Python">print("hi")\nx = 1</code></pre>',
            '```python\nprint("hi")\nx = 1\n```\n',
        ),
        ('<pre class="language-bash">echo $HOME</pre>', "```bash\necho $HOME\n```\n"),
        ('<pre><code class="hljs language-ts">let a = 1;</code></pre>', "```ts\nlet a = 1;\n```\n"),
        (
            '<pre class="lang-json"><code class="language-yaml">{}</code></pre>',
            "```json\n{}\n```\n",
        ),
        ('<pre><code class="lang-c++">int x;</code></pre>', "```c++\nint x;\n```\n"),
        ('<pre><code class="language-C#">var x;</code></pre>', "```c#\nvar x;\n```\n"),
        ('<pre><code class="brush-sql">select 1;</code></pre>', "```\nselect 1;\n```\n"),
        ("<pre>plain   text\n  indented</pre>", "```\nplain   text\n  indented\n```\n"),
        (
            "<pre>\n\nleading and trailing blank lines\n\n</pre>",
            "```\nleading and trailing blank lines\n```\n",
        ),
    ],
    ids=[
        "lang-on-code",
        "language-on-pre",
        "language-among-classes",
        "pre-wins",
        "cpp",
        "csharp",
        "unknown-class",
        "bare-pre",
        "blank-lines",
    ],
)
def test_code_block_language(pre: str, expected: str) -> None:
    assert render(pre) == expected


def test_markup_inside_code_is_flattened() -> None:
    body = (
        '<pre><code class="lang-sql">SELECT <strong>name</strong>, <em>age</em> '
        '<span class="hl">FROM</span> t &lt;x&gt; &amp;&amp; *a* _b_;</code></pre>'
    )
    assert render(body) == "```sql\nSELECT name, age FROM t <x> && *a* _b_;\n```\n"


def test_hide_language_wrapper_is_unwrapped() -> None:
    body = (
        '<p>Run:</p><div class="hide-language"><pre><code class="lang-bash">ls -la</code></pre>'
        "</div><p>Done</p>"
    )
    assert render(body) == "Run:\n\n```bash\nls -la\n```\n\nDone\n"


def test_backticks_inside_code_get_a_longer_fence() -> None:
    body = '<pre><code class="lang-markdown">```python\nprint(1)\n```</code></pre>'
    assert render(body) == "````markdown\n```python\nprint(1)\n```\n````\n"
    assert render("<pre>````\nx\n````</pre>") == "`````\n````\nx\n````\n`````\n"


def test_empty_code_block_is_dropped() -> None:
    assert render("<p>a</p><pre>   \n </pre><pre><code></code></pre><p>b</p>") == "a\n\nb\n"


def test_code_whitespace_is_preserved() -> None:
    code = "def f():\n    return 1  \n\n\n\n    # four blank lines above, trailing spaces kept\t"
    out = render(f'<pre><code class="lang-python">{code}\n</code></pre>')
    assert out == f"```python\n{code}\n```\n"


def test_line_breaks_inside_code_are_kept() -> None:
    body = '<pre><code class="lang-bash">line1<br>line2<br/>line3</code></pre>'
    assert render(body) == "```bash\nline1\nline2\nline3\n```\n"


def test_code_inside_callout_table_is_untouched() -> None:
    body = "<table><tr><td><p>Run:</p><pre>cmd \\\n--flag</pre></td></tr></table>"
    assert "> cmd \\\n> --flag" in render(body)


# --------------------------------------------------------------------------------------------
# render_body: headings
# --------------------------------------------------------------------------------------------


def test_empty_headings_are_dropped() -> None:
    assert render("<h2></h2><h2> </h2><h3>&nbsp;</h3><h4><br></h4><h3>Real</h3><p>t</p>") == (
        "### Real\n\nt\n"
    )


def test_image_only_heading_becomes_a_paragraph() -> None:
    body = '<h4 class="hide-if-no-js"><img alt="Shot" src="https://cdn.example.invalid/s.png"></h4><p>t</p>'
    assert render(body) == "![Shot](https://cdn.example.invalid/s.png)\n\nt\n"


def test_heading_with_only_a_broken_image_is_dropped() -> None:
    assert render('<h4><img alt="" src="Picture9.png"></h4><p>x</p>') == "x\n"


def test_h1_in_body_demotes_every_heading() -> None:
    body = "<h1>Top</h1><h2>Sub</h2><h3>Third</h3><h5>Fifth</h5><h6>Deep</h6><p>t</p>"
    assert render(body) == "## Top\n\n### Sub\n\n#### Third\n\n###### Fifth\n\n###### Deep\n\nt\n"


def test_headings_are_kept_without_h1() -> None:
    assert (
        render("<h2>Sub</h2><h3>SubSub</h3><h6>Deep</h6>")
        == "## Sub\n\n### SubSub\n\n###### Deep\n"
    )


def test_bold_inside_heading_is_unwrapped() -> None:
    assert (
        render("<h2><strong>Bold</strong> <b>title</b> <em>kept</em></h2>")
        == "## Bold title *kept*\n"
    )


def test_trailing_break_in_heading_is_removed() -> None:
    assert render("<h2>Title<br></h2><p>t</p>") == "## Title\n\nt\n"


def test_zero_width_only_heading_is_dropped() -> None:
    assert render("<h2>\u200b</h2><p>x</p>") == "x\n"


# --------------------------------------------------------------------------------------------
# render_body: tables
# --------------------------------------------------------------------------------------------


def test_one_by_one_table_becomes_blockquote() -> None:
    body = (
        "<table><tbody><tr><td><p><strong>Note:</strong> careful</p><p>second</p></td></tr>"
        "</tbody></table>"
    )
    assert render(body) == "> **Note:** careful\n>\n> second\n"


def test_one_by_one_header_table_becomes_blockquote() -> None:
    assert render("<table><tr><th>Callout</th></tr></table>") == "> Callout\n"


def test_table_cells_are_single_line() -> None:
    body = (
        "<table><tbody><tr><th>Name</th><th>Desc</th></tr>"
        "<tr><td>a|b</td><td>line1<br>line2</td></tr>"
        "<tr><td><p>p1</p><p>p2</p></td><td><ul><li>i1</li><li>i2</li></ul></td></tr>"
        "<tr><td><div>d1</div><div>d2</div></td><td>multi\nline</td></tr>"
        "<tr><td><br>lead</td><td>trail<br><br></td></tr>"
        "</tbody></table>"
    )
    assert render(body) == (
        "| Name | Desc |\n"
        "| --- | --- |\n"
        "| a\\|b | line1<br>line2 |\n"
        "| p1<br>p2 | i1<br>i2 |\n"
        "| d1<br>d2 | multi line |\n"
        "| lead | trail |\n"
    )


def test_header_pipe_is_escaped() -> None:
    assert render("<table><tr><th>a|b</th></tr><tr><td>c</td></tr></table>") == (
        "| a\\|b |\n| --- |\n| c |\n"
    )


def test_table_header_is_inferred_without_th() -> None:
    body = (
        "<table><tbody><tr><td>H1</td><td>H2</td></tr><tr><td>v1</td><td>v2</td></tr>"
        "</tbody></table>"
    )
    assert render(body) == "| H1 | H2 |\n| --- | --- |\n| v1 | v2 |\n"


def test_table_with_thead() -> None:
    body = (
        "<table><thead><tr><th>A</th><th>B</th></tr></thead>"
        "<tbody><tr><td>1</td><td>2</td></tr></tbody></table>"
    )
    assert render(body) == "| A | B |\n| --- | --- |\n| 1 | 2 |\n"


def test_table_colspan() -> None:
    body = '<table><tr><td colspan="2">wide</td></tr><tr><td>a</td><td>b</td></tr></table>'
    assert render(body) == "| wide | |\n| --- | --- |\n| a | b |\n"


def test_single_row_table_is_not_a_callout() -> None:
    assert render("<table><tr><td>a</td><td>b</td></tr></table>") == "| a | b |\n| --- | --- |\n"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            '<table><tr><th rowspan="2">Group</th><th>Metric</th></tr><tr><th>Sub</th></tr>'
            '<tr><td rowspan="3">Region A</td><td>1</td></tr><tr><td>2</td></tr>'
            "<tr><td>3</td></tr></table>",
            "| Group | Metric |\n| --- | --- |\n| Group | Sub |\n| Region A | 1 |\n"
            "| Region A | 2 |\n| Region A | 3 |\n",
        ),
        (
            "<table><tr><th>A</th><th>B</th><th>C</th></tr>"
            '<tr><td>a1</td><td rowspan="2">b</td><td>c1</td></tr><tr><td>a2</td><td>c2</td></tr>'
            "</table>",
            "| A | B | C |\n| --- | --- | --- |\n| a1 | b | c1 |\n| a2 | b | c2 |\n",
        ),
        (
            "<table><tr><th>A</th><th>B</th></tr>"
            '<tr><td>a1</td><td rowspan="2">b</td></tr><tr><td>a2</td></tr></table>',
            "| A | B |\n| --- | --- |\n| a1 | b |\n| a2 | b |\n",
        ),
        (
            "<table><tr><th>A</th><th>B</th><th>C</th></tr>"
            '<tr><td rowspan="2" colspan="2">ab</td><td>c1</td></tr><tr><td>c2</td></tr></table>',
            "| A | B | C |\n| --- | --- | --- |\n| ab | | c1 |\n| ab | | c2 |\n",
        ),
        (
            "<table><tr><th>A</th><th>B</th></tr>"
            '<tr><td rowspan="9">a</td><td>b1</td></tr><tr><td>b2</td></tr></table>',
            "| A | B |\n| --- | --- |\n| a | b1 |\n| a | b2 |\n",
        ),
    ],
    ids=["header-and-body", "middle-column", "last-column", "with-colspan", "past-last-row"],
)
def test_rowspan_cells_are_repeated_in_covered_rows(body: str, expected: str) -> None:
    assert render(body) == expected


def test_rowspan_after_a_short_row_stays_in_its_column() -> None:
    body = (
        "<table><tr><th>A</th><th>B</th><th>C</th></tr>"
        '<tr><td>a1</td><td>b1</td><td rowspan="2">c</td></tr><tr><td>a2</td></tr></table>'
    )
    assert render(body) == ("| A | B | C |\n| --- | --- | --- |\n| a1 | b1 | c |\n| a2 |  | c |\n")


def test_long_rowspan_text_leaves_covered_cells_empty() -> None:
    long_text = "긴 설명 " * 20  # well over 80 characters
    body = (
        "<table><tr><th>K</th><th>V</th></tr>"
        f'<tr><td rowspan="3">{long_text}</td><td>1</td></tr><tr><td>2</td></tr>'
        "<tr><td>3</td></tr></table>"
    )
    assert render(body) == (
        f"| K | V |\n| --- | --- |\n| {long_text.strip()} | 1 |\n|  | 2 |\n|  | 3 |\n"
    )


def test_nested_table_rows_are_not_expanded_into_the_outer_table() -> None:
    body = (
        "<table><tr><th>Outer</th><th>Detail</th></tr>"
        '<tr><td rowspan="2">o</td><td><table><tr><td rowspan="2">in</td></tr></table></td></tr>'
        "<tr><td>next</td></tr></table>"
    )
    out = render(body)
    assert out.splitlines()[:2] == ["| Outer | Detail |", "| --- | --- |"]
    assert "| o | next |" in out


@pytest.mark.parametrize(
    "attrs",
    [
        'colspan="²"',
        'colspan="五"',
        'rowspan="²"',
        'rowspan="五"',
        'colspan="１"',  # full-width digit
        'colspan="٣"',  # Arabic-Indic digit
        'colspan="２"',  # full-width digit two
        'colspan="99999"',
        'rowspan="-1"',
        'colspan="2.5"',
        'rowspan=""',
    ],
)
def test_odd_span_attributes_do_not_crash(attrs: str) -> None:
    body = (
        f"<table><tr><th {attrs}>H</th><th>X</th></tr><tr><td {attrs}>a</td><td>b</td></tr></table>"
    )
    assert render(body) == "| H | X |\n| --- | --- |\n| a | b |\n"


@pytest.mark.parametrize("start", ["²", "五", "１", "-3", "x", "99999"])
def test_odd_list_start_is_ignored(start: str) -> None:
    assert render(f'<ol start="{start}"><li>one</li><li>two</li></ol>') == "1. one\n2. two\n"


def test_valid_span_and_start_attributes_are_kept() -> None:
    assert render('<ol start="3"><li>three</li></ol>') == "3. three\n"
    body = '<table><tr><td colspan="2">wide</td></tr><tr><td>a</td><td>b</td></tr></table>'
    assert render(body) == "| wide | |\n| --- | --- |\n| a | b |\n"


def test_padded_colspan_with_rowspan_keeps_columns_aligned() -> None:
    body = (
        "<table><tr><th>A</th><th>B</th><th>C</th></tr>"
        '<tr><td rowspan="2" colspan=" 2 ">ab</td><td>c1</td></tr><tr><td>c2</td></tr></table>'
    )
    rows = render(body).splitlines()
    assert {row.count("|") for row in rows} == {4}


# --------------------------------------------------------------------------------------------
# render_body: inline formatting
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("<p><em>Amazon</em><em> Bedrock</em> is new</p>", "*Amazon Bedrock* is new\n"),
        ("<p><strong>AWS</strong> <strong>Lambda</strong></p>", "**AWS Lambda**\n"),
        ("<p><strong>a</strong><strong>b</strong><strong>c</strong></p>", "**abc**\n"),
        ("<p><code>foo</code><code>bar</code></p>", "`foobar`\n"),
        ("<p><em>x</em>, <em>y</em></p>", "*x*, *y*\n"),
    ],
    ids=["em", "strong-with-space", "three-fragments", "code", "not-adjacent"],
)
def test_fragmented_inline_formatting_is_merged(body: str, expected: str) -> None:
    assert render(body) == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("<p>A<em>/</em>B</p>", "A/B\n"),
        ("<p>key<strong>:</strong> value</p>", "key: value\n"),
        ("<p><em>(</em>x<em>)</em></p>", "(x)\n"),
        ("<p>a<strong> </strong>b</p>", "a b\n"),
        ("<p>a<em></em>b<span>c</span></p>", "abc\n"),
        ("<p>a<strong>\u200b</strong>b</p>", "ab\n"),
        ("<p><strong>Note: </strong>text</p>", "**Note:** text\n"),
    ],
    ids=["slash", "colon", "parens", "space-only", "empty", "zero-width-only", "trailing-space"],
)
def test_redundant_inline_formatting_is_unwrapped(body: str, expected: str) -> None:
    assert render(body) == expected


def test_emphasis_around_an_image_is_kept() -> None:
    body = (
        '<p><strong><img src="https://cdn.example.invalid/i.png" alt="I"></strong> text '
        '<em><img src="https://cdn.example.invalid/j.png" alt=""></em></p>'
    )
    assert render(body) == (
        "**![I](https://cdn.example.invalid/i.png)** text *![](https://cdn.example.invalid/j.png)*\n"
    )


def test_leading_and_trailing_breaks_are_removed() -> None:
    assert render("<p><br>text<br></p><ul><li>item<br><br></li></ul>") == "text\n\n- item\n"


def test_empty_paragraphs_are_dropped() -> None:
    assert render("<p>Text</p><p>&nbsp;</p><!-- '\"` --><p> </p><p>\u200b</p>") == "Text\n"


# --------------------------------------------------------------------------------------------
# render_body: text normalisation
# --------------------------------------------------------------------------------------------


def test_special_spaces_and_zero_width_characters_in_prose() -> None:
    body = "<p>a\xa0b\u202fc\u2007d\u200be\u200cf\u200dg\u2060h\ufeffi</p>"
    assert render(body) == "a b c defghi\n"


def test_prose_is_nfc_normalised() -> None:
    assert (
        render("<p>cafe\u0301 \u1100\u1161 <code>cafe\u0301</code></p>") == "caf\xe9 가 `caf\xe9`\n"
    )


def test_code_blocks_are_not_nfc_normalised_but_nbsp_is_replaced() -> None:
    out = render("<pre>cafe\u0301 \u1100\u1161\xa0x\u202fy\u2007z</pre>")
    assert out == "```\ncafe\u0301 \u1100\u1161 x y z\n```\n"


def test_space_runs_collapse_outside_code_only() -> None:
    body = "<p>a     b\t\t c\xa0\xa0\xa0d</p><pre>keep     these\t\tspaces</pre>"
    assert render(body) == "a b c d\n\n```\nkeep     these\t\tspaces\n```\n"


@pytest.mark.parametrize(
    "body",
    [
        "<p>a\x00b</p><p>c\x00\nd</p>",
        "<p>a&#0;b</p><p>c&#x0;\nd</p>",
        "<pre>x\x00y</pre>",
        '<p><img src="https://cdn.example.invalid/a.png" alt="x\x00y"></p>',
    ],
    ids=["raw", "entity", "code", "alt-text"],
)
def test_nul_characters_never_reach_the_markdown(body: str) -> None:
    out = render(body)
    assert "\x00" not in out
    assert "\\" not in out  # NUL is the hard-break placeholder; it must not turn into one


@pytest.mark.parametrize(
    "run",
    ["\xa0" * 40_000, " " * 40_000, "&nbsp; " * 20_000, "\u202f\u2007" * 20_000, "\t " * 20_000],
    ids=["nbsp", "spaces", "nbsp-entities", "narrow-spaces", "tabs"],
)
def test_long_space_runs_render_fast(run: str) -> None:
    started = time.perf_counter()
    out = render(f"<p>a{run}b</p><p><strong>x{run}</strong>y</p>")
    elapsed = time.perf_counter() - started
    assert out == "a b\n\n**x** y\n"
    assert elapsed < 0.5, f"{elapsed:.2f}s"


# --------------------------------------------------------------------------------------------
# render_body: image modes
# --------------------------------------------------------------------------------------------

IMAGES_BODY = (
    '<p><img src="https://cdn.example.invalid/a.png" alt="Architecture diagram"></p>'
    '<p>Text <a href="https://example.com/x">'
    '<img src="https://cdn.example.invalid/b.png" alt=""></a></p>'
    '<p><img src="https://cdn.example.invalid/c.png" alt=""></p>'
    '<p><a href="https://example.com/launch">'
    '<img src="https://cdn.example.invalid/d.png" alt="Launch"></a></p>'
    "<table><tr><th>A</th><th>B</th></tr>"
    '<tr><td><img src="https://cdn.example.invalid/t.png" alt="T"></td><td>x</td></tr></table>'
    "<p>tail</p>"
)


def test_image_mode_markdown() -> None:
    assert render(IMAGES_BODY) == (
        "![Architecture diagram](https://cdn.example.invalid/a.png)\n\n"
        "Text [![](https://cdn.example.invalid/b.png)](https://example.com/x)\n\n"
        "![](https://cdn.example.invalid/c.png)\n\n"
        "[![Launch](https://cdn.example.invalid/d.png)](https://example.com/launch)\n\n"
        "| A | B |\n| --- | --- |\n| ![T](https://cdn.example.invalid/t.png) | x |\n\n"
        "tail\n"
    )


def test_image_mode_alt() -> None:
    assert render(IMAGES_BODY, images="alt") == (
        "[Image: Architecture diagram]\n\n"
        "Text\n\n"
        "[[Image: Launch]](https://example.com/launch)\n\n"
        "| A | B |\n| --- | --- |\n| [Image: T] | x |\n\n"
        "tail\n"
    )


def test_image_mode_none() -> None:
    out = render(IMAGES_BODY, images="none")
    assert out == "Text\n\n| A | B |\n| --- | --- |\n|  | x |\n\ntail\n"
    assert "Image" not in out
    assert "example.com/launch" not in out


def test_image_mode_none_keeps_link_text() -> None:
    body = (
        '<p><a href="https://example.com/x">'
        '<img src="https://cdn.example.invalid/b.png" alt="Pic">Label</a></p>'
    )
    assert render(body, images="none") == "[Label](https://example.com/x)\n"


def test_image_modes_with_captions() -> None:
    body = (
        '<div class="wp-caption"><img src="https://cdn.example.invalid/b.png" alt="Alt">'
        '<p class="wp-caption-text">Cap</p></div>'
    )
    assert render(body, images="alt") == "[Image: Alt]\n\n*Cap*\n"
    assert render(body, images="none") == "*Cap*\n"


def test_image_only_heading_in_none_mode_disappears() -> None:
    assert (
        render(
            '<h4><img src="https://cdn.example.invalid/b.png" alt="Shot"></h4><p>x</p>',
            images="none",
        )
        == "x\n"
    )


def test_empty_body_renders_empty_string() -> None:
    assert render("") == ""
    assert render("<p> </p><script>x</script>") == ""


# --------------------------------------------------------------------------------------------
# inline markup that markdown cannot express (found on real posts)
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("<p><b>Install: <br/></b><code>pip</code></p>", "**Install:**\\\n`pip`\n"),
        (
            "<ol><li><b><b>Packages: <br/></b></b><code>pip install x</code></li></ol>",
            "1. **Packages:**\\\n   `pip install x`\n",
        ),
        ("<p><em><br>lead</em> text</p>", "*lead* text\n"),
        ("<ul><li>a<br><ul><li>b</li></ul></li></ul>", "- a\n  - b\n"),
    ],
    ids=["br-ends-bold", "nested-bold", "br-starts-em", "br-before-nested-list"],
)
def test_breaks_and_nesting_inside_emphasis(body: str, expected: str) -> None:
    assert render(body) == expected


def test_code_block_inside_bold_is_lifted_out() -> None:
    out = render("<div><strong>Tip<pre>cmd</pre></strong></div><p>a<br>b</p><pre>z</pre>")
    assert out == "**Tip**\n\n```\ncmd\n```\n\na\\\nb\n\n```\nz\n```\n"


def test_code_block_in_the_middle_of_emphasis_keeps_order() -> None:
    out = render("<p><em>before <pre>x = 1</pre> after</em></p>")
    assert out.index("*before*") < out.index("x = 1") < out.index("after*")


def test_paragraph_that_only_looks_like_a_fence_leaks_no_placeholder() -> None:
    out = render("<p>```bash<br>echo hi<br>```</p>")
    assert "\x00" not in out
    assert out == "```bash\necho hi\n```\n"


def test_blockquote_with_code_stays_one_quote() -> None:
    out = render("<blockquote><p>Note: run this</p><pre>cmd</pre><p>then this</p></blockquote>")
    assert out == "> Note: run this\n>\n> ```\n> cmd\n> ```\n>\n> then this\n"


def test_callout_table_with_code_stays_one_quote() -> None:
    out = render("<table><tr><td><p>Run:</p><pre>cmd --flag</pre><p>Done.</p></td></tr></table>")
    assert all(line.startswith(">") for line in out.strip().split("\n")), out
