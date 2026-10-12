"""Unit tests for the blog registry (``aws_blog_mcp.blogs``)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta

import pytest

from aws_blog_mcp import blogs
from aws_blog_mcp.blogs import (
    BLOGS,
    DEFAULT_BLOG,
    HOST,
    KO_TECH,
    ORIGIN,
    BlogConfig,
    blog_for_url,
)

EN_AWS = BlogConfig(
    key="en-aws",
    title="AWS News Blog",
    locale="en_US",
    id_prefix="1",
    base_url="https://aws.amazon.com/blogs/aws/",
    utc_offset_hours=-8,
)


def test_constants() -> None:
    assert HOST == "aws.amazon.com"
    assert ORIGIN == "https://aws.amazon.com"


def test_ko_tech_config() -> None:
    assert KO_TECH.key == "ko-tech"
    assert KO_TECH.locale == "ko_KR"
    assert KO_TECH.id_prefix == "65"
    assert KO_TECH.base_url == "https://aws.amazon.com/ko/blogs/tech/"
    assert KO_TECH.utc_offset_hours == 9


def test_registry_contents() -> None:
    assert list(BLOGS) == ["ko-tech"]
    assert BLOGS["ko-tech"] is KO_TECH
    assert DEFAULT_BLOG is KO_TECH


@pytest.mark.parametrize("blog", list(BLOGS.values()), ids=list(BLOGS))
def test_registry_invariants(blog: BlogConfig) -> None:
    assert BLOGS[blog.key] is blog
    assert blog.base_url.startswith(ORIGIN + "/")
    assert blog.base_url.endswith("/")
    assert blog.id_prefix.isdigit()
    assert blog.owns_url(blog.base_url)
    assert blog_for_url(blog.base_url + "some-post/") is blog


def test_config_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        KO_TECH.key = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("item_id", "expected"),
    [
        ("blog-posts#65-28623", True),
        ("blog-posts#65-1", True),
        ("blog-posts#651-28623", False),  # a different blog whose number starts with 65
        ("blog-posts#6-28623", False),
        ("blog-posts#165-28623", False),
        ("65-28623", False),  # short id without the namespace
        ("blog-posts#65_28623", False),
        ("other-dir#65-28623", False),
        ("BLOG-POSTS#65-28623", False),
        ("", False),
    ],
)
def test_owns_item_id(item_id: str, expected: bool) -> None:
    assert KO_TECH.owns_item_id(item_id) is expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://aws.amazon.com/ko/blogs/tech/my-post/", True),
        ("https://aws.amazon.com/ko/blogs/tech/", True),
        ("https://aws.amazon.com/ko/blogs/tech", False),  # no trailing slash
        ("https://aws.amazon.com/ko/blogs/technology/my-post/", False),
        ("https://aws.amazon.com/ko/blogs/korea/my-post/", False),
        ("https://aws.amazon.com/blogs/tech/my-post/", False),
        ("http://aws.amazon.com/ko/blogs/tech/my-post/", False),  # expects canonical URLs
        ("https://www.aws.amazon.com/ko/blogs/tech/my-post/", False),
        ("https://AWS.AMAZON.COM/ko/blogs/tech/my-post/", False),
        ("", False),
    ],
)
def test_owns_url(url: str, expected: bool) -> None:
    assert KO_TECH.owns_url(url) is expected


def test_path_prefix() -> None:
    assert KO_TECH.path_prefix == "/ko/blogs/tech/"
    assert EN_AWS.path_prefix == "/blogs/aws/"


def test_tz_offsets() -> None:
    assert KO_TECH.tz.utcoffset(None) == timedelta(hours=9)
    assert EN_AWS.tz.utcoffset(None) == timedelta(hours=-8)
    assert KO_TECH.tz.dst(None) is None


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        (datetime(2026, 1, 1, 14, 59, tzinfo=UTC), date(2026, 1, 1)),
        (datetime(2026, 1, 1, 15, 0, tzinfo=UTC), date(2026, 1, 2)),
        (datetime(2025, 12, 31, 15, 0, tzinfo=UTC), date(2026, 1, 1)),
    ],
)
def test_tz_display_date(utc: datetime, expected: date) -> None:
    assert utc.astimezone(KO_TECH.tz).date() == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://aws.amazon.com/ko/blogs/tech/my-post/",
        "https://aws.amazon.com/ko/blogs/tech/",
        "https://aws.amazon.com/ko/blogs/tech/%ED%95%9C%EA%B8%80/",
    ],
)
def test_blog_for_url_known(url: str) -> None:
    assert blog_for_url(url) is KO_TECH


@pytest.mark.parametrize(
    "url",
    [
        "https://aws.amazon.com/ko/blogs/tech",
        "https://aws.amazon.com/ko/blogs/techx/post/",
        "https://aws.amazon.com/blogs/aws/some-post/",
        "https://example.com/ko/blogs/tech/my-post/",
        "",
    ],
)
def test_blog_for_url_unknown(url: str) -> None:
    assert blog_for_url(url) is None


def test_blog_for_url_with_several_blogs(monkeypatch: pytest.MonkeyPatch) -> None:
    insights = dataclasses.replace(
        EN_AWS,
        key="en-aws-insights",
        id_prefix="2",
        base_url="https://aws.amazon.com/blogs/aws-insights/",
    )
    monkeypatch.setitem(blogs.BLOGS, EN_AWS.key, EN_AWS)
    monkeypatch.setitem(blogs.BLOGS, insights.key, insights)
    assert blog_for_url("https://aws.amazon.com/blogs/aws/launch-post/") is EN_AWS
    # The trailing slash in base_url keeps "aws" from claiming "aws-insights" posts.
    assert blog_for_url("https://aws.amazon.com/blogs/aws-insights/a-post/") is insights
    assert blog_for_url("https://aws.amazon.com/ko/blogs/tech/a-post/") is KO_TECH
    assert blog_for_url("https://aws.amazon.com/blogs/awsx/a-post/") is None
