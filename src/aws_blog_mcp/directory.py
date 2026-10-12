"""Client for the AWS directory API that backs the aws.amazon.com blog listings.

The endpoint is undocumented, so this module is deliberately defensive:

* a 200 response with an empty ``fieldTypes`` object is how the API reports a rejected query;
* ``size`` is capped at 2000 and ``(page + 1) * size`` at 10000, so large result sets are read
  from both ends (``desc`` then ``asc``) and de-duplicated;
* there is no blog filter. Item ids are tokenised by the search engine, so ``q=<blog number>``
  narrows results to (almost exactly) one blog; every result is still filtered by id prefix, and a
  full locale scan is the fallback if that trick ever stops working.
"""

from __future__ import annotations

import logging
from typing import Any

from aws_blog_mcp.blogs import BlogConfig
from aws_blog_mcp.errors import UpstreamError
from aws_blog_mcp.http import Fetcher

logger = logging.getLogger(__name__)

API_URL = "https://aws.amazon.com/api/dirs/items/search"
MAX_PAGE_SIZE = 2000
MAX_WINDOW = 10_000
MAX_RESPONSE_BYTES = 64_000_000
SORT_CREATED = "item.additionalFields.createdDate"
SORT_MODIFIED = "item.additionalFields.modifiedDate"
FALLBACK_RATIO = 0.5
"""Fall back to a full locale scan if the id-prefix query keeps less than this share of the
posts the previous load had."""

Item = dict[str, Any]


class DirectoryClient:
    """Read blog post metadata from the directory API."""

    def __init__(self, fetcher: Fetcher, *, page_size: int = MAX_PAGE_SIZE) -> None:
        self._fetcher = fetcher
        self._page_size = min(page_size, MAX_PAGE_SIZE)

    async def query(
        self,
        *,
        locale: str,
        q: str | None = None,
        sort_by: str = SORT_CREATED,
        sort_order: str = "desc",
        size: int,
        page: int = 0,
    ) -> tuple[list[Item], int | None]:
        """Run one query and return ``(items, total_hits)``; the total is ``None`` if absent."""
        params: dict[str, str | int] = {
            "item.directoryId": "blog-posts",
            "item.locale": locale,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "size": size,
            "page": page,
        }
        if q:
            params["q"] = q
            params["q_operator"] = "AND"
        response = await self._fetcher.get(API_URL, params=params, max_bytes=MAX_RESPONSE_BYTES)
        return _parse_page(response.json())

    async def fetch_blog(self, blog: BlogConfig, *, previous_count: int = 0) -> list[Item]:
        """Return every post of ``blog``, newest first."""
        own = _own_items(blog, await self._collect(blog.locale, q=blog.id_prefix))
        if not own or len(own) < previous_count * FALLBACK_RATIO:
            logger.warning(
                "Directory query q=%s kept %d posts (previous load: %d); scanning all of %s",
                blog.id_prefix,
                len(own),
                previous_count,
                blog.locale,
            )
            own = _own_items(blog, await self._collect(blog.locale, q=None))
        if not own:
            raise UpstreamError(
                f"The AWS directory API returned no posts for {blog.base_url}. "
                "The API may have changed; try again later."
            )
        return own

    async def fetch_recently_modified(self, blog: BlogConfig, *, size: int = 50) -> list[Item]:
        """Return the most recently modified posts of ``blog`` (for incremental refresh)."""
        items, _ = await self.query(
            locale=blog.locale, q=blog.id_prefix, sort_by=SORT_MODIFIED, size=size
        )
        return _own_items(blog, items)

    async def _collect(self, locale: str, *, q: str | None) -> list[Item]:
        size = self._page_size
        collected: dict[str, Item] = {}
        total: int | None = None
        exhausted = False
        for order in ("desc", "asc"):
            page = 0
            while (page + 1) * size <= MAX_WINDOW:
                items, total = await self.query(
                    locale=locale, q=q, sort_order=order, size=size, page=page
                )
                for item in items:
                    collected.setdefault(item["item"]["id"], item)
                if len(items) < size:
                    exhausted = True  # a short page is the end of the result set
                    break
                if total is not None and len(collected) >= total:
                    break
                page += 1
            if exhausted or (total is not None and len(collected) >= total):
                break
        if total is not None and len(collected) < total:
            logger.warning("Directory listing incomplete: %d of %d items", len(collected), total)
        return sorted(collected.values(), key=_created, reverse=True)


def _created(item: Item) -> str:
    value = item["item"]["additionalFields"].get("createdDate")
    return value if isinstance(value, str) else ""


def _parse_page(data: Any) -> tuple[list[Item], int | None]:
    if not isinstance(data, dict):
        raise UpstreamError("The AWS directory API returned an unexpected response.")
    if not data.get("fieldTypes"):
        # A rejected query still returns HTTP 200, with an empty fieldTypes object.
        raise UpstreamError("The AWS directory API rejected the query; try again later.")
    items = data.get("items")
    metadata = data.get("metadata")
    if not isinstance(items, list) or not isinstance(metadata, dict):
        raise UpstreamError("The AWS directory API returned an unexpected response.")
    total = metadata.get("totalHits")
    valid = [it for it in items if _is_valid_item(it)]
    if len(valid) != len(items):
        logger.warning("Skipped %d malformed directory items", len(items) - len(valid))
    return valid, total if isinstance(total, int) and not isinstance(total, bool) else None


def _is_valid_item(it: Any) -> bool:
    if not isinstance(it, dict):
        return False
    item = it.get("item")
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return False
    fields = item.get("additionalFields")
    tags = it.get("tags", [])
    return (
        isinstance(fields, dict)
        and isinstance(fields.get("link"), str)
        and isinstance(fields.get("title"), str)
        and isinstance(fields.get("createdDate"), str)
        and (tags is None or isinstance(tags, list))
    )


def _own_items(blog: BlogConfig, items: list[Item]) -> list[Item]:
    return [
        it
        for it in items
        if blog.owns_item_id(it["item"]["id"])
        and blog.owns_url(it["item"]["additionalFields"]["link"])
    ]
