"""In-memory stand-in for aws.amazon.com used by the test suite.

``FakeAws`` serves the directory API (``/api/dirs/items/search``) and post pages from data built
with :func:`make_item` and :func:`post_page`. Its directory emulation follows the behaviour
observed on the real API: ``q`` matches whole tokens of the id/title/slug, ``q_operator=AND``
requires all tokens, ``size`` is capped at 2000 and ``(page + 1) * size`` at 10000, and rejected
queries return HTTP 200 with an empty ``fieldTypes`` object.

Fixture content is synthetic. Do not paste real blog posts into tests: they are copyrighted.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx2

BASE = "https://aws.amazon.com/ko/blogs/tech/"
API_PATH = "/api/dirs/items/search"
FIELD_TYPES = {"title": "Text", "createdDate": "DateTime", "link": "URL"}

Category = tuple[str, str, str]
"""``(namespace, slug, display name)``, e.g. ``("blog-posts#category", "compute", "Compute")``."""

TOP = "blog-posts#category"


def cat(slug: str, name: str, parent_key: str = "") -> Category:
    """Build a category triple; ``parent_key`` is the hyphen-joined slug path of the parent."""
    ns = f"{TOP}-{parent_key}" if parent_key else TOP
    return (ns, slug, name)


def make_item(
    number: int,
    title: str,
    *,
    slug: str | None = None,
    created: str = "2026-01-01T00:00:00Z",
    modified: str | None = None,
    excerpt: str = "Synthetic excerpt for testing. [...]",
    contributors: str = "Test Author",
    categories: list[Category] | None = None,
    global_tags: list[tuple[str, str]] | None = None,
    prefix: str = "65",
    base: str = BASE,
) -> dict[str, Any]:
    """Build one directory API item."""
    slug = slug or f"post-{number}"
    tags: list[dict[str, Any]] = []
    for ns, tag_slug, name in categories or [cat("uncategorized", "Uncategorized")]:
        tags.append(
            {
                "id": f"{ns}#{tag_slug}",
                "tagNamespaceId": ns,
                "name": tag_slug,
                "description": json.dumps({"name": name, "description": "No Description Provided"}),
            }
        )
    for ns, name in global_tags or []:
        tags.append({"id": f"{ns}#{name}", "tagNamespaceId": ns, "name": name, "description": name})
    return {
        "item": {
            "id": f"blog-posts#{prefix}-{number}",
            "locale": "ko_KR",
            "directoryId": "blog-posts",
            "name": f"blog-posts#{prefix}-{number}",
            "dateCreated": created,
            "dateUpdated": modified or created,
            "additionalFields": {
                "title": title,
                "link": f"{base}{slug}/",
                "slug": slug,
                "postExcerpt": excerpt,
                "createdDate": created,
                "modifiedDate": modified or created,
                "contributors": contributors,
                "contentType": "블로그 게시물",
                "displayDate": "01 Jan 2026",
            },
        },
        "tags": tags,
    }


def post_page(
    *,
    url: str,
    title: str,
    body: str,
    byline: str = "Test Author",
    published: str = "2026-01-01T09:00:00+09:00",
    modified: str | None = None,
    categories: list[tuple[str, str]] | None = None,
    author_boxes: list[str] | None = None,
    single: bool = True,
) -> str:
    """Build a synthetic post page using the real template's structure."""
    cats = ", ".join(
        f'<a href="/ko/blogs/tech/category/{path}/" title="View all posts in {html.escape(name)}">'
        f'<span property="articleSection">{html.escape(name)}</span></a>'
        for path, name in (categories or [("compute", "Compute")])
    )
    boxes = "".join(
        f'<div class="blog-author-box" property="author" typeof="Person">'
        f'<div class="blog-author-image">'
        f'<img alt="{html.escape(n)}" src="https://example.invalid/a.jpg"></div>'
        f'<h3 class="lb-h4" property="name">{html.escape(n)}</h3>'
        f'<p property="description">Bio of {html.escape(n)}.</p></div>'
        for n in (author_boxes or [])
    )
    body_class = "aws-blogs-page aws-blogs-page-single" if single else "aws-blogs-page-standard"
    dated = f'<span>on <time property="datePublished" datetime="{published}">x</time></span>'
    return f"""<!doctype html>
<html class="aws-lng-en_US"><head>
<link rel="canonical" href="{url}">
<meta property="og:title" content="{html.escape(title)} | Amazon Web Services">
<meta property="article:published_time" content="{published}">
<meta property="article:modified_time" content="{modified or published}">
</head>
<body class="awsm page basicpage {body_class}">
<main id="aws-page-content-main">
<h2 class="blog-title"><a href="/ko/blogs/tech/">AWS 기술 블로그</a></h2>
<article class="blog-post" vocab="https://schema.org/" typeof="TechArticle">
<meta property="inLanguage" content="ko-KR">
<h1 class="lb-h2 blog-post-title" property="name headline">{html.escape(title)}</h1>
<footer class="blog-post-meta">
<span>by {html.escape(byline)}</span> {dated}
<span>in <span class="blog-post-categories">{cats}</span></span>
<span><a href="{url}" property="url">Permalink</a></span>
<span><a href="#" role="button" data-share-dialog-toggle>Share</a></span>
<div class="blog-share-dialog" data-share-dialog style="display: none;"><ul><li><a href="https://x.invalid/share">s</a></li></ul></div>
</footer>
<section class="blog-post-content lb-rtxt" property="articleBody">{body}</section>
<footer>{boxes}</footer>
</article>
</main></body></html>"""


@dataclass
class FakeAws:
    """Programmable fake of the aws.amazon.com endpoints used by the server."""

    items: list[dict[str, Any]] = field(default_factory=list)
    pages: dict[str, str] = field(default_factory=dict)
    """Post page HTML by exact URL."""
    redirects: dict[str, str] = field(default_factory=dict)
    """``url -> location`` answered with 301."""
    requests: list[httpx2.Request] = field(default_factory=list)
    directory_override: Callable[[httpx2.Request], httpx2.Response | None] | None = None
    """Return a response to short-circuit the directory emulation (``None`` to fall through)."""
    page_status: dict[str, int] = field(default_factory=dict)
    """Force an HTTP status for a page URL."""

    def transport(self) -> httpx2.MockTransport:
        """Return a transport serving this fake."""
        return httpx2.MockTransport(self.handler)

    def directory_requests(self) -> list[httpx2.Request]:
        """Requests made to the directory API."""
        return [r for r in self.requests if r.url.path == API_PATH]

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        """Serve one request."""
        self.requests.append(request)
        url = str(request.url)
        if url in self.redirects:
            return httpx2.Response(301, headers={"location": self.redirects[url]})
        if request.url.path == API_PATH:
            if self.directory_override is not None:
                response = self.directory_override(request)
                if response is not None:
                    return response
            return httpx2.Response(200, json=self._search(request))
        if url in self.page_status:
            return httpx2.Response(self.page_status[url], text="status")
        if url in self.pages:
            return httpx2.Response(
                200,
                text=self.pages[url],
                headers={"content-type": "text/html; charset=UTF-8"},
            )
        return httpx2.Response(404, text="<html><body class='error'>Not found</body></html>")

    def _search(self, request: httpx2.Request) -> dict[str, Any]:
        params = request.url.params
        rejected = {"items": [], "metadata": {"count": 0, "totalHits": 0}, "fieldTypes": {}}
        try:
            size = int(params.get("size", "10"))
            page = int(params.get("page", "0"))
        except ValueError:
            return rejected
        if params.get("item.directoryId") != "blog-posts" or not 1 <= size <= 2000:
            return rejected
        if (page + 1) * size > 10_000:
            return rejected
        sort_by = params.get("sort_by", "item.additionalFields.createdDate")
        field_name = sort_by.rsplit(".", 1)[-1]
        if field_name not in ("createdDate", "modifiedDate"):
            return rejected
        items = [
            it for it in self.items if it["item"]["locale"] == params.get("item.locale", "ko_KR")
        ]
        q = params.get("q")
        if q:
            tokens = q.lower().split()
            mode_all = params.get("q_operator") == "AND"
            matcher = all if mode_all else any
            items = [it for it in items if matcher(t in _tokens(it) for t in tokens)]
        reverse = params.get("sort_order", "desc") != "asc"
        items.sort(key=lambda it: it["item"]["additionalFields"][field_name], reverse=reverse)
        window = items[page * size : (page + 1) * size]
        return {
            "items": window,
            "metadata": {"count": len(window), "totalHits": len(items)},
            "fieldTypes": FIELD_TYPES,
        }


def _tokens(item: dict[str, Any]) -> set[str]:
    fields = item["item"]["additionalFields"]
    text = " ".join([item["item"]["id"], fields["title"], fields["slug"], fields["postExcerpt"]])
    return set(re.split(r"[^0-9a-z가-힣]+", text.lower())) - {""}
