"""MCP-layer tests: tool metadata, argument validation and tool behaviour against ``FakeAws``.

Most tests use the conftest ``client`` fixture, which runs every test once per protocol mode
(2026 discovery and the legacy initialize handshake). Fixture content is synthetic.
"""

from __future__ import annotations

import functools
import importlib.resources
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, tzinfo
from typing import Any, Self

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

import aws_blog_mcp.index as index_module
import aws_blog_mcp.server as server_module
from aws_blog_mcp import __version__
from aws_blog_mcp.http import PROJECT_URL, user_agent
from aws_blog_mcp.index import BlogIndex
from aws_blog_mcp.models import CategoriesResult, PostListResult, SearchPostsResult
from aws_blog_mcp.reader import CONTENT_NOTE, CONTINUED_NOTE
from aws_blog_mcp.server import INSTRUCTIONS, LOG_LEVEL_ENV, create_server, main
from tests.fakes import BASE, FakeAws, cat, make_item, post_page

pytestmark = pytest.mark.anyio

# --------------------------------------------------------------------------------------------
# Synthetic site
# --------------------------------------------------------------------------------------------

COMPUTE = cat("compute", "Compute")
EKS = cat("amazon-eks", "Amazon Elastic Kubernetes Service", "compute")
AI = cat("artificial-intelligence", "Artificial Intelligence")
BEDROCK = cat("amazon-bedrock", "Amazon Bedrock", "artificial-intelligence")
KIRO = cat("kiro", "Kiro", "artificial-intelligence")
DATABASES = cat("databases", "Databases")
POST_TYPES = cat("post-types", "*Post Types")
HOW_TO = cat("technical-how-to", "Technical How-to", "post-types")
CUSTOMER = cat("customer-solutions", "Customer Solutions", "post-types")
ADVANCED = cat("advanced-300", "Advanced (300)", "learning-levels")


def _items() -> list[dict[str, Any]]:
    return [
        make_item(
            101,
            "Amazon EKS 비용 최적화 가이드",
            slug="eks-cost-optimization",
            created="2026-03-10T01:00:00Z",
            categories=[COMPUTE, EKS, POST_TYPES, HOW_TO],
            contributors="Kim Minsu, Lee Jiyoung",
            excerpt="클러스터 노드 비용을 줄이는 방법을 소개합니다. [...]",
        ),
        make_item(
            102,
            "Amazon Bedrock으로 에이전트 만들기",
            slug="build-agents-with-bedrock",
            created="2026-03-05T01:00:00Z",
            categories=[AI, BEDROCK],
            contributors="Park Seojun",
            excerpt="생성형 AI 애플리케이션을 구축합니다. [...]",
        ),
        make_item(
            108,
            "Amazon DynamoDB 설계 패턴",
            slug="dynamodb-design-patterns",
            # 2026-03-01 00:30 in Korea: the date filters must use Korea time.
            created="2026-02-28T15:30:00Z",
            categories=[DATABASES],
            contributors="Jung Hoon",
            excerpt="단일 테이블 설계를 다룹니다. [...]",
        ),
        make_item(
            103,
            "쿠버네티스 클러스터 업그레이드 전략",
            slug="kubernetes-upgrade",
            created="2026-02-20T01:00:00Z",
            categories=[EKS, ADVANCED],
            contributors="Kim Minsu",
            excerpt="무중단 업그레이드 절차를 설명합니다. [...]",
        ),
        make_item(
            104,
            "Kiro로 시작하는 스펙 기반 개발",
            slug="kiro-spec-driven",
            created="2026-02-01T01:00:00Z",
            categories=[KIRO],
            contributors="Choi Yuna, Ph.D",
            excerpt="AI 에이전트와 함께 개발하는 방법입니다. [...]",
        ),
        make_item(
            105,
            "Aurora PostgreSQL 마이그레이션 사례",
            slug="aurora-migration-case",
            created="2025-12-15T01:00:00Z",
            categories=[DATABASES, CUSTOMER],
            contributors="Jung Hoon",
            excerpt="고객 사례를 소개합니다. [...]",
            global_tags=[("GLOBAL#tech-category", "데이터베이스")],
        ),
        make_item(
            106,
            "게임 서버 아키텍처",
            slug="game-server",
            created="2025-11-01T01:00:00Z",
            categories=[COMPUTE],
            contributors="Lee Jiyoung",
            excerpt="Amazon EKS에서 게임 서버를 운영합니다. [...]",
        ),
        make_item(107, "주간 소식", slug="weekly-news", created="2025-10-01T01:00:00Z"),
    ]


ALL_IDS_NEWEST_FIRST = [
    "65-101",
    "65-102",
    "65-108",
    "65-103",
    "65-104",
    "65-105",
    "65-106",
    "65-107",
]

EKS_URL = BASE + "eks-cost-optimization/"
LONG_URL = BASE + "kubernetes-upgrade/"
UNINDEXED_URL = BASE + "not-in-index/"
IMG = "https://d2908q01vomqb2.cloudfront.net/test/arch.png"
EKS_BODY = f"""
<h2>개요</h2>
<p>이 글은 <strong>합성</strong> 테스트 본문입니다.</p>
<p><img src="{IMG}" alt="아키텍처 다이어그램" width="800"></p>
<pre class="lang-python">def handler(event):
    return {{"ok": True}}</pre>
<table><tr><th>항목</th><th>값</th></tr><tr><td>노드</td><td>3</td></tr></table>
<h2>정리</h2>
<p>끝.</p>
"""
LONG_BODY = "".join(f"<h2>섹션 {i}</h2><p>{'가나다라마바사 ' * 30}</p>" for i in range(12))


def _pages() -> dict[str, str]:
    return {
        EKS_URL: post_page(
            url=EKS_URL,
            title="Amazon EKS 비용 최적화 가이드",
            body=EKS_BODY,
            byline="Kim Minsu and Lee Jiyoung",
            published="2026-03-10T10:00:00+09:00",
            modified="2026-03-12T10:00:00+09:00",
            categories=[
                ("compute", "Compute"),
                ("compute/amazon-eks", "Amazon Elastic Kubernetes Service"),
            ],
            author_boxes=["Kim Minsu", "Lee Jiyoung"],
        ),
        LONG_URL: post_page(
            url=LONG_URL,
            title="쿠버네티스 클러스터 업그레이드 전략",
            body=LONG_BODY,
            published="2026-02-20T10:00:00+09:00",
        ),
        UNINDEXED_URL: post_page(
            url=UNINDEXED_URL, title="색인에 없는 글", body="<p>아직 목록에 없는 글입니다.</p>"
        ),
    }


EKS_EXPECTED = f"""\
# Amazon EKS 비용 최적화 가이드

- URL: {EKS_URL}
- Authors: Kim Minsu; Lee Jiyoung
- Published: 2026-03-10 (updated 2026-03-12)
- Categories: Compute, Amazon Elastic Kubernetes Service
- Content: characters 0-205 of 205

> {CONTENT_NOTE}

--- BEGIN POST CONTENT ---
## 개요

이 글은 **합성** 테스트 본문입니다.

![아키텍처 다이어그램]({IMG})

```python
def handler(event):
    return {{"ok": True}}
```

| 항목 | 값 |
| --- | --- |
| 노드 | 3 |

## 정리

끝.

--- END POST CONTENT ---"""

NO_RESULTS_NOTICE = (
    "No posts found. Try fewer or different keywords (the English or Korean name), or remove "
    "filters."
)
ANY_TERM_NOTICE = (
    "No post contains all query terms; showing the posts that best match some of them "
    "(distinctive terms weigh more)."
)
SIMILAR_HINT = "Call list_categories with a query to search category names."
REJECTED = {"items": [], "metadata": {"count": 0, "totalHits": 0}, "fieldTypes": {}}
TOOL_NAMES = {"search_posts", "list_recent_posts", "list_categories", "get_post"}


@pytest.fixture
def site(fake_aws: FakeAws) -> FakeAws:
    """Populate the fake with the synthetic blog (the index loads lazily on first use)."""
    fake_aws.items = _items()
    fake_aws.pages.update(_pages())
    return fake_aws


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _text(result: CallToolResult) -> str:
    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def _ok(result: CallToolResult) -> dict[str, Any]:
    """Return the structured result, checking the text content is the same data as compact JSON."""
    assert not result.is_error, _text(result)
    data = result.structured_content
    assert data is not None
    text = _text(result)
    assert json.loads(text) == data
    assert text == json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return data


def _error(result: CallToolResult, tool: str) -> str:
    """Return the error message of a failed call (without the SDK prefix)."""
    assert result.is_error
    assert result.structured_content is None
    text = _text(result)
    prefix = f"Error executing tool {tool}: "
    assert text.startswith(prefix), text
    return text[len(prefix) :]


def _ids(data: dict[str, Any]) -> list[str]:
    return [p["id"] for p in data["posts"]]


async def _call(client: Client, tool: str, **args: Any) -> CallToolResult:
    return await client.call_tool(tool, args)


async def _tools(client: Client) -> dict[str, Tool]:
    listing = await client.list_tools()
    assert listing.next_cursor is None
    return {t.name: t for t in listing.tools}


BEGIN = "--- BEGIN POST CONTENT ---"
END = "--- END POST CONTENT ---"
_FOOTER_RE = re.compile(
    r"(?:\n\[Truncated: \d+ characters remain\. Call get_post with start_index=(\d+)"
    r'(?: and images="(?:alt|none)")? to continue\.\])?'
)


def _split_post(text: str) -> tuple[str, str, int | None]:
    """Split get_post output into ``(header, chunk, next_start)``.

    The header includes the reference-material note; the chunk is what sits between the
    begin/end markers; anything after the end marker must be the truncation hint.
    """
    header, sep, rest = text.partition(f"\n\n{BEGIN}\n")
    assert sep, text
    chunk, sep, footer = rest.rpartition(f"\n\n{END}")
    assert sep, text
    match = _FOOTER_RE.fullmatch(footer)
    assert match, footer
    return header, chunk, int(match.group(1)) if match.group(1) else None


def _content_range(header: str) -> tuple[int, int, int]:
    match = re.search(r"^- Content: characters (\d+)-(\d+) of (\d+)$", header, re.M)
    assert match, header
    start, end, total = (int(g) for g in match.groups())
    return start, end, total


# --------------------------------------------------------------------------------------------
# Server and tool metadata
# --------------------------------------------------------------------------------------------


async def test_server_identity_and_instructions(client: Client) -> None:
    info = client.server_info
    assert info is not None
    assert info.name == "aws-blog-mcp"
    assert info.title == "AWS Blog MCP (unofficial)"
    assert info.version == __version__
    assert info.website_url == PROJECT_URL
    assert client.instructions == INSTRUCTIONS
    assert 0 < len(INSTRUCTIONS) <= 2048
    for name in TOOL_NAMES:
        assert name in INSTRUCTIONS


async def test_exact_tool_set_and_titles(client: Client) -> None:
    tools = await _tools(client)
    assert {name: t.title for name, t in tools.items()} == {
        "search_posts": "Search AWS Korea tech blog posts",
        "list_recent_posts": "List recent AWS Korea tech blog posts",
        "list_categories": "List AWS Korea tech blog categories",
        "get_post": "Read an AWS Korea tech blog post",
    }


async def test_all_tools_are_read_only_and_open_world(client: Client) -> None:
    for tool in (await _tools(client)).values():
        annotations = tool.annotations
        assert annotations == ToolAnnotations(read_only_hint=True, open_world_hint=True)
        assert annotations is not None
        assert annotations.read_only_hint is True
        assert annotations.open_world_hint is True
        assert annotations.destructive_hint is None


async def test_output_schemas(client: Client) -> None:
    tools = await _tools(client)
    assert tools["search_posts"].output_schema == SearchPostsResult.model_json_schema()
    assert tools["list_recent_posts"].output_schema == PostListResult.model_json_schema()
    assert tools["list_categories"].output_schema == CategoriesResult.model_json_schema()
    assert tools["get_post"].output_schema is None


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("search_posts", {"query": "EKS"}),
        ("list_recent_posts", {"limit": 2}),
        ("list_categories", {"limit": 3}),
    ],
)
async def test_list_tools_return_compact_json_text(
    client: Client, site: FakeAws, tool: str, args: dict[str, Any]
) -> None:
    result = await client.call_tool(tool, args)
    assert not result.is_error
    data = result.structured_content
    assert data is not None
    text = _text(result)
    # The text block is the structured result itself, serialised without indentation.
    assert text == json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    assert "\n" not in text
    assert text.startswith('{"blog":"AWS 기술 블로그",')


async def test_get_post_images_description_asks_for_a_stable_value(client: Client) -> None:
    images = (await _tools(client))["get_post"].input_schema["properties"]["images"]
    assert "use the same value for every chunk" in images["description"]


async def test_descriptions_are_short_and_free_of_docstring_sections(client: Client) -> None:
    for tool in (await _tools(client)).values():
        description = tool.description or ""
        assert 0 < len(description) <= 2048, tool.name
        assert "Args:" not in description
        assert "Returns:" not in description
        assert "ctx" not in description
        properties = tool.input_schema["properties"]
        assert "ctx" not in properties
        for name, prop in properties.items():
            assert prop.get("description"), f"{tool.name}.{name} has no description"
            assert "ctx" not in prop["description"]


def _constraints(prop: dict[str, Any]) -> dict[str, Any]:
    keys = ("type", "minimum", "maximum", "minLength", "maxLength", "enum", "default", "format")
    flat = dict(prop)
    for variant in prop.get("anyOf", []):  # optional params: anyOf [constrained, null]
        if variant.get("type") != "null":
            flat.update(variant)
    return {k: flat[k] for k in keys if k in flat}


async def test_input_schemas(client: Client) -> None:
    tools = await _tools(client)
    schemas = {name: t.input_schema for name, t in tools.items()}
    props = {name: s["properties"] for name, s in schemas.items()}

    assert schemas["search_posts"]["required"] == ["query"]
    assert schemas["get_post"]["required"] == ["url"]
    assert "required" not in schemas["list_recent_posts"]
    assert "required" not in schemas["list_categories"]

    assert list(props["search_posts"]) == [
        "query",
        "category",
        "author",
        "published_after",
        "published_before",
        "sort",
        "limit",
        "offset",
    ]
    assert list(props["list_recent_posts"]) == [
        "category",
        "author",
        "published_after",
        "published_before",
        "limit",
        "offset",
    ]
    assert list(props["list_categories"]) == ["query", "parent", "limit"]
    assert list(props["get_post"]) == ["url", "start_index", "max_length", "images"]

    search = props["search_posts"]
    assert _constraints(search["query"]) == {"type": "string", "minLength": 1, "maxLength": 200}
    assert _constraints(search["sort"]) == {
        "type": "string",
        "enum": ["relevance", "newest"],
        "default": "relevance",
    }
    assert _constraints(search["published_after"]) == {
        "type": "string",
        "format": "date",
        "default": None,
    }
    for tool in ("search_posts", "list_recent_posts"):
        assert _constraints(props[tool]["limit"]) == {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "default": 10,
        }
        assert _constraints(props[tool]["offset"]) == {
            "type": "integer",
            "minimum": 0,
            "default": 0,
        }
        assert _constraints(props[tool]["category"]) == {
            "type": "string",
            "maxLength": 200,
            "default": None,
        }
    assert _constraints(props["list_categories"]["limit"]) == {
        "type": "integer",
        "minimum": 1,
        "maximum": 500,
        "default": 50,
    }
    get_post = props["get_post"]
    assert _constraints(get_post["url"]) == {"type": "string", "minLength": 1, "maxLength": 500}
    assert _constraints(get_post["start_index"]) == {"type": "integer", "minimum": 0, "default": 0}
    assert _constraints(get_post["max_length"]) == {
        "type": "integer",
        "minimum": 1000,
        "maximum": 50000,
        "default": 10000,
    }
    assert _constraints(get_post["images"]) == {
        "type": "string",
        "enum": ["markdown", "alt", "none"],
        "default": "markdown",
    }


@pytest.mark.parametrize(
    ("tool", "args", "field", "message"),
    [
        ("search_posts", {}, "query", "Field required"),
        ("search_posts", {"query": ""}, "query", "String should have at least 1 character"),
        (
            "search_posts",
            {"query": "x" * 201},
            "query",
            "String should have at most 200 characters",
        ),
        ("search_posts", {"query": "eks", "limit": 0}, "limit", "greater than or equal to 1"),
        ("search_posts", {"query": "eks", "limit": 51}, "limit", "less than or equal to 50"),
        ("search_posts", {"query": "eks", "offset": -1}, "offset", "greater than or equal to 0"),
        ("search_posts", {"query": "eks", "sort": "oldest"}, "sort", "'relevance' or 'newest'"),
        (
            "search_posts",
            {"query": "eks", "published_after": "2026-13-01"},
            "published_after",
            "Input should be a valid date",
        ),
        ("search_posts", {"query": "eks", "limit": "many"}, "limit", "valid integer"),
        ("list_recent_posts", {"limit": 100}, "limit", "less than or equal to 50"),
        ("list_recent_posts", {"author": "a" * 101}, "author", "at most 100 characters"),
        ("list_categories", {"limit": 0}, "limit", "greater than or equal to 1"),
        ("list_categories", {"limit": 501}, "limit", "less than or equal to 500"),
        ("get_post", {}, "url", "Field required"),
        ("get_post", {"url": "x" * 501}, "url", "String should have at most 500 characters"),
        (
            "get_post",
            {"url": "x", "max_length": 999},
            "max_length",
            "greater than or equal to 1000",
        ),
        (
            "get_post",
            {"url": "x", "max_length": 50001},
            "max_length",
            "less than or equal to 50000",
        ),
        ("get_post", {"url": "x", "start_index": -1}, "start_index", "greater than or equal to 0"),
        ("get_post", {"url": "x", "images": "html"}, "images", "'markdown', 'alt' or 'none'"),
    ],
)
async def test_invalid_arguments_are_tool_errors(
    client: Client,
    fake_aws: FakeAws,
    tool: str,
    args: dict[str, Any],
    field: str,
    message: str,
) -> None:
    error = _error(await client.call_tool(tool, args), tool)
    assert error.startswith(f"1 validation error for {tool}Arguments\n{field}\n"), error
    assert message in error
    assert fake_aws.requests == []


# --------------------------------------------------------------------------------------------
# search_posts
# --------------------------------------------------------------------------------------------


async def test_search_ranks_title_matches_above_excerpt_matches(
    client: Client, site: FakeAws
) -> None:
    data = _ok(await _call(client, "search_posts", query="EKS"))
    assert data == {
        "blog": "AWS 기술 블로그",
        "query": "EKS",
        "match": "all_terms",
        "total": 2,
        "offset": 0,
        "next_offset": None,
        "notice": None,
        "posts": [
            {
                "id": "65-101",
                "title": "Amazon EKS 비용 최적화 가이드",
                "url": EKS_URL,
                "published": "2026-03-10",
                "authors": ["Kim Minsu", "Lee Jiyoung"],
                "categories": ["Compute", "Amazon Elastic Kubernetes Service", "Technical How-to"],
                "excerpt": "클러스터 노드 비용을 줄이는 방법을 소개합니다.…",
            },
            {
                "id": "65-106",
                "title": "게임 서버 아키텍처",
                "url": BASE + "game-server/",
                "published": "2025-11-01",
                "authors": ["Lee Jiyoung"],
                "categories": ["Compute"],
                "excerpt": "Amazon EKS에서 게임 서버를 운영합니다.…",
            },
        ],
    }
    SearchPostsResult.model_validate(data)


async def test_search_synonyms_relevance_and_newest_sort(client: Client, site: FakeAws) -> None:
    # 65-103 has 쿠버네티스 in its title; 65-101 only matches "Kubernetes" in a category name.
    relevance = _ok(await _call(client, "search_posts", query="쿠버네티스"))
    assert _ids(relevance) == ["65-103", "65-101"]
    newest = _ok(await _call(client, "search_posts", query="쿠버네티스", sort="newest"))
    assert _ids(newest) == ["65-101", "65-103"]
    assert newest["total"] == 2
    english = _ok(await _call(client, "search_posts", query="kubernetes"))
    assert _ids(english) == ["65-103", "65-101"]


async def test_search_strips_korean_particles(client: Client, site: FakeAws) -> None:
    data = _ok(await _call(client, "search_posts", query="쿠버네티스를"))
    assert _ids(data) == ["65-103", "65-101"]


async def test_search_category_filter_includes_subcategories(client: Client, site: FakeAws) -> None:
    unfiltered = _ok(await _call(client, "search_posts", query="에이전트"))
    assert _ids(unfiltered) == ["65-102", "65-104"]
    by_name = _ok(await _call(client, "search_posts", query="에이전트", category="Amazon Bedrock"))
    assert _ids(by_name) == ["65-102"]
    by_path = _ok(
        await _call(
            client, "search_posts", query="에이전트", category="artificial-intelligence/kiro"
        )
    )
    assert _ids(by_path) == ["65-104"]
    parent = _ok(
        await _call(client, "search_posts", query="에이전트", category="artificial intelligence")
    )
    assert _ids(parent) == ["65-102", "65-104"]


async def test_search_author_and_date_filters(client: Client, site: FakeAws) -> None:
    by_author = _ok(await _call(client, "search_posts", query="쿠버네티스", author="lee"))
    assert _ids(by_author) == ["65-101"]
    after = _ok(
        await _call(client, "search_posts", query="쿠버네티스", published_after="2026-03-01")
    )
    assert _ids(after) == ["65-101"]
    window = _ok(
        await _call(
            client,
            "search_posts",
            query="쿠버네티스",
            published_after="2026-02-20",
            published_before="2026-02-20",
        )
    )
    assert _ids(window) == ["65-103"]
    none = _ok(await _call(client, "search_posts", query="쿠버네티스", author="nobody"))
    assert none["total"] == 0
    assert none["notice"] == NO_RESULTS_NOTICE


async def test_search_any_term_fallback(client: Client, site: FakeAws) -> None:
    data = _ok(await _call(client, "search_posts", query="Kiro 오로라"))
    assert data["match"] == "any_term"
    assert data["notice"] == ANY_TERM_NOTICE
    assert _ids(data) == ["65-104", "65-105"]
    assert data["total"] == 2


async def test_search_no_results_notice(client: Client, site: FakeAws) -> None:
    data = _ok(await _call(client, "search_posts", query="존재하지않는단어"))
    assert data["match"] == "all_terms"
    assert data["total"] == 0
    assert data["posts"] == []
    assert data["next_offset"] is None
    assert data["notice"] == NO_RESULTS_NOTICE


async def test_search_no_results_notice_for_multi_word_query(client: Client, site: FakeAws) -> None:
    data = _ok(await _call(client, "search_posts", query="존재하지않는단어 다른단어"))
    assert data["total"] == 0
    assert data["notice"] == NO_RESULTS_NOTICE


async def test_search_any_term_newest_keeps_only_the_best_tier(
    client: Client, fake_aws: FakeAws
) -> None:
    fake_aws.items = [
        # Matches the common term only weakly (excerpt), but is the newest post.
        make_item(
            3, "주간 업데이트", created="2026-03-01T00:00:00Z", excerpt="Graviton 소식 [...]"
        ),
        make_item(1, "Graviton 성능 비교", created="2026-02-01T00:00:00Z"),
        make_item(2, "Inferentia 추론 비용", created="2026-01-01T00:00:00Z"),
        make_item(4, "주간 소식", created="2025-12-01T00:00:00Z"),
        make_item(5, "공지", created="2025-11-01T00:00:00Z"),
    ]
    relevance = _ok(await _call(client, "search_posts", query="Graviton Inferentia"))
    assert relevance["match"] == "any_term"
    assert _ids(relevance) == ["65-2", "65-1", "65-3"]  # the rarer term weighs more
    assert relevance["total"] == 3

    newest = _ok(await _call(client, "search_posts", query="Graviton Inferentia", sort="newest"))
    # Only hits scoring at least half of the best one survive, then newest first.
    assert newest["match"] == "any_term"
    assert _ids(newest) == ["65-1", "65-2"]
    assert newest["total"] == 2
    assert newest["notice"] == ANY_TERM_NOTICE


async def test_search_ignores_question_words_and_particles(client: Client, site: FakeAws) -> None:
    plain = _ok(await _call(client, "search_posts", query="EKS"))
    for query in ("EKS 관련 게시물을 찾아줘", "show me the latest EKS posts", "블로그에서 EKS"):
        data = _ok(await _call(client, "search_posts", query=query))
        assert (data["match"], _ids(data)) == ("all_terms", _ids(plain)), query


async def test_search_pagination(client: Client, fake_aws: FakeAws) -> None:
    fake_aws.items = [
        make_item(200 + n, f"AWS Lambda 패턴 {n}", created=f"2025-01-{n:02d}T00:00:00Z")
        for n in range(1, 26)
    ]
    newest_first = [f"65-{200 + n}" for n in range(25, 0, -1)]

    pages: list[str] = []
    offset: int | None = 0
    seen_offsets: list[int] = []
    while offset is not None:
        seen_offsets.append(offset)
        data = _ok(await _call(client, "search_posts", query="람다", offset=offset))
        assert data["total"] == 25
        assert data["offset"] == offset
        pages += _ids(data)
        offset = data["next_offset"]
    assert seen_offsets == [0, 10, 20]
    assert pages == newest_first  # equal scores keep the newest-first order

    last = _ok(await _call(client, "search_posts", query="lambda", offset=23, limit=1))
    assert (_ids(last), last["next_offset"]) == (["65-202"], 24)
    final = _ok(await _call(client, "search_posts", query="lambda", offset=24, limit=1))
    assert (_ids(final), final["next_offset"]) == (["65-201"], None)
    past = _ok(await _call(client, "search_posts", query="lambda", offset=100))
    assert (past["posts"], past["total"], past["next_offset"]) == ([], 25, None)
    many = _ok(await _call(client, "search_posts", query="lambda", limit=50))
    assert (len(many["posts"]), many["next_offset"]) == (25, None)


async def test_search_invalid_date_range(client: Client, site: FakeAws) -> None:
    result = await _call(
        client,
        "search_posts",
        query="eks",
        published_after="2026-03-02",
        published_before="2026-03-01",
    )
    assert _error(result, "search_posts") == (
        "published_after must not be later than published_before."
    )
    assert site.requests == []


async def test_search_unknown_category_suggests_close_matches(
    client: Client, site: FakeAws
) -> None:
    result = await _call(client, "search_posts", query="에이전트", category="Amazon Bedrok")
    assert _error(result, "search_posts") == (
        "Unknown category 'Amazon Bedrok'. Similar categories: Amazon Bedrock "
        f"(artificial-intelligence/amazon-bedrock). {SIMILAR_HINT}"
    )
    result = await _call(client, "search_posts", query="에이전트", category="zzzz")
    assert _error(result, "search_posts") == f"Unknown category 'zzzz'. {SIMILAR_HINT}"


async def test_search_query_without_searchable_words(client: Client, site: FakeAws) -> None:
    result = await _call(client, "search_posts", query="!!! ???")
    assert _error(result, "search_posts") == (
        "The query has no searchable words. Use keywords such as a service name or topic."
    )
    assert site.requests == []


# --------------------------------------------------------------------------------------------
# list_recent_posts
# --------------------------------------------------------------------------------------------


async def test_list_recent_posts_newest_first(client: Client, site: FakeAws) -> None:
    data = _ok(await _call(client, "list_recent_posts"))
    assert data["blog"] == "AWS 기술 블로그"
    assert data["total"] == 8
    assert data["next_offset"] is None
    assert data["notice"] is None
    assert _ids(data) == ALL_IDS_NEWEST_FIRST
    # Korea-time dates: 2026-02-28T15:30Z is 2026-03-01 in Seoul.
    assert data["posts"][2]["published"] == "2026-03-01"
    # Uncategorized posts list no category; facet roots ("Post Types") are not shown.
    assert data["posts"][-1]["categories"] == []
    assert data["posts"][0]["categories"] == [
        "Compute",
        "Amazon Elastic Kubernetes Service",
        "Technical How-to",
    ]
    assert data["posts"][4]["authors"] == ["Choi Yuna, Ph.D"]
    PostListResult.model_validate(data)
    assert set(data) == {"blog", "total", "offset", "next_offset", "posts", "notice"}


async def test_list_recent_posts_paging(client: Client, site: FakeAws) -> None:
    first = _ok(await _call(client, "list_recent_posts", limit=3))
    assert (_ids(first), first["next_offset"], first["total"]) == (
        ALL_IDS_NEWEST_FIRST[:3],
        3,
        8,
    )
    middle = _ok(await _call(client, "list_recent_posts", limit=3, offset=3))
    assert (_ids(middle), middle["next_offset"]) == (ALL_IDS_NEWEST_FIRST[3:6], 6)
    last = _ok(await _call(client, "list_recent_posts", limit=3, offset=6))
    assert (_ids(last), last["next_offset"]) == (ALL_IDS_NEWEST_FIRST[6:], None)


async def test_list_recent_posts_filters(client: Client, site: FakeAws) -> None:
    compute = _ok(await _call(client, "list_recent_posts", category="Compute"))
    assert _ids(compute) == ["65-101", "65-103", "65-106"]  # 65-103 via its subcategory
    assert compute["total"] == 3
    by_id = _ok(await _call(client, "list_recent_posts", category="compute/amazon-eks"))
    assert _ids(by_id) == ["65-101", "65-103"]
    by_slug = _ok(await _call(client, "list_recent_posts", category="CUSTOMER-SOLUTIONS"))
    assert _ids(by_slug) == ["65-105"]
    topic_label = _ok(await _call(client, "list_recent_posts", category="데이터베이스"))
    assert _ids(topic_label) == ["65-105"]
    author = _ok(await _call(client, "list_recent_posts", author="jung hoon"))
    assert _ids(author) == ["65-108", "65-105"]
    combined = _ok(await _call(client, "list_recent_posts", category="databases", author="Jung"))
    assert _ids(combined) == ["65-108", "65-105"]


LABEL_NOTICE = (
    "'{0}' is not a blog category; showing posts labelled with it (post tags and AWS "
    "product/topic labels)."
)


async def test_label_filters_explain_themselves(client: Client, site: FakeAws) -> None:
    expected = LABEL_NOTICE.format("데이터베이스") + " Related categories: Databases (databases)."
    listed = _ok(await _call(client, "list_recent_posts", category="데이터베이스"))
    assert (_ids(listed), listed["notice"]) == (["65-105"], expected)
    found = _ok(await _call(client, "search_posts", query="고객", category="데이터베이스"))
    assert (_ids(found), found["notice"]) == (["65-105"], expected)
    nothing = _ok(await _call(client, "search_posts", query="없는단어", category="데이터베이스"))
    assert nothing["notice"] == f"{NO_RESULTS_NOTICE} {expected}"
    # Real categories carry no notice.
    real = _ok(await _call(client, "list_recent_posts", category="Databases"))
    assert real["notice"] is None


async def test_product_abbreviation_filters_by_label(client: Client, fake_aws: FakeAws) -> None:
    eks_label = ("GLOBAL#product", "Amazon Elastic Kubernetes Service (EKS)")
    fake_aws.items = [
        make_item(1, "클러스터 운영", created="2026-01-01T00:00:00Z", global_tags=[eks_label]),
        make_item(2, "다른 글", created="2026-01-02T00:00:00Z", categories=[COMPUTE]),
        make_item(3, "노드 관리", created="2026-01-03T00:00:00Z", categories=[COMPUTE, EKS]),
    ]
    data = _ok(await _call(client, "list_recent_posts", category="EKS"))
    assert _ids(data) == ["65-1"]
    assert data["notice"] == (
        LABEL_NOTICE.format("EKS")
        + " Related categories: Amazon Elastic Kubernetes Service (compute/amazon-eks)."
    )
    search = _ok(await _call(client, "search_posts", query="클러스터", category="eks"))
    assert _ids(search) == ["65-1"]
    assert search["notice"] is not None
    assert search["notice"].startswith(LABEL_NOTICE.format("eks"))
    # Only whole words of a label count.
    partial = await _call(client, "list_recent_posts", category="KS")
    assert _error(partial, "list_recent_posts").startswith("Unknown category 'KS'.")


async def test_list_recent_posts_date_filters_use_korea_time(client: Client, site: FakeAws) -> None:
    march = _ok(await _call(client, "list_recent_posts", published_after="2026-03-01"))
    assert _ids(march) == ["65-101", "65-102", "65-108"]
    february = _ok(
        await _call(
            client,
            "list_recent_posts",
            published_after="2026-02-01",
            published_before="2026-02-28",
        )
    )
    assert _ids(february) == ["65-103", "65-104"]
    single_day = _ok(
        await _call(
            client,
            "list_recent_posts",
            published_after="2026-03-01",
            published_before="2026-03-01",
        )
    )
    assert _ids(single_day) == ["65-108"]
    empty = _ok(await _call(client, "list_recent_posts", published_before="2020-01-01"))
    assert (empty["posts"], empty["total"], empty["next_offset"]) == ([], 0, None)


async def test_list_recent_posts_errors(client: Client, site: FakeAws) -> None:
    result = await _call(
        client,
        "list_recent_posts",
        published_after="2026-01-02",
        published_before="2026-01-01",
    )
    assert _error(result, "list_recent_posts") == (
        "published_after must not be later than published_before."
    )
    result = await _call(client, "list_recent_posts", category="Kirro")
    assert _error(result, "list_recent_posts") == (
        f"Unknown category 'Kirro'. Similar categories: Kiro (artificial-intelligence/kiro). "
        f"{SIMILAR_HINT}"
    )


# --------------------------------------------------------------------------------------------
# list_categories
# --------------------------------------------------------------------------------------------

ALL_CATEGORIES = [
    {"id": "compute", "name": "Compute", "path": "Compute", "post_count": 3},
    {
        "id": "artificial-intelligence",
        "name": "Artificial Intelligence",
        "path": "Artificial Intelligence",
        "post_count": 2,
    },
    {
        "id": "compute/amazon-eks",
        "name": "Amazon Elastic Kubernetes Service",
        "path": "Compute > Amazon Elastic Kubernetes Service",
        "post_count": 2,
    },
    {"id": "databases", "name": "Databases", "path": "Databases", "post_count": 2},
    {"id": "post-types", "name": "Post Types", "path": "Post Types", "post_count": 2},
    {
        "id": "artificial-intelligence/amazon-bedrock",
        "name": "Amazon Bedrock",
        "path": "Artificial Intelligence > Amazon Bedrock",
        "post_count": 1,
    },
    {
        "id": "artificial-intelligence/kiro",
        "name": "Kiro",
        "path": "Artificial Intelligence > Kiro",
        "post_count": 1,
    },
    {
        "id": "learning-levels/advanced-300",
        "name": "Advanced (300)",
        "path": "Advanced (300)",
        "post_count": 1,
    },
    {
        "id": "post-types/customer-solutions",
        "name": "Customer Solutions",
        "path": "Post Types > Customer Solutions",
        "post_count": 1,
    },
    {
        "id": "post-types/technical-how-to",
        "name": "Technical How-to",
        "path": "Post Types > Technical How-to",
        "post_count": 1,
    },
]


async def test_list_categories_most_used_first_without_uncategorized(
    client: Client, site: FakeAws
) -> None:
    data = _ok(await _call(client, "list_categories"))
    assert data == {
        "blog": "AWS 기술 블로그",
        "total": 10,
        "categories": ALL_CATEGORIES,
        "notice": None,
    }
    CategoriesResult.model_validate(data)


async def test_list_categories_limit_keeps_total(client: Client, site: FakeAws) -> None:
    data = _ok(await _call(client, "list_categories", limit=3))
    assert data["total"] == 10
    assert data["categories"] == ALL_CATEGORIES[:3]


async def test_list_categories_parent_filter(client: Client, site: FakeAws) -> None:
    by_name = _ok(await _call(client, "list_categories", parent="Artificial Intelligence"))
    assert [c["id"] for c in by_name["categories"]] == [
        "artificial-intelligence/amazon-bedrock",
        "artificial-intelligence/kiro",
    ]
    assert by_name["total"] == 2
    by_id = _ok(await _call(client, "list_categories", parent="post-types"))
    assert [c["id"] for c in by_id["categories"]] == [
        "post-types/customer-solutions",
        "post-types/technical-how-to",
    ]
    compute = _ok(await _call(client, "list_categories", parent="compute"))
    assert compute["categories"] == [ALL_CATEGORIES[2]]


async def test_list_categories_query(client: Client, site: FakeAws) -> None:
    by_name = _ok(await _call(client, "list_categories", query="bedrock"))
    assert (by_name["total"], by_name["categories"]) == (1, [ALL_CATEGORIES[5]])
    synonym = _ok(await _call(client, "list_categories", query="쿠버네티스"))
    assert synonym["categories"] == [ALL_CATEGORIES[2]]
    by_id = _ok(await _call(client, "list_categories", query="Artificial-Intelligence/"))
    assert [c["id"] for c in by_id["categories"]] == [
        "artificial-intelligence",
        "artificial-intelligence/amazon-bedrock",
        "artificial-intelligence/kiro",
    ]
    nothing = _ok(await _call(client, "list_categories", query="zzz"))
    assert (nothing["total"], nothing["categories"], nothing["notice"]) == (0, [], None)


async def test_list_categories_query_within_parent_excludes_the_parent(
    client: Client, site: FakeAws
) -> None:
    # "compute" matches the parent's own name, but the parent is never listed under itself.
    data = _ok(await _call(client, "list_categories", parent="compute", query="compute"))
    assert (data["total"], data["categories"]) == (1, [ALL_CATEGORIES[2]])
    empty = _ok(await _call(client, "list_categories", parent="compute", query="bedrock"))
    assert empty["total"] == 0


async def test_list_categories_parent_without_subcategories(client: Client, site: FakeAws) -> None:
    data = _ok(await _call(client, "list_categories", parent="amazon-bedrock"))
    assert (data["total"], data["categories"]) == (0, [])


async def test_list_categories_parent_at_second_level(client: Client, fake_aws: FakeAws) -> None:
    # Mirrors the live tree: Artificial Intelligence > Amazon Machine Learning > Amazon Bedrock.
    ml = cat("amazon-machine-learning", "Amazon Machine Learning", "artificial-intelligence")
    bedrock = cat(
        "amazon-bedrock", "Amazon Bedrock", "artificial-intelligence-amazon-machine-learning"
    )
    fake_aws.items = [
        make_item(1, "하나", created="2026-01-01T00:00:00Z", categories=[AI, ml, bedrock]),
        make_item(2, "둘", created="2026-01-02T00:00:00Z", categories=[AI, ml]),
    ]
    top = _ok(await _call(client, "list_categories", parent="artificial-intelligence"))
    assert [c["id"] for c in top["categories"]] == [
        "artificial-intelligence/amazon-machine-learning",
        "artificial-intelligence/amazon-machine-learning/amazon-bedrock",
    ]
    data = _ok(await _call(client, "list_categories", parent="Amazon Machine Learning"))
    assert data["categories"] == [
        {
            "id": "artificial-intelligence/amazon-machine-learning/amazon-bedrock",
            "name": "Amazon Bedrock",
            "path": "Artificial Intelligence > Amazon Machine Learning > Amazon Bedrock",
            "post_count": 1,
        }
    ]
    assert data["total"] == 1


async def test_list_categories_parent_errors(client: Client, site: FakeAws) -> None:
    unknown = await _call(client, "list_categories", parent="Artificial Inteligence")
    assert _error(unknown, "list_categories") == (
        "Unknown category 'Artificial Inteligence'. Similar categories: Artificial Intelligence "
        f"(artificial-intelligence). {SIMILAR_HINT}"
    )
    label = await _call(client, "list_categories", parent="데이터베이스")
    assert _error(label, "list_categories") == (
        "'데이터베이스' is a topic label, not a category with subcategories. "
        "Call list_categories without parent to see categories."
    )


async def test_list_categories_hides_uncategorized_only_blog(
    client: Client, fake_aws: FakeAws
) -> None:
    fake_aws.items = [
        make_item(1, "하나", created="2026-01-01T00:00:00Z"),
        make_item(2, "둘", created="2026-01-02T00:00:00Z"),
    ]
    data = _ok(await _call(client, "list_categories"))
    assert (data["total"], data["categories"]) == (0, [])
    # Hidden from the listing, but still usable as a filter.
    result = await _call(client, "list_recent_posts", category="uncategorized")
    assert _ids(_ok(result)) == ["65-2", "65-1"]
    no_children = await _call(client, "list_categories", parent="uncategorized")
    assert (_ok(no_children)["total"], _ok(no_children)["categories"]) == (0, [])


# --------------------------------------------------------------------------------------------
# get_post
# --------------------------------------------------------------------------------------------


async def test_get_post_by_url(client: Client, site: FakeAws) -> None:
    result = await _call(client, "get_post", url=EKS_URL)
    assert not result.is_error
    assert result.structured_content is None
    assert _text(result) == EKS_EXPECTED
    # A URL is read directly, without loading the post index.
    assert site.directory_requests() == []


@pytest.mark.parametrize(
    "reference",
    [
        "eks-cost-optimization",
        "65-101",
        "blog-posts#65-101",
        "http://aws.amazon.com/ko/blogs/tech/eks-cost-optimization",
        "aws.amazon.com/ko/blogs/tech/eks-cost-optimization/?utm_source=x#intro",
        "  https://www.aws.amazon.com/ko/blogs/tech/eks-cost-optimization/index.html  ",
        "HTTPS://AWS.Amazon.com/ko/blogs/tech//eks-cost-optimization",
    ],
)
async def test_get_post_reference_forms(client: Client, site: FakeAws, reference: str) -> None:
    result = await _call(client, "get_post", url=reference)
    assert _text(result) == EKS_EXPECTED
    assert [str(r.url) for r in site.requests if r.url.path != "/api/dirs/items/search"] == [
        EKS_URL
    ]


async def test_get_post_unknown_post_id(client: Client, site: FakeAws) -> None:
    result = await _call(client, "get_post", url="65-99999")
    assert _error(result, "get_post") == (
        "No post with id '65-99999'. Use search_posts to find posts and their ids."
    )
    assert len(site.directory_requests()) == 1
    assert [r for r in site.requests if r.url.path != "/api/dirs/items/search"] == []


async def test_get_post_slug_not_in_index_is_still_fetched(client: Client, site: FakeAws) -> None:
    text = _text(await _call(client, "get_post", url="not-in-index"))
    header, chunk, next_start = _split_post(text)
    assert header.startswith(f"# 색인에 없는 글\n\n- URL: {UNINDEXED_URL}\n")
    assert chunk == "아직 목록에 없는 글입니다."
    assert next_start is None
    missing = await _call(client, "get_post", url="missing-slug")
    assert _error(missing, "get_post") == (
        f"No post found at {BASE}missing-slug/. Check the URL, or find the post with search_posts."
    )


async def test_get_post_pagination_and_continuation(client: Client, site: FakeAws) -> None:
    full_text = _text(await _call(client, "get_post", url=LONG_URL, max_length=50000))
    full_header, full_body, no_more = _split_post(full_text)
    assert no_more is None
    _, end, total = _content_range(full_header)
    assert end == total == len(full_body) + 1  # the body's final newline is stripped

    chunks: list[str] = []
    start: int | None = 0
    calls = 0
    while start is not None:
        calls += 1
        text = _text(
            await _call(client, "get_post", url="65-103", start_index=start, max_length=1000)
        )
        header, chunk, next_start = _split_post(text)
        chunk_start, chunk_end, chunk_total = _content_range(header)
        assert (chunk_start, chunk_total) == (start, total)
        assert chunk_end - chunk_start <= 1000
        if start == 0:
            assert header.startswith("# 쿠버네티스 클러스터 업그레이드 전략\n\n- URL: ")
            assert "- Authors: Test Author" in header
            assert CONTENT_NOTE in header
        else:
            assert header == (
                f"# 쿠버네티스 클러스터 업그레이드 전략 (continued)\n\n- URL: {LONG_URL}\n"
                f"- Content: characters {chunk_start}-{chunk_end} of {total}\n\n"
                f"> {CONTINUED_NOTE}"
            )
        if next_start is not None:
            assert next_start == chunk_end
            remain = re.search(r"\[Truncated: (\d+) characters remain", text)
            assert remain is not None
            assert int(remain.group(1)) == total - next_start
            assert not chunk.rstrip().endswith(("섹션 0", "섹션 1", "섹션 2", "섹션 3"))
        chunks.append(chunk)
        start = next_start
    assert calls == 4
    assert "\n\n".join(chunks) == full_body
    # The page is fetched once; continuation calls are served from the cache.
    assert [str(r.url) for r in site.requests].count(LONG_URL) == 1


async def test_get_post_start_index_past_end(client: Client, site: FakeAws) -> None:
    result = await _call(client, "get_post", url=EKS_URL, start_index=205)
    assert _error(result, "get_post") == (
        "start_index 205 is past the end of the post (205 characters). "
        "Use start_index=0 to read from the beginning."
    )


async def test_get_post_image_modes(client: Client, site: FakeAws) -> None:
    markdown_line = f"![아키텍처 다이어그램]({IMG})"
    default = _text(await _call(client, "get_post", url=EKS_URL))
    assert markdown_line in default
    explicit = _text(await _call(client, "get_post", url=EKS_URL, images="markdown"))
    assert explicit == default

    alt = _text(await _call(client, "get_post", url=EKS_URL, images="alt"))
    assert "\n\n[Image: 아키텍처 다이어그램]\n\n" in alt
    assert IMG not in alt
    assert "- Content: characters 0-158 of 158" in alt

    none = _text(await _call(client, "get_post", url=EKS_URL, images="none"))
    assert "아키텍처 다이어그램" not in none
    assert IMG not in none
    assert "이 글은 **합성** 테스트 본문입니다.\n\n```python\n" in none
    assert "- Content: characters 0-137 of 137" in none
    # One fetch serves every image mode.
    assert [str(r.url) for r in site.requests] == [EKS_URL]


@pytest.mark.parametrize("images", ["alt", "none"])
async def test_get_post_truncation_hint_keeps_the_image_mode(
    client: Client, site: FakeAws, images: str
) -> None:
    text = _text(await _call(client, "get_post", url=LONG_URL, max_length=1000, images=images))
    header, _, next_start = _split_post(text)
    assert next_start is not None
    assert f"> {CONTENT_NOTE}" in header
    assert text.endswith(
        f"\n\n{END}\n[Truncated: {_content_range(header)[2] - next_start} characters remain. "
        f'Call get_post with start_index={next_start} and images="{images}" to continue.]'
    )


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        (
            BASE + "category/compute/",
            f"'{BASE}category/compute/' is a listing page, not a post. Use search_posts or "
            "list_recent_posts to find post URLs.",
        ),
        (
            BASE,
            f"'{BASE}' is a listing page, not a post. Use search_posts or list_recent_posts to "
            "find post URLs.",
        ),
        (
            BASE + "page/2/",
            f"'{BASE}page/2/' is a listing page, not a post. Use search_posts or "
            "list_recent_posts to find post URLs.",
        ),
        (
            "https://example.com/ko/blogs/tech/foo/",
            "'https://example.com/ko/blogs/tech/foo/' is not a URL on https://aws.amazon.com. "
            f"Supported blogs: {BASE}",
        ),
        (
            "https://aws.amazon.com/blogs/aws/foo/",
            "'https://aws.amazon.com/blogs/aws/foo/' is not in a supported blog. "
            f"Supported blogs: {BASE}",
        ),
        (
            "https://aws.amazon.com:8443/ko/blogs/tech/foo/",
            "'https://aws.amazon.com:8443/ko/blogs/tech/foo/' is not a plain "
            "https://aws.amazon.com URL.",
        ),
        ("not a post", "'not a post' is not a post URL, slug or id."),
        ("   ", "Pass a post URL, slug or id, for example one from search_posts."),
    ],
)
async def test_get_post_rejects_bad_references_without_fetching(
    client: Client, site: FakeAws, reference: str, message: str
) -> None:
    assert _error(await _call(client, "get_post", url=reference), "get_post") == message
    assert site.requests == []


async def test_get_post_missing_page(client: Client, site: FakeAws) -> None:
    url = BASE + "missing-post/"
    result = await _call(client, "get_post", url=url)
    assert _error(result, "get_post") == (
        f"No post found at {url}. Check the URL, or find the post with search_posts."
    )


async def test_get_post_upstream_status_error(client: Client, site: FakeAws) -> None:
    site.page_status[EKS_URL] = 403
    result = await _call(client, "get_post", url=EKS_URL)
    assert _error(result, "get_post") == f"aws.amazon.com answered HTTP 403 for {EKS_URL}."


async def test_get_post_not_a_single_post_page(client: Client, site: FakeAws) -> None:
    url = BASE + "looks-like-a-post/"
    site.pages[url] = post_page(url=url, title="목록", body="<p>x</p>", single=False)
    result = await _call(client, "get_post", url=url)
    assert _error(result, "get_post") == (
        f"{url} is not a single blog post page. Pass the URL of an individual post "
        "(for example one returned by search_posts)."
    )


async def test_get_post_follows_redirect_within_blog(client: Client, site: FakeAws) -> None:
    site.redirects[BASE + "old-slug/"] = EKS_URL
    assert _text(await _call(client, "get_post", url="old-slug")) == EKS_EXPECTED


async def test_get_post_redirect_out_of_blog(client: Client, site: FakeAws) -> None:
    other = "https://aws.amazon.com/blogs/aws/moved/"
    site.redirects[BASE + "moved/"] = other
    site.pages[other] = post_page(url=other, title="Elsewhere", body="<p>x</p>")
    result = await _call(client, "get_post", url=BASE + "moved/")
    assert _error(result, "get_post") == (
        f"{BASE}moved/ redirected to {other}, which is outside the supported blogs."
    )


async def test_get_post_redirect_to_foreign_host(client: Client, site: FakeAws) -> None:
    site.redirects[BASE + "phish/"] = "https://evil.example/ko/blogs/tech/phish/"
    result = await _call(client, "get_post", url=BASE + "phish/")
    assert _error(result, "get_post") == (
        "Refusing to fetch https://evil.example/ko/blogs/tech/phish/: "
        "only https://aws.amazon.com is allowed."
    )
    assert [r.url.host for r in site.requests] == ["aws.amazon.com"]


# --------------------------------------------------------------------------------------------
# Index loading, refresh and stale data
# --------------------------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now


LOADED_AT = datetime(2026, 3, 11, 3, 4, tzinfo=UTC)  # 12:04 in Korea
STALE_NOTICE = (
    "Refreshing the post list failed; showing the list loaded at 2026-03-11 12:04 (UTC+9). "
    "Very recent posts may be missing."
)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
        return cls.fromtimestamp(LOADED_AT.timestamp(), tz)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Drive the index clock and freeze its ``loaded_at`` timestamps.

    ``BlogIndex`` binds ``time.monotonic`` as a default argument, so the clock is injected by
    wrapping the class the server instantiates.
    """
    fake_clock = _Clock()
    monkeypatch.setattr(server_module, "BlogIndex", functools.partial(BlogIndex, clock=fake_clock))
    monkeypatch.setattr(index_module, "datetime", _FrozenDatetime)
    return fake_clock


def _reject_directory(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, json=REJECTED)


@pytest.fixture(params=["auto", "legacy"])
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


async def test_stale_notice_when_refresh_fails(mode: str, site: FakeAws, clock: _Clock) -> None:
    server = create_server(transport=site.transport(), request_interval=0)
    async with Client(server, raise_exceptions=True, mode=mode) as client:
        fresh = _ok(await _call(client, "list_recent_posts", limit=1))
        assert fresh["notice"] is None
        assert len(site.directory_requests()) == 1

        clock.now += 601  # past the 10 minute refresh interval
        site.directory_override = _reject_directory
        stale = _ok(await _call(client, "list_recent_posts", limit=1))
        assert stale["notice"] == STALE_NOTICE
        assert _ids(stale) == ["65-101"]  # the previous snapshot is still served
        assert len(site.directory_requests()) == 2

        # Within the retry interval the stale snapshot is served without new requests.
        clock.now += 30
        search = _ok(await _call(client, "search_posts", query="Kiro 오로라"))
        assert search["notice"] == f"{ANY_TERM_NOTICE} {STALE_NOTICE}"
        hit = _ok(await _call(client, "search_posts", query="EKS"))
        assert hit["notice"] == STALE_NOTICE
        categories = _ok(await _call(client, "list_categories", limit=1))
        assert categories["notice"] == STALE_NOTICE
        text = _text(await _call(client, "get_post", url="65-101"))
        assert text == EKS_EXPECTED
        assert len(site.directory_requests()) == 2

        # After the retry interval a successful refresh clears the flag.
        clock.now += 31
        site.directory_override = None
        recovered = _ok(await _call(client, "list_recent_posts", limit=1))
        assert recovered["notice"] is None
        assert len(site.directory_requests()) == 3


async def test_new_posts_appear_after_refresh_interval(
    mode: str, site: FakeAws, clock: _Clock
) -> None:
    server = create_server(transport=site.transport(), request_interval=0)
    async with Client(server, raise_exceptions=True, mode=mode) as client:
        assert _ids(_ok(await _call(client, "list_recent_posts", limit=1))) == ["65-101"]
        site.items.append(make_item(109, "Amazon Nova 소개", created="2026-03-11T01:00:00Z"))

        clock.now += 599
        assert _ids(_ok(await _call(client, "list_recent_posts", limit=1))) == ["65-101"]
        assert len(site.directory_requests()) == 1

        clock.now += 2
        data = _ok(await _call(client, "list_recent_posts", limit=2))
        assert _ids(data) == ["65-109", "65-101"]
        assert data["total"] == 9
        assert data["notice"] is None
        assert len(site.directory_requests()) == 2
        assert _ids(_ok(await _call(client, "search_posts", query="노바"))) == ["65-109"]


async def test_first_load_failure_is_a_tool_error(mode: str, site: FakeAws, clock: _Clock) -> None:
    server = create_server(transport=site.transport(), request_interval=0)
    async with Client(server, raise_exceptions=True, mode=mode) as client:
        site.directory_override = _reject_directory
        expected = "The AWS directory API rejected the query; try again later."
        for tool, args in [
            ("search_posts", {"query": "eks"}),
            ("list_recent_posts", {}),
            ("list_categories", {}),
            ("get_post", {"url": "65-101"}),
        ]:
            assert _error(await client.call_tool(tool, args), tool) == expected
        # The failure is remembered for a short cool-down: only the first call reached the API.
        assert len(site.directory_requests()) == 1
        # A slug or URL can still be read without the index.
        assert _text(await _call(client, "get_post", url="eks-cost-optimization")) == EKS_EXPECTED

        site.directory_override = None
        clock.now += 14.9
        assert _error(await _call(client, "list_recent_posts"), "list_recent_posts") == expected
        assert len(site.directory_requests()) == 1
        # After the 15 s cool-down the load is retried and succeeds once the API recovers.
        clock.now += 0.1
        assert _ok(await _call(client, "list_recent_posts"))["total"] == 8
        assert len(site.directory_requests()) == 2


async def test_directory_returning_no_posts_is_a_tool_error(
    client: Client, fake_aws: FakeAws
) -> None:
    fake_aws.items = [
        make_item(1, "Other blog", prefix="66", base="https://aws.amazon.com/blogs/x/")
    ]
    result = await _call(client, "list_recent_posts")
    assert _error(result, "list_recent_posts") == (
        f"The AWS directory API returned no posts for {BASE}. "
        "The API may have changed; try again later."
    )


async def test_concurrent_first_calls_load_the_index_once(client: Client, site: FakeAws) -> None:
    results: dict[str, CallToolResult] = {}

    async def run(tool: str, args: dict[str, Any]) -> None:
        results[tool] = await client.call_tool(tool, args)

    async with anyio.create_task_group() as tg:
        tg.start_soon(run, "search_posts", {"query": "EKS"})
        tg.start_soon(run, "list_recent_posts", {})
        tg.start_soon(run, "list_categories", {})
        tg.start_soon(run, "get_post", {"url": "65-101"})
    assert _ok(results["search_posts"])["total"] == 2
    assert _ok(results["list_recent_posts"])["total"] == 8
    assert _ok(results["list_categories"])["total"] == 10
    assert _text(results["get_post"]) == EKS_EXPECTED
    assert len(site.directory_requests()) == 1
    assert {r.headers["user-agent"] for r in site.requests} == {user_agent()}


# --------------------------------------------------------------------------------------------
# Entry point and configuration
# --------------------------------------------------------------------------------------------


def test_package_is_marked_as_typed() -> None:
    assert importlib.resources.files("aws_blog_mcp").joinpath("py.typed").is_file()


def test_main_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out == f"aws-blog-mcp {__version__}\n"


def test_main_help_mentions_environment(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: aws-blog-mcp [-h] [--version]\n")
    assert LOG_LEVEL_ENV in out
    assert "DEBUG|INFO|WARNING|ERROR|CRITICAL" in out
    assert "AWS_BLOG_MCP_USER_AGENT" in out


def test_main_rejects_unknown_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--bogus"])
    assert exc_info.value.code == 2
    assert "unrecognized arguments: --bogus" in capsys.readouterr().err


def test_main_runs_the_server_over_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[str] = []

    class _Stub:
        def run(self) -> None:
            runs.append("run")

    monkeypatch.setattr(server_module, "create_server", _Stub)
    main([])
    assert runs == ["run"]


def test_python_dash_m_help() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "aws_blog_mcp", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("usage: aws-blog-mcp ")
    assert "--version" in proc.stdout


def test_python_dash_m_version() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "aws_blog_mcp", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == f"aws-blog-mcp {__version__}\n"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "WARNING"),
        ("debug", "DEBUG"),
        (" Info ", "INFO"),
        ("ERROR", "ERROR"),
        ("critical", "CRITICAL"),
        ("verbose", "WARNING"),
        ("", "WARNING"),
    ],
)
def test_log_level_from_environment(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: str
) -> None:
    if value is None:
        monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    else:
        monkeypatch.setenv(LOG_LEVEL_ENV, value)
    assert create_server().settings.log_level == expected


async def test_list_categories_parent_that_never_appears_lists_its_children(
    client: Client, fake_aws: FakeAws
) -> None:
    fake_aws.items = [
        make_item(
            1, "Level post", categories=[cat("advanced-300", "Advanced (300)", "learning-levels")]
        )
    ]
    data = _ok(await _call(client, "list_categories", parent="learning-levels"))
    assert [c["id"] for c in data["categories"]] == ["learning-levels/advanced-300"]
