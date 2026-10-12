"""Registry of supported AWS blogs.

Every blog post in the AWS directory API has an id like ``blog-posts#65-28623``. The number before
the dash identifies the blog and stays stable even when a blog is renamed, so it is the blog's
identity here. Adding a blog is a matter of adding a :class:`BlogConfig` entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from urllib.parse import urlsplit

HOST = "aws.amazon.com"
ORIGIN = f"https://{HOST}"


@dataclass(frozen=True, slots=True)
class BlogConfig:
    """Static description of one blog."""

    key: str
    """Short identifier exposed to users, e.g. ``ko-tech``."""
    title: str
    """Display title as shown on the site."""
    locale: str
    """Directory API locale, e.g. ``ko_KR``."""
    id_prefix: str
    """Blog number in directory item ids (``blog-posts#<id_prefix>-<post>``)."""
    base_url: str
    """Blog home URL with trailing slash; every post URL is ``base_url + slug + '/'``."""
    utc_offset_hours: int
    """Offset used to turn timestamps into the dates readers see on the site."""

    @property
    def path_prefix(self) -> str:
        """URL path of the blog home, e.g. ``/ko/blogs/tech/``."""
        return urlsplit(self.base_url).path

    @property
    def tz(self) -> timezone:
        """Fixed-offset timezone for display dates (no DST in currently supported locales)."""
        return timezone(timedelta(hours=self.utc_offset_hours))

    def owns_item_id(self, item_id: str) -> bool:
        """Return True if a directory item id belongs to this blog."""
        return item_id.startswith(f"blog-posts#{self.id_prefix}-")

    def owns_url(self, url: str) -> bool:
        """Return True if ``url`` is under this blog's home URL."""
        return url.startswith(self.base_url)


KO_TECH = BlogConfig(
    key="ko-tech",
    title="AWS 기술 블로그",
    locale="ko_KR",
    id_prefix="65",
    base_url="https://aws.amazon.com/ko/blogs/tech/",
    utc_offset_hours=9,
)

BLOGS: dict[str, BlogConfig] = {blog.key: blog for blog in (KO_TECH,)}
DEFAULT_BLOG = KO_TECH


def blog_for_url(url: str) -> BlogConfig | None:
    """Return the registered blog whose home URL prefixes ``url``."""
    for blog in BLOGS.values():
        if blog.owns_url(url):
            return blog
    return None
