"""Tests for :mod:`aws_blog_mcp.directory` against the FakeAws directory emulation."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest

from aws_blog_mcp import directory
from aws_blog_mcp.blogs import KO_TECH
from aws_blog_mcp.directory import (
    API_URL,
    MAX_PAGE_SIZE,
    SORT_CREATED,
    SORT_MODIFIED,
    DirectoryClient,
)
from aws_blog_mcp.errors import UpstreamError
from aws_blog_mcp.http import Fetcher, create_http_client
from tests.fakes import API_PATH, FIELD_TYPES, FakeAws, make_item

pytestmark = pytest.mark.anyio

OTHER_BLOG = "https://aws.amazon.com/blogs/aws/"
START = datetime(2025, 1, 1, tzinfo=UTC)


def day(n: int) -> str:
    """ISO timestamp ``n`` days after START (distinct, sortable created/modified dates)."""
    return (START + timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def own(n: int, **kwargs: Any) -> dict[str, Any]:
    """A ko-tech post created on day ``n``."""
    return make_item(n, f"Synthetic post {n}", created=day(n), **kwargs)


def ids(items: list[dict[str, Any]]) -> list[str]:
    return [it["item"]["id"] for it in items]


def numbers(items: list[dict[str, Any]]) -> list[int]:
    return [int(i.rsplit("-", 1)[1]) for i in ids(items)]


def params(request: httpx2.Request) -> dict[str, str]:
    return dict(request.url.params.items())


def pages(fake: FakeAws) -> list[tuple[str | None, str, str, str]]:
    """``(q, sort_order, page, size)`` of every directory request, in order."""
    out: list[tuple[str | None, str, str, str]] = []
    for r in fake.directory_requests():
        p = params(r)
        out.append((p.get("q"), p["sort_order"], p["page"], p["size"]))
    return out


def payload(items: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    return {
        "items": items,
        "metadata": {"count": len(items), "totalHits": len(items) if total is None else total},
        "fieldTypes": FIELD_TYPES,
    }


@asynccontextmanager
async def client_for(
    fake: FakeAws, *, page_size: int = MAX_PAGE_SIZE
) -> AsyncIterator[DirectoryClient]:
    async with create_http_client(fake.transport()) as http:
        yield DirectoryClient(Fetcher(http, min_interval=0, max_attempts=1), page_size=page_size)


def window_limit(fake: FakeAws, window: int) -> None:
    """Make the fake reject pages beyond a (small) result window, like the real 10000 cap."""

    def override(request: httpx2.Request) -> httpx2.Response | None:
        p = params(request)
        if (int(p["page"]) + 1) * int(p["size"]) > window:
            return httpx2.Response(
                200, json={"items": [], "metadata": {"totalHits": 0}, "fieldTypes": {}}
            )
        return None

    fake.directory_override = override


# --------------------------------------------------------------------------------------------
# query()
# --------------------------------------------------------------------------------------------


async def test_query_sends_all_params_with_q(fake_aws: FakeAws) -> None:
    fake_aws.items = [own(1), own(2)]
    async with client_for(fake_aws) as dc:
        items, total = await dc.query(locale="ko_KR", q="65", size=10)
    (request,) = fake_aws.requests
    assert str(request.url).startswith(API_URL + "?")
    assert request.url.path == API_PATH
    assert params(request) == {
        "item.directoryId": "blog-posts",
        "item.locale": "ko_KR",
        "sort_by": SORT_CREATED,
        "sort_order": "desc",
        "size": "10",
        "page": "0",
        "q": "65",
        "q_operator": "AND",
    }
    assert numbers(items) == [2, 1]
    assert total == 2


@pytest.mark.parametrize("q", [None, ""])
async def test_query_without_q_omits_q_operator(fake_aws: FakeAws, q: str | None) -> None:
    fake_aws.items = [own(1)]
    async with client_for(fake_aws) as dc:
        await dc.query(locale="ko_KR", q=q, sort_by=SORT_MODIFIED, sort_order="asc", size=5, page=3)
    (request,) = fake_aws.requests
    assert params(request) == {
        "item.directoryId": "blog-posts",
        "item.locale": "ko_KR",
        "sort_by": SORT_MODIFIED,
        "sort_order": "asc",
        "size": "5",
        "page": "3",
    }


@pytest.mark.parametrize(
    "body",
    [
        {"items": [], "metadata": {"count": 0, "totalHits": 0}, "fieldTypes": {}},
        {"items": [], "metadata": {"count": 0, "totalHits": 0}},
        {"items": [], "metadata": {"count": 0, "totalHits": 0}, "fieldTypes": None},
    ],
)
async def test_empty_field_types_means_rejected(fake_aws: FakeAws, body: dict[str, Any]) -> None:
    fake_aws.directory_override = lambda request: httpx2.Response(200, json=body)
    async with client_for(fake_aws) as dc:
        with pytest.raises(UpstreamError) as info:
            await dc.query(locale="ko_KR", size=10)
    assert str(info.value) == "The AWS directory API rejected the query; try again later."


async def test_fake_rejection_of_oversized_window_surfaces(fake_aws: FakeAws) -> None:
    fake_aws.items = [own(1)]
    async with client_for(fake_aws) as dc:
        with pytest.raises(UpstreamError, match="rejected the query"):
            await dc.query(locale="ko_KR", size=2000, page=5)


@pytest.mark.parametrize(
    "body",
    [
        [],
        "items",
        None,
        42,
        {"fieldTypes": FIELD_TYPES, "items": None, "metadata": {"totalHits": 0}},
        {"fieldTypes": FIELD_TYPES, "items": {}, "metadata": {"totalHits": 0}},
        {"fieldTypes": FIELD_TYPES, "items": []},
        {"fieldTypes": FIELD_TYPES, "items": [], "metadata": []},
        {"fieldTypes": FIELD_TYPES, "metadata": {"totalHits": 0}},
    ],
)
async def test_malformed_payload(fake_aws: FakeAws, body: Any) -> None:
    raw = json.dumps(body).encode()
    fake_aws.directory_override = lambda request: httpx2.Response(200, content=raw)
    async with client_for(fake_aws) as dc:
        with pytest.raises(UpstreamError) as info:
            await dc.query(locale="ko_KR", size=10)
    assert str(info.value) == "The AWS directory API returned an unexpected response."


async def test_invalid_json_body(fake_aws: FakeAws) -> None:
    fake_aws.directory_override = lambda request: httpx2.Response(200, content=b"<html>oops")
    async with client_for(fake_aws) as dc:
        with pytest.raises(UpstreamError, match="returned invalid JSON"):
            await dc.query(locale="ko_KR", size=10)


async def test_http_error_from_directory(fake_aws: FakeAws) -> None:
    fake_aws.directory_override = lambda request: httpx2.Response(500)
    async with client_for(fake_aws) as dc:
        with pytest.raises(UpstreamError, match="HTTP 500"):
            await dc.query(locale="ko_KR", size=10)


@pytest.mark.parametrize(
    "metadata", [{}, {"totalHits": "12"}, {"totalHits": None}, {"totalHits": True}]
)
async def test_missing_or_bad_total_hits_is_unknown(
    fake_aws: FakeAws, metadata: dict[str, Any]
) -> None:
    body = {"items": [own(1), own(2)], "metadata": metadata, "fieldTypes": FIELD_TYPES}
    fake_aws.directory_override = lambda request: httpx2.Response(200, json=body)
    async with client_for(fake_aws) as dc:
        items, total = await dc.query(locale="ko_KR", size=10)
    assert numbers(items) == [1, 2]
    assert total is None


async def test_unknown_total_keeps_paging_until_a_short_page(fake_aws: FakeAws) -> None:
    """Without totalHits, paging must continue until a page comes back short."""
    responses = [[own(1), own(2)], [own(3), own(4)], [own(5)]]

    def override(request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params["page"])
        items = responses[page] if page < len(responses) else []
        return httpx2.Response(
            200, json={"items": items, "metadata": {}, "fieldTypes": FIELD_TYPES}
        )

    fake_aws.directory_override = override
    async with client_for(fake_aws, page_size=2) as dc:
        items = await dc.fetch_blog(KO_TECH)
    assert sorted(numbers(items)) == [1, 2, 3, 4, 5]
    # A short page ends the result set: no second pass in ascending order.
    assert pages(fake_aws) == [("65", "desc", str(n), "2") for n in range(3)]


async def test_unknown_total_stops_at_an_empty_page(fake_aws: FakeAws) -> None:
    full_pages = [[own(4), own(3)], [own(2), own(1)]]

    def override(request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params["page"])
        items = full_pages[page] if page < len(full_pages) else []
        return httpx2.Response(
            200, json={"items": items, "metadata": {"count": 2}, "fieldTypes": FIELD_TYPES}
        )

    fake_aws.directory_override = override
    async with client_for(fake_aws, page_size=2) as dc:
        items = await dc.fetch_blog(KO_TECH)
    assert numbers(items) == [4, 3, 2, 1]
    assert pages(fake_aws) == [("65", "desc", str(n), "2") for n in range(3)]


async def test_unknown_total_with_a_full_window_reads_from_both_ends(
    fake_aws: FakeAws, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without totalHits a full window cannot be told apart from the end; the asc pass runs."""
    monkeypatch.setattr(directory, "MAX_WINDOW", 4)
    fake_aws.items = [own(n) for n in range(1, 7)]

    def drop_total(request: httpx2.Request) -> httpx2.Response:
        body = fake_aws._search(request)
        body["metadata"] = {}
        return httpx2.Response(200, json=body)

    fake_aws.directory_override = drop_total
    async with client_for(fake_aws, page_size=2) as dc:
        items = await dc.fetch_blog(KO_TECH)
    assert numbers(items) == [6, 5, 4, 3, 2, 1]
    assert [(order, page) for _, order, page, _ in pages(fake_aws)] == [
        ("desc", "0"),
        ("desc", "1"),
        ("asc", "0"),
        ("asc", "1"),
    ]


async def test_malformed_items_are_skipped(
    fake_aws: FakeAws, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(mutate: Any) -> dict[str, Any]:
        item = own(99)
        mutate(item)
        return item

    good_a, good_b = own(1), own(2)
    bad: list[Any] = [
        "not a dict",
        None,
        {},
        {"item": "not a dict"},
        broken(lambda it: it["item"].__setitem__("id", 65)),
        broken(lambda it: it["item"].pop("id")),
        broken(lambda it: it["item"].pop("additionalFields")),
        broken(lambda it: it["item"].__setitem__("additionalFields", [])),
        broken(lambda it: it["item"]["additionalFields"].__setitem__("link", None)),
        broken(lambda it: it["item"]["additionalFields"].pop("title")),
        broken(lambda it: it["item"]["additionalFields"].__setitem__("title", ["x"])),
        broken(lambda it: it["item"]["additionalFields"].pop("createdDate")),
        broken(lambda it: it["item"]["additionalFields"].__setitem__("createdDate", None)),
        broken(lambda it: it["item"]["additionalFields"].__setitem__("createdDate", 20260101)),
        broken(lambda it: it.__setitem__("tags", "compute")),
        broken(lambda it: it.__setitem__("tags", {"id": "x"})),
    ]
    body = {
        "items": [good_a, *bad, good_b],
        "metadata": {"count": len(bad) + 2, "totalHits": 40},
        "fieldTypes": FIELD_TYPES,
    }
    fake_aws.directory_override = lambda request: httpx2.Response(200, json=body)
    async with client_for(fake_aws) as dc:
        with caplog.at_level(logging.WARNING, logger="aws_blog_mcp.directory"):
            items, total = await dc.query(locale="ko_KR", size=50)
    assert items == [good_a, good_b]
    assert total == 40
    assert f"Skipped {len(bad)} malformed directory items" in caplog.text


@pytest.mark.parametrize("tags", [None, [], [{"id": "x"}]])
async def test_items_with_null_or_list_tags_are_kept(fake_aws: FakeAws, tags: Any) -> None:
    item = own(1)
    item["tags"] = tags
    no_tags = own(2)
    del no_tags["tags"]
    body = payload([item, no_tags])
    fake_aws.directory_override = lambda request: httpx2.Response(200, json=body)
    async with client_for(fake_aws) as dc:
        items, _ = await dc.query(locale="ko_KR", size=10)
    assert numbers(items) == [1, 2]


# --------------------------------------------------------------------------------------------
# fetch_blog(): filtering
# --------------------------------------------------------------------------------------------


async def test_fetch_blog_filters_by_id_prefix_and_link_prefix(fake_aws: FakeAws) -> None:
    fake_aws.items = [
        own(1),
        own(2),
        # blog 65 id but the link points to another blog
        make_item(3, "Wrong link", created=day(3), base=OTHER_BLOG),
        # another blog whose title happens to contain the token "65"
        make_item(4, "65 tips", created=day(4), prefix="12", base=OTHER_BLOG),
        # id prefix 650 must not pass as 65; link is even under the ko-tech blog
        make_item(5, "65 ways", created=day(5), prefix="650"),
        # ko-tech link but foreign id
        make_item(6, "65 more", created=day(6), prefix="12"),
    ]
    async with client_for(fake_aws) as dc:
        items = await dc.fetch_blog(KO_TECH)
    assert ids(items) == ["blog-posts#65-2", "blog-posts#65-1"]
    # The q trick found blog 65 plus the "65" titles; no fallback scan was needed.
    assert pages(fake_aws) == [("65", "desc", "0", "2000")]
    assert params(fake_aws.requests[0])["q_operator"] == "AND"


async def test_fetch_blog_returns_newest_first(fake_aws: FakeAws) -> None:
    fake_aws.items = [own(3), own(1), own(5), own(2), own(4)]
    async with client_for(fake_aws) as dc:
        items = await dc.fetch_blog(KO_TECH)
    assert numbers(items) == [5, 4, 3, 2, 1]


async def test_page_size_is_capped(fake_aws: FakeAws) -> None:
    fake_aws.items = [own(1)]
    async with client_for(fake_aws, page_size=5000) as dc:
        await dc.fetch_blog(KO_TECH)
    assert pages(fake_aws) == [("65", "desc", "0", str(MAX_PAGE_SIZE))]


# --------------------------------------------------------------------------------------------
# fetch_blog(): paging
# --------------------------------------------------------------------------------------------


async def test_paging_until_short_page(fake_aws: FakeAws) -> None:
    fake_aws.items = [own(n) for n in range(1, 8)]
    async with client_for(fake_aws, page_size=3) as dc:
        items = await dc.fetch_blog(KO_TECH)
    assert numbers(items) == [7, 6, 5, 4, 3, 2, 1]
    assert pages(fake_aws) == [
        ("65", "desc", "0", "3"),
        ("65", "desc", "1", "3"),
        ("65", "desc", "2", "3"),
    ]


async def test_paging_stops_when_total_reached(fake_aws: FakeAws) -> None:
    fake_aws.items = [own(n) for n in range(1, 7)]
    async with client_for(fake_aws, page_size=3) as dc:
        items = await dc.fetch_blog(KO_TECH)
    assert numbers(items) == [6, 5, 4, 3, 2, 1]
    assert pages(fake_aws) == [("65", "desc", "0", "3"), ("65", "desc", "1", "3")]


async def test_paging_total_includes_foreign_items(fake_aws: FakeAws) -> None:
    # totalHits counts every q match, own or not; paging must continue to collect them all.
    fake_aws.items = [
        *(own(n) for n in range(1, 4)),
        *(make_item(n, "65 foreign", created=day(n), prefix="12") for n in range(4, 8)),
    ]
    async with client_for(fake_aws, page_size=3) as dc:
        items = await dc.fetch_blog(KO_TECH)
    assert numbers(items) == [3, 2, 1]
    assert [p[2] for p in pages(fake_aws)] == ["0", "1", "2"]


async def test_desc_then_asc_when_total_exceeds_window(
    fake_aws: FakeAws, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(directory, "MAX_WINDOW", 6)
    window_limit(fake_aws, 6)
    fake_aws.items = [own(n) for n in range(1, 9)]
    async with client_for(fake_aws, page_size=3) as dc:
        items = await dc.fetch_blog(KO_TECH)
    assert numbers(items) == [8, 7, 6, 5, 4, 3, 2, 1]
    assert len(set(ids(items))) == 8
    assert pages(fake_aws) == [
        ("65", "desc", "0", "3"),
        ("65", "desc", "1", "3"),
        ("65", "asc", "0", "3"),
    ]


async def test_incomplete_listing_logs_and_returns_what_it_got(
    fake_aws: FakeAws, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(directory, "MAX_WINDOW", 6)
    window_limit(fake_aws, 6)
    fake_aws.items = [own(n) for n in range(1, 16)]
    async with client_for(fake_aws, page_size=3) as dc:
        with caplog.at_level(logging.WARNING, logger="aws_blog_mcp.directory"):
            items = await dc.fetch_blog(KO_TECH)
    assert numbers(items) == [15, 14, 13, 12, 11, 10, 6, 5, 4, 3, 2, 1]
    assert pages(fake_aws) == [
        ("65", "desc", "0", "3"),
        ("65", "desc", "1", "3"),
        ("65", "asc", "0", "3"),
        ("65", "asc", "1", "3"),
    ]
    assert "Directory listing incomplete: 12 of 15 items" in caplog.text


async def test_real_window_constants_with_large_result_set(fake_aws: FakeAws) -> None:
    """12000 hits with the real limits: five desc pages of 2000, then one asc page."""
    total = 12_000

    def synthetic(n: int) -> dict[str, Any]:
        created = (START + timedelta(minutes=n)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return make_item(n, f"Synthetic post {n}", created=created)

    def override(request: httpx2.Request) -> httpx2.Response:
        p = params(request)
        size, page = int(p["size"]), int(p["page"])
        if size > MAX_PAGE_SIZE or (page + 1) * size > directory.MAX_WINDOW:
            return httpx2.Response(200, json={"items": [], "metadata": {}, "fieldTypes": {}})
        if p["sort_order"] == "desc":
            numbers_ = range(total - page * size, total - (page + 1) * size, -1)
        else:
            numbers_ = range(page * size + 1, (page + 1) * size + 1)
        return httpx2.Response(200, json=payload([synthetic(n) for n in numbers_], total))

    fake_aws.directory_override = override
    async with client_for(fake_aws) as dc:
        items = await dc.fetch_blog(KO_TECH)
    assert len(items) == total
    assert numbers(items)[:2] == [12_000, 11_999]
    assert numbers(items)[-2:] == [2, 1]
    assert pages(fake_aws) == [
        *(("65", "desc", str(p), "2000") for p in range(5)),
        ("65", "asc", "0", "2000"),
    ]


# --------------------------------------------------------------------------------------------
# fetch_blog(): fallback scan
# --------------------------------------------------------------------------------------------


def q_returns(fake: FakeAws, items: list[dict[str, Any]]) -> None:
    """Answer every ``q=`` query with ``items``; full scans fall through to the fake."""

    def override(request: httpx2.Request) -> httpx2.Response | None:
        if "q" in params(request):
            return httpx2.Response(200, json=payload(items))
        return None

    fake.directory_override = override


async def test_fallback_scan_when_q_trick_finds_nothing(
    fake_aws: FakeAws, caplog: pytest.LogCaptureFixture
) -> None:
    fake_aws.items = [
        own(1),
        own(2),
        own(3),
        make_item(4, "Other blog", created=day(4), prefix="12", base=OTHER_BLOG),
    ]
    q_returns(fake_aws, [])
    async with client_for(fake_aws) as dc:
        with caplog.at_level(logging.WARNING, logger="aws_blog_mcp.directory"):
            items = await dc.fetch_blog(KO_TECH)
    assert numbers(items) == [3, 2, 1]
    assert pages(fake_aws) == [("65", "desc", "0", "2000"), (None, "desc", "0", "2000")]
    assert "q_operator" not in params(fake_aws.requests[1])
    assert "Directory query q=65 kept 0 posts (previous load: 0); scanning all of ko_KR" in (
        caplog.text
    )


async def test_fallback_scan_when_q_trick_finds_only_foreign_items(fake_aws: FakeAws) -> None:
    foreign = make_item(9, "65 elsewhere", created=day(9), prefix="12", base=OTHER_BLOG)
    fake_aws.items = [own(1), foreign]
    q_returns(fake_aws, [foreign])
    async with client_for(fake_aws) as dc:
        items = await dc.fetch_blog(KO_TECH)
    assert numbers(items) == [1]
    assert [p[0] for p in pages(fake_aws)] == ["65", None]


async def test_fallback_scan_when_q_trick_keeps_less_than_half(fake_aws: FakeAws) -> None:
    fake_aws.items = [own(1), own(2), own(3), own(4)]
    q_returns(fake_aws, [own(4)])
    async with client_for(fake_aws) as dc:
        items = await dc.fetch_blog(KO_TECH, previous_count=4)
    assert numbers(items) == [4, 3, 2, 1]
    assert [p[0] for p in pages(fake_aws)] == ["65", None]


async def test_no_fallback_at_exactly_half(fake_aws: FakeAws) -> None:
    fake_aws.items = [own(1), own(2), own(3), own(4)]
    q_returns(fake_aws, [own(4), own(3)])
    async with client_for(fake_aws) as dc:
        items = await dc.fetch_blog(KO_TECH, previous_count=4)
    assert numbers(items) == [4, 3]
    assert [p[0] for p in pages(fake_aws)] == ["65"]


async def test_no_fallback_without_previous_count(fake_aws: FakeAws) -> None:
    fake_aws.items = [own(1), own(2), own(3), own(4)]
    q_returns(fake_aws, [own(4)])
    async with client_for(fake_aws) as dc:
        items = await dc.fetch_blog(KO_TECH)
    assert numbers(items) == [4]
    assert len(fake_aws.requests) == 1


async def test_fallback_scan_pages_too(fake_aws: FakeAws) -> None:
    fake_aws.items = [own(n) for n in range(1, 6)]
    q_returns(fake_aws, [])
    async with client_for(fake_aws, page_size=2) as dc:
        items = await dc.fetch_blog(KO_TECH)
    assert numbers(items) == [5, 4, 3, 2, 1]
    assert pages(fake_aws) == [
        ("65", "desc", "0", "2"),
        (None, "desc", "0", "2"),
        (None, "desc", "1", "2"),
        (None, "desc", "2", "2"),
    ]


async def test_error_when_nothing_found(fake_aws: FakeAws) -> None:
    fake_aws.items = [make_item(1, "Other blog", prefix="12", base=OTHER_BLOG)]
    async with client_for(fake_aws) as dc:
        with pytest.raises(UpstreamError) as info:
            await dc.fetch_blog(KO_TECH)
    assert str(info.value) == (
        "The AWS directory API returned no posts for https://aws.amazon.com/ko/blogs/tech/. "
        "The API may have changed; try again later."
    )
    assert [p[0] for p in pages(fake_aws)] == ["65", None]


async def test_error_when_directory_empty(fake_aws: FakeAws) -> None:
    async with client_for(fake_aws) as dc:
        with pytest.raises(UpstreamError, match="returned no posts"):
            await dc.fetch_blog(KO_TECH)
    assert len(fake_aws.requests) == 2


async def test_rejected_q_query_is_not_masked_by_fallback(fake_aws: FakeAws) -> None:
    fake_aws.items = [own(1)]
    fake_aws.directory_override = lambda request: httpx2.Response(
        200, json={"items": [], "metadata": {}, "fieldTypes": {}}
    )
    async with client_for(fake_aws) as dc:
        with pytest.raises(UpstreamError, match="rejected the query"):
            await dc.fetch_blog(KO_TECH)
    assert len(fake_aws.requests) == 1


# --------------------------------------------------------------------------------------------
# fetch_recently_modified()
# --------------------------------------------------------------------------------------------


async def test_fetch_recently_modified(fake_aws: FakeAws) -> None:
    fake_aws.items = [
        own(1, modified=day(30)),
        own(2, modified=day(10)),
        own(3),  # modified == created (day 3)
        own(4, modified=day(20)),
        make_item(5, "65 other", created=day(5), modified=day(99), prefix="12"),
    ]
    async with client_for(fake_aws) as dc:
        items = await dc.fetch_recently_modified(KO_TECH)
    assert numbers(items) == [1, 4, 2, 3]
    (request,) = fake_aws.requests
    assert params(request) == {
        "item.directoryId": "blog-posts",
        "item.locale": "ko_KR",
        "sort_by": SORT_MODIFIED,
        "sort_order": "desc",
        "size": "50",
        "page": "0",
        "q": "65",
        "q_operator": "AND",
    }


async def test_fetch_recently_modified_custom_size(fake_aws: FakeAws) -> None:
    fake_aws.items = [own(1, modified=day(30)), own(2, modified=day(10)), own(3)]
    async with client_for(fake_aws) as dc:
        items = await dc.fetch_recently_modified(KO_TECH, size=2)
    assert numbers(items) == [1, 2]
    assert params(fake_aws.requests[0])["size"] == "2"


async def test_fetch_recently_modified_may_be_empty(fake_aws: FakeAws) -> None:
    async with client_for(fake_aws) as dc:
        assert await dc.fetch_recently_modified(KO_TECH) == []
