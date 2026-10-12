"""Validate and normalise references to blog posts (URL, slug or post id)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote, unquote, urlsplit

from aws_blog_mcp.blogs import BLOGS, HOST, ORIGIN, BlogConfig, blog_for_url
from aws_blog_mcp.errors import InvalidInputError

# Path segments under a blog that are listings, not posts.
RESERVED_SEGMENTS = frozenset(
    {"author", "category", "feed", "page", "search", "tag", "wp-admin", "wp-content", "wp-json"}
)
DOT_SEGMENTS = frozenset({".", ".."})
_POST_ID_RE = re.compile(r"^(?:blog-posts#)?([0-9]+-[0-9]+)$")
_SLUG_RE = re.compile(r"^[^\s/?#\\]+/?$")


@dataclass(frozen=True, slots=True)
class PostReference:
    """A parsed reference to a post."""

    kind: Literal["url", "post_id", "slug"]
    value: str
    """Normalised URL, short post id (``65-28623``) or slug."""
    blog: BlogConfig | None = None
    """Set for ``url`` references."""


def parse_reference(raw: str) -> PostReference:
    """Classify and normalise a user-supplied post reference.

    Raises:
        InvalidInputError: the reference is not a supported post URL, slug or id.
    """
    value = raw.strip()
    if not value:
        raise InvalidInputError("Pass a post URL, slug or id, for example one from search_posts.")
    if match := _POST_ID_RE.match(value):
        return PostReference("post_id", match.group(1))
    if _SLUG_RE.match(value) and not value.lower().startswith(("http:", "https:")):
        slug = unquote(value.rstrip("/"))
        if slug in DOT_SEGMENTS:
            raise InvalidInputError(f"{value!r} is not a post slug.")
        return PostReference("slug", quote(slug, safe="-_.~"))
    if value.startswith("/"):
        value = ORIGIN + value  # a path such as /ko/blogs/tech/<slug>/
    elif "/" not in value and not value.lower().startswith(("http:", "https:")):
        raise InvalidInputError(f"{value!r} is not a post URL, slug or id.")
    blog, url = normalize_post_url(value)
    return PostReference("url", url, blog)


def normalize_post_url(raw: str) -> tuple[BlogConfig, str]:
    """Return ``(blog, canonical_url)`` for a post URL.

    Accepts ``http``/``https`` and a missing scheme; strips query and fragment, collapses repeated
    slashes, drops ``index.html`` and adds the trailing slash the site requires.

    Raises:
        InvalidInputError: not an ``aws.amazon.com`` URL of a post in a supported blog.
    """
    text = raw.strip()
    if text.lower().startswith(f"{HOST}/") or text.lower().startswith(f"www.{HOST}/"):
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise InvalidInputError(f"{raw!r} is not a valid URL. {_supported_hint()}") from exc
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() not in ("http", "https") or host not in (HOST, f"www.{HOST}"):
        raise InvalidInputError(f"{raw!r} is not a URL on {ORIGIN}. {_supported_hint()}")
    if port not in (None, 80, 443) or parts.username or parts.password or "\\" in text:
        raise InvalidInputError(f"{raw!r} is not a plain {ORIGIN} URL.")
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    path = re.sub(r"/index\.html?$", "/", path)
    if not path.endswith("/"):
        path += "/"
    path = quote(unquote(path), safe="/-_.~")
    url = ORIGIN + path
    blog = blog_for_url(url)
    if blog is None:
        raise InvalidInputError(f"{raw!r} is not in a supported blog. {_supported_hint()}")
    rest = path[len(blog.path_prefix) :].strip("/")
    if unquote(rest) in DOT_SEGMENTS or ".." in unquote(path).split("/"):
        raise InvalidInputError(f"{raw!r} is not a post URL.")
    if not rest or "/" in rest or rest in RESERVED_SEGMENTS:
        raise InvalidInputError(
            f"{raw!r} is a listing page, not a post. Use search_posts or list_recent_posts to "
            "find post URLs."
        )
    return blog, url


def _supported_hint() -> str:
    homes = ", ".join(blog.base_url for blog in BLOGS.values())
    return f"Supported blogs: {homes}"
