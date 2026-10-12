"""In-memory index of one blog's post metadata.

The whole Korean tech blog is under a thousand posts, so its metadata is loaded with a single
directory API call and kept in memory. Searching, filtering and listing then work locally.

Refresh policy (on demand only, never in the background):

* first use: full load;
* older than ``refresh_interval``: fetch the most recently modified posts and merge them;
* older than ``full_reload_interval``: full reload (the only way deleted posts disappear);
* if a refresh fails, the previous snapshot keeps being served and is flagged as stale.
"""

from __future__ import annotations

import difflib
import html
import json
import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import anyio

from aws_blog_mcp.blogs import BlogConfig
from aws_blog_mcp.directory import DirectoryClient, Item
from aws_blog_mcp.errors import BlogMCPError, InvalidInputError, UpstreamError
from aws_blog_mcp.search import SearchDoc, normalize, synonyms
from aws_blog_mcp.text import clean_excerpt, clean_text, split_authors

logger = logging.getLogger(__name__)

CATEGORY_NS = "blog-posts#category"
SUBCATEGORY_NS_PREFIX = "blog-posts#category-"
POST_TAGS_NS = "blog-posts#post-tags"
# Site-wide taxonomies whose (often Korean) names help matching but are not blog categories.
SEARCHABLE_GLOBAL_NS = frozenset(
    {
        "GLOBAL#tech-category",
        "GLOBAL#product",
        "GLOBAL#industry",
        "GLOBAL#use-case",
        "GLOBAL#business-category",
    }
)
HIDDEN_CATEGORY_SLUGS = frozenset({"uncategorized"})
# Top-level categories that only group facets (post type, level); their children are shown instead.
FACET_ROOT_SLUGS = frozenset({"post-types", "learning-levels"})
_DO_NOT_USE_RE = re.compile(r"^\[do not use\]\s*", re.I)
_ABBREVIATION_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<abbr>[^()]+)\)$")
MIN_YEAR, MAX_YEAR = 1990, 2100


@dataclass(frozen=True, slots=True)
class CategoryTag:
    """A WordPress category as it appears on a directory item."""

    tag_id: str
    slug: str
    name: str
    parent_key: str
    """Hyphen-joined slug path of the parent (empty for top-level categories)."""

    @property
    def key(self) -> str:
        """Hyphen-joined slug path of this category, as used in child namespaces."""
        return f"{self.parent_key}-{self.slug}" if self.parent_key else self.slug


@dataclass(frozen=True, slots=True)
class PostRecord:
    """Metadata of one post."""

    item_id: str
    url: str
    slug: str
    title: str
    excerpt: str
    authors: tuple[str, ...]
    published: datetime
    modified: datetime | None
    categories: tuple[CategoryTag, ...]
    extra_terms: tuple[str, ...]
    """Names from post tags and site-wide taxonomies, used for search and category matching."""
    image_url: str | None

    @property
    def post_id(self) -> str:
        """Short id, e.g. ``65-28623``."""
        return self.item_id.split("#", 1)[1]

    @property
    def category_names(self) -> tuple[str, ...]:
        """Display names of visible categories: topics first, then post type and level."""
        visible = [
            c
            for c in self.categories
            if c.slug not in HIDDEN_CATEGORY_SLUGS
            and not (not c.parent_key and c.slug in FACET_ROOT_SLUGS)
        ]
        visible.sort(key=lambda c: c.parent_key in FACET_ROOT_SLUGS)
        return tuple(dict.fromkeys(c.name for c in visible))


@dataclass(slots=True)
class CategoryNode:
    """A category in the blog's category tree."""

    tag_id: str
    slug: str
    name: str
    path: str
    """Slash-joined slug path, e.g. ``artificial-intelligence/kiro``. Unique within the blog."""
    display_path: str
    """Human readable path, e.g. ``Artificial Intelligence > Kiro``."""
    parent: str | None
    children: list[str] = field(default_factory=list)
    post_count: int = 0
    """Posts in this category or any of its subcategories."""


@dataclass(frozen=True, slots=True)
class IndexSnapshot:
    """Immutable view of the index at one point in time."""

    blog: BlogConfig
    posts: tuple[PostRecord, ...]
    """Newest first."""
    docs: tuple[SearchDoc, ...]
    """Search documents, parallel to ``posts``."""
    categories: dict[str, CategoryNode]
    """By tag id."""
    loaded_at: datetime
    stale: bool = False
    _by_url: dict[str, int] = field(default_factory=dict)
    _by_slug: dict[str, int] = field(default_factory=dict)
    _by_post_id: dict[str, int] = field(default_factory=dict)

    @classmethod
    def build(
        cls, blog: BlogConfig, posts: Iterable[PostRecord], *, loaded_at: datetime
    ) -> IndexSnapshot:
        """Build a snapshot (sorts posts newest first and builds lookup tables)."""
        ordered = tuple(sorted(posts, key=lambda p: p.published, reverse=True))
        docs = tuple(
            SearchDoc.build(
                title=p.title,
                categories=[*p.category_names, *p.extra_terms],
                excerpt=p.excerpt,
                slug=p.slug,
                authors=p.authors,
            )
            for p in ordered
        )
        return cls(
            blog=blog,
            posts=ordered,
            docs=docs,
            categories=_build_category_tree(ordered),
            loaded_at=loaded_at,
            _by_url={p.url: i for i, p in enumerate(ordered)},
            _by_slug={p.slug: i for i, p in enumerate(ordered)},
            _by_post_id={p.post_id: i for i, p in enumerate(ordered)},
        )

    def with_stale(self, stale: bool) -> IndexSnapshot:
        """Return a copy with the stale flag set."""
        return IndexSnapshot(
            blog=self.blog,
            posts=self.posts,
            docs=self.docs,
            categories=self.categories,
            loaded_at=self.loaded_at,
            stale=stale,
            _by_url=self._by_url,
            _by_slug=self._by_slug,
            _by_post_id=self._by_post_id,
        )

    def find(
        self, *, url: str | None = None, slug: str | None = None, post_id: str | None = None
    ) -> PostRecord | None:
        """Look up a post by URL, slug or short id."""
        for key, table in ((url, self._by_url), (slug, self._by_slug), (post_id, self._by_post_id)):
            if key is not None and key in table:
                return self.posts[table[key]]
        return None

    def filter_positions(
        self,
        *,
        category: str | None = None,
        author: str | None = None,
        published_after: date | None = None,
        published_before: date | None = None,
    ) -> list[int]:
        """Return positions (into ``posts``) of posts that pass all filters, newest first."""
        tz = self.blog.tz
        category_ids = self.resolve_category(category) if category else None
        term_patterns = _term_patterns(category) if category and category_ids is None else None
        author_q = normalize(author) if author else None
        result: list[int] = []
        for i, post in enumerate(self.posts):
            day = post.published.astimezone(tz).date()
            if published_after and day < published_after:
                continue
            if published_before and day > published_before:
                continue
            if category_ids is not None and not any(
                c.tag_id in category_ids for c in post.categories
            ):
                continue
            if term_patterns is not None and not any(
                p.search(normalize(t)) for t in post.extra_terms for p in term_patterns
            ):
                continue
            if author_q and not any(author_q in normalize(a) for a in post.authors):
                continue
            result.append(i)
        return result

    def match_categories(self, value: str, *, prefix_fallback: bool = True) -> list[CategoryNode]:
        """Return the categories ``value`` names exactly.

        Accepts a slug path (``artificial-intelligence/kiro``), a slug (``kiro``), a display name
        (``Amazon Bedrock``) or a display path (``Artificial Intelligence > Kiro``),
        case-insensitively. A path prefix whose root category never appears on a post (so it has
        no node of its own) matches the categories below it.
        """
        wanted = normalize(value).strip("/ ")
        if not wanted:
            return []
        nodes = list(self.categories.values())
        matches = [
            n
            for n in nodes
            if wanted in (n.path, n.slug, normalize(n.name), normalize(n.display_path))
        ]
        if not matches and prefix_fallback:
            matches = [n for n in nodes if n.parent is None and n.path.startswith(wanted + "/")]
        return matches

    def resolve_category(self, value: str) -> set[str] | None:
        """Map a user-supplied category to the tag ids it covers (including subcategories).

        See :meth:`match_categories` for accepted forms. Returns ``None`` when the value is not a
        category but names a post tag or a site-wide label, matched as a whole word (e.g. the
        Korean ``데이터베이스``, or ``EKS`` in "Amazon Elastic Kubernetes Service (EKS)"); the
        caller then filters on ``extra_terms``.

        Raises:
            InvalidInputError: nothing matches; the message suggests similar categories.
        """
        matches = self.match_categories(value)
        if matches:
            covered: set[str] = set()
            for node in matches:
                covered |= self._subtree(node.tag_id)
            return covered
        patterns = _term_patterns(value)
        if patterns and any(
            p.search(normalize(t))
            for post in self.posts
            for t in post.extra_terms
            for p in patterns
        ):
            return None
        similar = self.suggest_categories(value)
        hint = f" Similar categories: {_describe(similar)}." if similar else ""
        raise InvalidInputError(
            f"Unknown category {value!r}.{hint} Call list_categories with a query to search "
            "category names."
        )

    def category_notice(self, value: str) -> str | None:
        """Explain a category filter that matched labels rather than a real category."""
        if self.match_categories(value):
            return None
        similar = self.suggest_categories(value)
        notice = (
            f"{value!r} is not a blog category; showing posts labelled with it (post tags and "
            "AWS product/topic labels)."
        )
        if similar:
            notice += f" Related categories: {_describe(similar)}."
        return notice

    def suggest_categories(self, value: str, limit: int = 5) -> list[CategoryNode]:
        """Return categories related to ``value``, most used first.

        Related means the name or id contains it (or a synonym of it), or it abbreviates a
        product whose name matches a category; close spellings come last.
        """
        wanted = normalize(value).strip("/ ")
        if not wanted:
            return []
        nodes = [n for n in self.categories.values() if n.slug not in HIDDEN_CATEGORY_SLUGS]
        bare = _strip_vendor(wanted)
        found = [
            n for n in nodes if category_matches_text(n, wanted) or category_matches_text(n, bare)
        ]
        expansions = _abbreviation_expansions(bare, self.posts)
        found += [
            n for n in nodes if n not in found and any(e in normalize(n.name) for e in expansions)
        ]
        found.sort(key=lambda n: -n.post_count)
        if len(found) < limit:
            # Close spellings, whole names first, then single words ("kubernetis").
            names = {_strip_vendor(normalize(n.name)): n for n in nodes}
            words: dict[str, list[CategoryNode]] = {}
            for n in nodes:
                for word in normalize(n.name).split():
                    if len(word) > 3:
                        words.setdefault(word, []).append(n)
            close = [names[c] for c in difflib.get_close_matches(bare, list(names), limit, 0.6)]
            for word in difflib.get_close_matches(bare, list(words), limit, 0.8):
                close.extend(words[word])
            for node in close:
                if node not in found:
                    found.append(node)
        return found[:limit]

    def _subtree(self, tag_id: str) -> set[str]:
        out = {tag_id}
        stack = [tag_id]
        while stack:
            for child in self.categories[stack.pop()].children:
                if child not in out:
                    out.add(child)
                    stack.append(child)
        return out


class BlogIndex:
    """Lazily loaded, periodically refreshed index for one blog."""

    def __init__(
        self,
        blog: BlogConfig,
        directory: DirectoryClient,
        *,
        refresh_interval: float = 600.0,
        full_reload_interval: float = 6 * 3600.0,
        retry_interval: float = 60.0,
        first_load_cooldown: float = 15.0,
        refresh_deadline: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.blog = blog
        self._directory = directory
        self._refresh_interval = refresh_interval
        self._full_reload_interval = full_reload_interval
        self._retry_interval = retry_interval
        self._first_load_cooldown = first_load_cooldown
        self._refresh_deadline = refresh_deadline
        self._clock = clock
        self._lock = anyio.Lock()
        self._snapshot: IndexSnapshot | None = None
        self._full_at = 0.0
        self._refreshed_at = 0.0
        self._next_attempt = 0.0
        self._failure: str | None = None
        self._failure_until = 0.0

    async def snapshot(self) -> IndexSnapshot:
        """Return a current snapshot, loading or refreshing first if needed.

        While a refresh is running, other callers get the current snapshot without waiting. A
        failed refresh keeps serving the previous snapshot (flagged stale) and is retried after
        ``retry_interval``. A failed first load is reported to every caller for
        ``first_load_cooldown`` seconds before it is attempted again.

        Raises:
            UpstreamError: the first load failed.
        """
        current = self._snapshot
        if current is not None and (not self._refresh_due(self._clock()) or self._lock.locked()):
            return current
        self._raise_recent_failure()
        async with self._lock:
            now = self._clock()
            if self._snapshot is None:
                self._raise_recent_failure()
                try:
                    await self._full_load(now)
                except Exception as exc:
                    self._failure = (
                        str(exc)
                        if isinstance(exc, BlogMCPError)
                        else "Loading the post list from aws.amazon.com failed "
                        f"({type(exc).__name__})."
                    )
                    self._failure_until = self._clock() + self._first_load_cooldown
                    if isinstance(exc, BlogMCPError):
                        raise
                    logger.exception("Unexpected error while loading the post list")
                    raise UpstreamError(self._failure) from exc
                self._failure = None
            elif self._refresh_due(now):
                try:
                    with anyio.fail_after(self._refresh_deadline):
                        if now - self._full_at >= self._full_reload_interval:
                            await self._full_load(now)
                        else:
                            await self._incremental(now)
                except Exception as exc:
                    logger.warning(
                        "Index refresh failed, serving stale data: %s",
                        str(exc) or type(exc).__name__,
                        exc_info=not isinstance(exc, BlogMCPError | TimeoutError),
                    )
                    self._snapshot = self._snapshot.with_stale(True)
                    self._next_attempt = self._clock() + self._retry_interval
            assert self._snapshot is not None  # noqa: S101 - _full_load raises or sets it
            return self._snapshot

    def _raise_recent_failure(self) -> None:
        if self._snapshot is None and self._failure and self._clock() < self._failure_until:
            raise UpstreamError(self._failure)

    def _refresh_due(self, now: float) -> bool:
        return now >= self._next_attempt and now - self._refreshed_at >= self._refresh_interval

    async def _full_load(self, now: float) -> None:
        previous = len(self._snapshot.posts) if self._snapshot else 0
        items = await self._directory.fetch_blog(self.blog, previous_count=previous)
        records = _parse_items(items)
        self._snapshot = IndexSnapshot.build(self.blog, records, loaded_at=datetime.now(UTC))
        self._full_at = self._refreshed_at = now
        logger.info("Loaded %d posts for %s", len(records), self.blog.key)

    async def _incremental(self, now: float) -> None:
        assert self._snapshot is not None  # noqa: S101
        current = self._snapshot
        changed = _parse_items(await self._directory.fetch_recently_modified(self.blog))
        newest_known = max((p.modified or p.published for p in current.posts), default=None)
        oldest_changed = min((p.modified or p.published for p in changed), default=None)
        if newest_known and oldest_changed and oldest_changed > newest_known:
            # Everything in the batch is new: there may be more changes than one batch holds.
            await self._full_load(now)
            return
        merged = {p.item_id: p for p in current.posts}
        merged.update((p.item_id, p) for p in changed)
        self._snapshot = IndexSnapshot.build(
            self.blog, merged.values(), loaded_at=datetime.now(UTC)
        )
        self._refreshed_at = now


def _parse_items(items: Iterable[Item]) -> list[PostRecord]:
    records: list[PostRecord] = []
    for item in items:
        try:
            records.append(parse_item(item))
        except Exception as exc:  # one odd item must not take the whole index down
            logger.warning("Skipping unparsable directory item: %r", exc)
    return records


def parse_item(raw: Item) -> PostRecord:
    """Convert one directory API item into a :class:`PostRecord`.

    Raises:
        KeyError, TypeError, ValueError: required fields are missing or malformed.
    """
    item = raw["item"]
    fields = item["additionalFields"]
    categories: list[CategoryTag] = []
    extra: list[str] = []
    tags = raw.get("tags")
    for tag in tags if isinstance(tags, list) else []:
        if not isinstance(tag, dict):
            continue
        ns, tag_id, slug = tag.get("tagNamespaceId"), tag.get("id"), tag.get("name")
        if not (isinstance(ns, str) and isinstance(tag_id, str) and isinstance(slug, str)):
            continue
        if ns == CATEGORY_NS or ns.startswith(SUBCATEGORY_NS_PREFIX):
            categories.append(
                CategoryTag(
                    tag_id=tag_id,
                    slug=slug,
                    name=_tag_display_name(tag),
                    parent_key=ns[len(SUBCATEGORY_NS_PREFIX) :] if ns != CATEGORY_NS else "",
                )
            )
        elif ns == POST_TAGS_NS:
            extra.append(_tag_display_name(tag))
        elif ns in SEARCHABLE_GLOBAL_NS:
            extra.append(clean_text(_DO_NOT_USE_RE.sub("", html.unescape(slug))))
    link = fields["link"]
    published = _parse_datetime(fields["createdDate"])
    modified = _optional_datetime(fields.get("modifiedDate"))
    return PostRecord(
        item_id=item["id"],
        url=link,
        slug=_text(fields.get("slug")) or link.rstrip("/").rsplit("/", 1)[-1],
        title=clean_text(fields["title"]),
        excerpt=clean_excerpt(_text(fields.get("postExcerpt"))),
        authors=split_authors(_text(fields.get("contributors"))),
        published=published,
        modified=modified,
        categories=tuple(categories),
        extra_terms=tuple(dict.fromkeys(t for t in extra if t)),
        image_url=_text(fields.get("featuredImageUrl")) or None,
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _parse_datetime(value)
    except ValueError:
        return None


def _tag_display_name(tag: dict[str, Any]) -> str:
    description = tag.get("description") or ""
    try:
        name = json.loads(description).get("name")
    except (ValueError, TypeError, AttributeError):
        name = None
    if isinstance(name, str) and name.strip(" *"):
        return clean_text(name).lstrip("* ")  # "*Post Types" sorts first in WordPress admin only
    return str(tag.get("name", "")).replace("-", " ").title()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if not MIN_YEAR <= parsed.year <= MAX_YEAR:
        raise ValueError(f"implausible date {value!r}")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def category_matches_text(node: CategoryNode, wanted: str) -> bool:
    """Return True if normalised text (or a synonym) occurs in a category's name or id.

    For example ``쿠버네티스`` matches "Amazon Elastic Kubernetes Service".
    """
    haystacks = (normalize(node.name), node.path, normalize(node.display_path))
    needles = {wanted} | set(synonyms(wanted))
    return any(needle in h for needle in needles for h in haystacks)


def _strip_vendor(text: str) -> str:
    """``amazon eks`` -> ``eks``; ``aws lambda`` -> ``lambda`` (labels rarely repeat the vendor)."""
    for prefix in ("amazon ", "aws "):
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix) :]
    return text


def _term_patterns(value: str) -> list[re.Pattern[str]]:
    """Whole-word patterns for a label filter: the value, without vendor prefix, and synonyms."""
    wanted = normalize(value).strip("/ ")
    if not wanted:
        return []
    forms = {wanted, _strip_vendor(wanted)}
    for form in list(forms):
        forms |= synonyms(form)
    return [
        re.compile(rf"(?<![0-9a-z가-힣]){re.escape(form)}(?![0-9a-z가-힣])")
        for form in sorted(forms)
    ]


def _abbreviation_expansions(wanted: str, posts: Iterable[PostRecord]) -> set[str]:
    """Names abbreviated as ``wanted`` in labels like "Amazon Elastic Kubernetes Service (EKS)"."""
    out: set[str] = set()
    for post in posts:
        for term in post.extra_terms:
            match = _ABBREVIATION_RE.match(term)
            if match and normalize(match.group("abbr")) == wanted:
                out.add(normalize(match.group("name")).removeprefix("amazon ").strip())
    return {e for e in out if e}


def _describe(nodes: Iterable[CategoryNode]) -> str:
    return ", ".join(f"{n.name} ({n.path})" for n in nodes)


def _build_category_tree(posts: Iterable[PostRecord]) -> dict[str, CategoryNode]:
    tags: dict[str, CategoryTag] = {}
    members: dict[str, set[str]] = {}
    for post in posts:
        for tag in post.categories:
            tags.setdefault(tag.tag_id, tag)
            members.setdefault(tag.tag_id, set()).add(post.item_id)
    by_key = {tag.key: tag.tag_id for tag in tags.values()}

    def parent_of(tag: CategoryTag) -> str | None:
        return by_key.get(tag.parent_key) if tag.parent_key else None

    def chain(tag: CategoryTag) -> list[CategoryTag]:
        out = [tag]
        seen = {tag.tag_id}
        parent = parent_of(tag)
        while parent and parent not in seen:
            seen.add(parent)
            out.append(tags[parent])
            parent = parent_of(tags[parent])
        return out[::-1]

    nodes: dict[str, CategoryNode] = {}
    for tag in tags.values():
        lineage = chain(tag)
        # Parent never seen on any post: keep its namespace so the path stays unique.
        prefix = lineage[0].parent_key + "/" if lineage[0].parent_key else ""
        nodes[tag.tag_id] = CategoryNode(
            tag_id=tag.tag_id,
            slug=tag.slug,
            name=tag.name,
            path=prefix + "/".join(t.slug for t in lineage),
            display_path=" > ".join(t.name for t in lineage),
            parent=parent_of(tag),
        )
    for node in nodes.values():
        if node.parent:
            nodes[node.parent].children.append(node.tag_id)
    # A post counts once per category even if it is tagged with several of its subcategories.
    for node in nodes.values():
        covered: set[str] = set()
        stack = [node.tag_id]
        while stack:
            current = stack.pop()
            covered |= members.get(current, set())
            stack.extend(nodes[current].children)
        node.post_count = len(covered)
    return nodes
