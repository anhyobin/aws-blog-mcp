"""Opt-in checks against the real aws.amazon.com (``pytest --run-live``).

They guard the assumptions the server makes about undocumented endpoints and the page template.
Request volume is kept tiny: one raw directory query, one index load shared by the MCP tests,
and a handful of post pages.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent

from aws_blog_mcp.blogs import KO_TECH
from aws_blog_mcp.directory import API_URL, MAX_RESPONSE_BYTES, SORT_CREATED
from aws_blog_mcp.http import Fetcher, create_http_client
from aws_blog_mcp.server import create_server

pytestmark = [pytest.mark.anyio, pytest.mark.live]

CODE_HEAVY_QUERY = "CDK"
MAX_CODE_POSTS = 3


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
async def live_client(anyio_backend: str) -> AsyncIterator[Client]:
    """One server for the module, so the post index is loaded only once."""
    async with Client(create_server(), raise_exceptions=True) as client:
        yield client


def _text(result: CallToolResult) -> str:
    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def _ok(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, _text(result)
    assert result.structured_content is not None
    return result.structured_content


def _body(text: str) -> str:
    """Return the post content between get_post's begin/end markers."""
    _, sep, rest = text.partition("\n--- BEGIN POST CONTENT ---\n")
    assert sep, text[:500]
    body, sep, _ = rest.rpartition("\n\n--- END POST CONTENT ---")
    assert sep, text[-500:]
    return body


async def _read(client: Client, url: str) -> str:
    result = await client.call_tool("get_post", {"url": url, "max_length": 50000, "images": "none"})
    assert not result.is_error, _text(result)
    return _text(result)


async def test_directory_blog_filter_trick() -> None:
    async with create_http_client() as http:
        response = await Fetcher(http).get(
            API_URL,
            params={
                "item.directoryId": "blog-posts",
                "item.locale": KO_TECH.locale,
                "sort_by": SORT_CREATED,
                "sort_order": "desc",
                "size": 2000,
                "page": 0,
                "q": KO_TECH.id_prefix,
                "q_operator": "AND",
            },
            max_bytes=MAX_RESPONSE_BYTES,
        )
    data = response.json()
    assert data["fieldTypes"], "an empty fieldTypes object means the query was rejected"
    items = data["items"]
    own = [it for it in items if KO_TECH.owns_item_id(it["item"]["id"])]
    assert len(own) >= 900
    assert len(own) / len(items) >= 0.99
    links = [it["item"]["additionalFields"]["link"] for it in own]
    assert sum(KO_TECH.owns_url(link) for link in links) / len(links) >= 0.99
    assert data["metadata"]["totalHits"] >= len(items)


@pytest.mark.parametrize("query", ["쿠버네티스", "Bedrock"])
async def test_search_finds_posts(live_client: Client, query: str) -> None:
    data = _ok(await live_client.call_tool("search_posts", {"query": query, "limit": 5}))
    assert data["match"] == "all_terms"
    assert data["total"] >= 5
    assert len(data["posts"]) == 5
    for post in data["posts"]:
        assert post["url"].startswith(KO_TECH.base_url)
        assert re.fullmatch(r"65-\d+", post["id"])


async def test_get_newest_and_oldest_post(live_client: Client) -> None:
    newest = _ok(await live_client.call_tool("list_recent_posts", {"limit": 1}))
    total = newest["total"]
    assert total >= 900
    oldest = _ok(
        await live_client.call_tool("list_recent_posts", {"limit": 1, "offset": total - 1})
    )
    assert oldest["next_offset"] is None
    assert oldest["posts"][0]["published"].startswith("2022-")

    for post in (newest["posts"][0], oldest["posts"][0]):
        text = await _read(live_client, post["url"])
        title_line, _, rest = text.partition("\n\n")
        # The page <h1> and the directory title may differ in inline spacing only.
        assert "".join(title_line.split()) == "".join(f"# {post['title']}".split()), title_line
        assert rest.startswith(f"- URL: {post['url']}\n"), text[:300]
        assert f"\n- Published: {post['published']}" in text
        assert len(_body(text).strip()) >= 200


async def test_code_blocks_are_fenced(live_client: Client) -> None:
    found = _ok(
        await live_client.call_tool(
            "search_posts", {"query": CODE_HEAVY_QUERY, "limit": MAX_CODE_POSTS}
        )
    )
    assert found["posts"], f"no posts found for {CODE_HEAVY_QUERY!r}"
    fenced: list[str] = []
    for post in found["posts"]:
        body = _body(await _read(live_client, post["url"]))
        assert body.strip()
        if re.search(r"^```", body, re.M):
            fenced.append(post["url"])
            break
    assert fenced, f"no code fence in the top {MAX_CODE_POSTS} {CODE_HEAVY_QUERY!r} posts"
