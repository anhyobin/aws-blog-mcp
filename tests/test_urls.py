"""Unit tests for ``aws_blog_mcp.urls``."""

from __future__ import annotations

import re

import pytest

from aws_blog_mcp.blogs import KO_TECH
from aws_blog_mcp.errors import InvalidInputError
from aws_blog_mcp.urls import (
    RESERVED_SEGMENTS,
    PostReference,
    normalize_post_url,
    parse_reference,
)

BASE = "https://aws.amazon.com/ko/blogs/tech/"
POST = BASE + "my-post/"

# --- parse_reference --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("65-28623", PostReference("post_id", "65-28623")),
        ("blog-posts#65-28623", PostReference("post_id", "65-28623")),
        ("  65-28623\n", PostReference("post_id", "65-28623")),
        ("1-2", PostReference("post_id", "1-2")),
        ("my-post", PostReference("slug", "my-post")),
        ("My-Post", PostReference("slug", "My-Post")),  # slug case is preserved
        ("my_post.v2~x", PostReference("slug", "my_post.v2~x")),
        ("12345", PostReference("slug", "12345")),
        ("65-", PostReference("slug", "65-")),
        ("-28623", PostReference("slug", "-28623")),
        (
            "한글-슬러그",
            PostReference("slug", "%ED%95%9C%EA%B8%80-%EC%8A%AC%EB%9F%AC%EA%B7%B8"),
        ),
        ("%ed%95%9c%ea%b8%80", PostReference("slug", "%ED%95%9C%EA%B8%80")),
        ("%ED%95%9C%EA%B8%80", PostReference("slug", "%ED%95%9C%EA%B8%80")),
        (POST, PostReference("url", POST, KO_TECH)),
        ("aws.amazon.com/ko/blogs/tech/my-post", PostReference("url", POST, KO_TECH)),
        ("HTTP://aws.amazon.com/ko/blogs/tech/my-post", PostReference("url", POST, KO_TECH)),
    ],
)
def test_parse_reference(raw: str, expected: PostReference) -> None:
    assert parse_reference(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "\n\t"])
def test_parse_reference_empty(raw: str) -> None:
    with pytest.raises(InvalidInputError, match="Pass a post URL, slug or id"):
        parse_reference(raw)


@pytest.mark.parametrize(
    "raw",
    ["my post", "my?post", "my#post", "blog-posts#65-1x", "BLOG-POSTS#65-1", "a\tb"],
)
def test_parse_reference_rejects_non_slugs(raw: str) -> None:
    with pytest.raises(InvalidInputError, match="is not a post URL, slug or id"):
        parse_reference(raw)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("ko/blogs/tech/my-post", "is not a URL on https://aws.amazon.com"),
        ("https:my-post", "is not a URL on https://aws.amazon.com"),
        ("https://example.com/ko/blogs/tech/my-post/", "is not a URL on https://aws.amazon.com"),
        (BASE, "is a listing page"),
    ],
)
def test_parse_reference_url_errors(raw: str, message: str) -> None:
    with pytest.raises(InvalidInputError, match=re.escape(message)):
        parse_reference(raw)


# --- normalize_post_url -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (POST, POST),
        ("http://aws.amazon.com/ko/blogs/tech/my-post/", POST),
        ("HTTPS://aws.amazon.com/ko/blogs/tech/my-post/", POST),
        ("aws.amazon.com/ko/blogs/tech/my-post/", POST),
        ("AWS.Amazon.com/ko/blogs/tech/my-post/", POST),
        ("www.aws.amazon.com/ko/blogs/tech/my-post", POST),
        ("https://www.aws.amazon.com/ko/blogs/tech/my-post/", POST),
        ("https://AWS.AMAZON.COM/ko/blogs/tech/my-post/", POST),
        ("https://aws.amazon.com/ko/blogs/tech/my-post", POST),
        ("  https://aws.amazon.com/ko/blogs/tech/my-post/ \n", POST),
        ("https://aws.amazon.com/ko/blogs/tech/my-post/?utm_source=x&b=2", POST),
        ("https://aws.amazon.com/ko/blogs/tech/my-post/#section-2", POST),
        ("https://aws.amazon.com/ko/blogs/tech/my-post?x=1#frag", POST),
        ("https://aws.amazon.com//ko//blogs/tech//my-post//", POST),
        ("https://aws.amazon.com/ko/blogs/tech/my-post/index.html", POST),
        ("https://aws.amazon.com/ko/blogs/tech/my-post/index.htm", POST),
        ("https://aws.amazon.com/ko/blogs/tech/my-post/index.html?x=1", POST),
        ("https://aws.amazon.com:443/ko/blogs/tech/my-post/", POST),
        ("https://aws.amazon.com:80/ko/blogs/tech/my-post/", POST),
        ("https://aws.amazon.com/ko/blogs/tech/My-Post/", BASE + "My-Post/"),
        # Non-ASCII slugs are percent-encoded with upper-case hex; encoded input is normalised.
        (
            "https://aws.amazon.com/ko/blogs/tech/한글-슬러그/",
            BASE + "%ED%95%9C%EA%B8%80-%EC%8A%AC%EB%9F%AC%EA%B7%B8/",
        ),
        ("https://aws.amazon.com/ko/blogs/tech/%ed%95%9c%ea%b8%80/", BASE + "%ED%95%9C%EA%B8%80/"),
        ("https://aws.amazon.com/ko/blogs/tech/%ED%95%9C%EA%B8%80", BASE + "%ED%95%9C%EA%B8%80/"),
        ("https://aws.amazon.com/ko/blogs/tech/a b/", BASE + "a%20b/"),
        ("https://aws.amazon.com/ko/blogs/tech/x%3Fy/", BASE + "x%3Fy/"),
        ("https://aws.amazon.com/ko/blogs/tech/100%25-done/", BASE + "100%25-done/"),
    ],
)
def test_normalize_post_url(raw: str, expected: str) -> None:
    blog, url = normalize_post_url(raw)
    assert blog is KO_TECH
    assert url == expected


@pytest.mark.parametrize(
    "raw",
    [
        POST,
        "https://aws.amazon.com/ko/blogs/tech/한글-슬러그/",
        "https://aws.amazon.com/ko/blogs/tech/100%25-done/",
        "https://aws.amazon.com/ko/blogs/tech/a b/",
    ],
)
def test_normalize_post_url_is_idempotent(raw: str) -> None:
    _, once = normalize_post_url(raw)
    _, twice = normalize_post_url(once)
    assert twice == once


@pytest.mark.parametrize(
    "raw",
    [
        BASE,
        "https://aws.amazon.com/ko/blogs/tech",
        "aws.amazon.com/ko/blogs/tech/",
        "https://aws.amazon.com/ko/blogs/tech/index.html",
        "https://aws.amazon.com/ko/blogs/tech/page/2/",
        "https://aws.amazon.com/ko/blogs/tech/page/",
        "https://aws.amazon.com/ko/blogs/tech/category/compute/",
        "https://aws.amazon.com/ko/blogs/tech/category/artificial-intelligence/kiro/",
        "https://aws.amazon.com/ko/blogs/tech/category/",
        "https://aws.amazon.com/ko/blogs/tech/tag/eks/",
        "https://aws.amazon.com/ko/blogs/tech/author/someone/",
        "https://aws.amazon.com/ko/blogs/tech/feed/",
        "https://aws.amazon.com/ko/blogs/tech/my-post/feed/",
        "https://aws.amazon.com/ko/blogs/tech/search/",
        "https://aws.amazon.com/ko/blogs/tech/wp-json/",
        "https://aws.amazon.com/ko/blogs/tech/wp-admin/",
        "https://aws.amazon.com/ko/blogs/tech/wp-content/",
        "https://aws.amazon.com/ko/blogs/tech/2024/01/my-post/",
        "https://aws.amazon.com/ko/blogs/tech/x%2Fy/",  # an encoded slash is still a slash
    ],
)
def test_normalize_post_url_rejects_listing_pages(raw: str) -> None:
    with pytest.raises(InvalidInputError, match="is a listing page, not a post"):
        normalize_post_url(raw)


@pytest.mark.parametrize("segment", sorted(RESERVED_SEGMENTS))
def test_every_reserved_segment_is_a_listing(segment: str) -> None:
    with pytest.raises(InvalidInputError, match="is a listing page"):
        normalize_post_url(f"{BASE}{segment}/")


@pytest.mark.parametrize(
    "raw",
    [
        "https://aws.amazon.com/blogs/aws/some-post/",
        "https://aws.amazon.com/ko/blogs/korea/some-post/",
        "https://aws.amazon.com/ko/blogs/technology/some-post/",
        "https://aws.amazon.com/KO/blogs/tech/some-post/",
        "https://aws.amazon.com/ko/blogs/",
        "https://aws.amazon.com/",
        "aws.amazon.com/",
    ],
)
def test_normalize_post_url_rejects_other_blogs(raw: str) -> None:
    with pytest.raises(InvalidInputError, match="is not in a supported blog") as info:
        normalize_post_url(raw)
    assert BASE in str(info.value)  # the message lists the supported blogs


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com/ko/blogs/tech/my-post/",
        "https://aws.amazon.com.evil.example/ko/blogs/tech/my-post/",
        "https://evilaws.amazon.com/ko/blogs/tech/my-post/",
        "https://docs.aws.amazon.com/ko/blogs/tech/my-post/",
        "https://aws.amazon.com./ko/blogs/tech/my-post/",
        "https://[::1]/ko/blogs/tech/my-post/",
        "ftp://aws.amazon.com/ko/blogs/tech/my-post/",
        "javascript://aws.amazon.com/ko/blogs/tech/my-post/",
        "//aws.amazon.com/ko/blogs/tech/my-post/",
        "/ko/blogs/tech/my-post/",
        "example.com/aws.amazon.com/ko/blogs/tech/my-post/",
        "https:///ko/blogs/tech/my-post/",
    ],
)
def test_normalize_post_url_rejects_other_hosts_and_schemes(raw: str) -> None:
    with pytest.raises(
        InvalidInputError, match=re.escape("is not a URL on https://aws.amazon.com")
    ):
        normalize_post_url(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "https://aws.amazon.com:8443/ko/blogs/tech/my-post/",
        "https://aws.amazon.com:0/ko/blogs/tech/my-post/",
        "https://user@aws.amazon.com/ko/blogs/tech/my-post/",
        "https://user:secret@aws.amazon.com/ko/blogs/tech/my-post/",
        "https://:secret@aws.amazon.com/ko/blogs/tech/my-post/",
    ],
)
def test_normalize_post_url_rejects_ports_and_userinfo(raw: str) -> None:
    with pytest.raises(
        InvalidInputError, match=re.escape("is not a plain https://aws.amazon.com URL")
    ):
        normalize_post_url(raw)


# --- malformed and tricky references --------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "https://aws.amazon.com:abc/ko/blogs/tech/my-post/",
        "https://aws.amazon.com:99999/ko/blogs/tech/my-post/",
    ],
)
def test_bad_port_is_invalid_input(raw: str) -> None:
    with pytest.raises(InvalidInputError):
        parse_reference(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "https://aws.amazon.com/ko/blogs/tech/../",
        "https://aws.amazon.com/ko/blogs/tech/./",
        "https://aws.amazon.com/ko/blogs/tech/%2e%2e/",
        "..",
        ".",
    ],
)
def test_dot_segments_are_not_posts(raw: str) -> None:
    with pytest.raises(InvalidInputError):
        parse_reference(raw)


@pytest.mark.parametrize("raw", ["６５-２８６２３", "٦٥-١٢", "65-２8623"])
def test_post_id_needs_ascii_digits(raw: str) -> None:
    ref = parse_reference(raw)
    assert ref.kind == "slug"  # not a post id; looked up (and fetched) as a slug instead
    assert ref.value.isascii()


def test_prefixed_post_id_needs_ascii_digits() -> None:
    with pytest.raises(InvalidInputError, match="is not a post URL, slug or id"):
        parse_reference("blog-posts#６５-1")


@pytest.mark.parametrize(
    ("raw", "kind", "value"),
    [
        ("/ko/blogs/tech/my-post/", "url", "https://aws.amazon.com/ko/blogs/tech/my-post/"),
        ("/ko/blogs/tech/my-post", "url", "https://aws.amazon.com/ko/blogs/tech/my-post/"),
        ("my-post/", "slug", "my-post"),
    ],
)
def test_parse_reference_accepts_paths_and_trailing_slash_slugs(
    raw: str, kind: str, value: str
) -> None:
    ref = parse_reference(raw)
    assert (ref.kind, ref.value) == (kind, value)


@pytest.mark.parametrize("raw", ["/ko/blogs/korea/x/", "/ko/blogs/tech/", "/../etc/passwd"])
def test_parse_reference_rejects_bad_paths(raw: str) -> None:
    with pytest.raises(InvalidInputError):
        parse_reference(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "https://[aws.amazon.com/ko/blogs/tech/x/",
        "https://aws.amazon.com\\@evil.example/ko/blogs/tech/x/",
        "https://aws.amazon.com/ko/blogs/tech/a/../b/",
    ],
)
def test_normalize_rejects_malformed_or_tricky_urls(raw: str) -> None:
    with pytest.raises(InvalidInputError):
        normalize_post_url(raw)
