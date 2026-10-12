"""Tests for :mod:`aws_blog_mcp.index`: item parsing, category tree, snapshot, BlogIndex."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import anyio
import httpx2
import pytest

import aws_blog_mcp.index as index_module
from aws_blog_mcp.blogs import KO_TECH
from aws_blog_mcp.directory import SORT_MODIFIED, DirectoryClient, Item
from aws_blog_mcp.errors import InvalidInputError, UpstreamError
from aws_blog_mcp.http import Fetcher, create_http_client
from aws_blog_mcp.index import (
    BlogIndex,
    CategoryNode,
    CategoryTag,
    IndexSnapshot,
    PostRecord,
    parse_item,
)
from tests.fakes import BASE, FIELD_TYPES, Category, FakeAws, cat, make_item

pytestmark = pytest.mark.anyio

LOADED_AT = datetime(2026, 1, 5, tzinfo=UTC)

AI = cat("artificial-intelligence", "Artificial Intelligence")
GENAI = cat("generative-ai", "Generative AI", "artificial-intelligence")
BEDROCK = cat("amazon-bedrock", "Amazon Bedrock", "artificial-intelligence-generative-ai")
KIRO = cat("kiro", "Kiro", "artificial-intelligence")
DEVTOOLS = cat("developer-tools", "Developer Tools")
DEV_KIRO = cat("kiro", "Kiro", "developer-tools")
COMPUTE = cat("compute", "Compute")
POST_TYPES = cat("post-types", "*Post Types")
TUTORIAL = cat("tutorial", "Tutorial", "post-types")
LEVELS = cat("learning-levels", "*Learning Levels")
L300 = cat("advanced-300", "Advanced (300)", "learning-levels")
UNCAT = cat("uncategorized", "Uncategorized")
POST_TAG_GENAI = ("blog-posts#post-tags", "genai", "GenAI")


def tid(category: Category) -> str:
    """Tag id produced by make_item for a category triple."""
    ns, slug, _ = category
    return f"{ns}#{slug}"


def record(number: int, **kwargs: Any) -> PostRecord:
    return parse_item(make_item(number, f"Post {number}", **kwargs))


def snapshot_of(*records: PostRecord) -> IndexSnapshot:
    return IndexSnapshot.build(KO_TECH, records, loaded_at=LOADED_AT)


def fields_of(item: dict[str, Any]) -> dict[str, Any]:
    return item["item"]["additionalFields"]


# --------------------------------------------------------------------------------------------
# parse_item
# --------------------------------------------------------------------------------------------


def test_parse_item_core_fields() -> None:
    item = make_item(
        28623,
        "Bedrock &amp; Kiro:\n  &lt;guide&gt;",
        slug="my-slug",
        created="2026-03-04T05:06:07Z",
        modified="2026-03-05T00:00:00Z",
        excerpt="Learn &quot;things&quot;   here [...]",
        contributors="Kyutae Park, Ph.D, Changwoo In and Minsu Kim",
    )
    rec = parse_item(item)
    assert rec.item_id == "blog-posts#65-28623"
    assert rec.post_id == "65-28623"
    assert rec.url == BASE + "my-slug/"
    assert rec.slug == "my-slug"
    assert rec.title == "Bedrock & Kiro: <guide>"
    assert rec.excerpt == 'Learn "things" here…'
    assert rec.authors == ("Kyutae Park, Ph.D", "Changwoo In", "Minsu Kim")
    assert rec.published == datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
    assert rec.modified == datetime(2026, 3, 5, tzinfo=UTC)
    assert rec.categories == (CategoryTag(tid(UNCAT), "uncategorized", "Uncategorized", ""),)
    assert rec.extra_terms == ()
    assert rec.image_url is None


@pytest.mark.parametrize(
    ("contributors", "expected"),
    [
        ("A Kim, B Lee, and C Park", ("A Kim", "B Lee", "C Park")),
        ("Solo Author", ("Solo Author",)),
        ("A Kim, a kim", ("A Kim",)),
        ("", ()),
    ],
)
def test_parse_item_authors(contributors: str, expected: tuple[str, ...]) -> None:
    assert record(1, contributors=contributors).authors == expected


def test_parse_item_image_url() -> None:
    item = make_item(1, "t")
    fields_of(item)["featuredImageUrl"] = "https://d2908q01vomqb2.cloudfront.net/x.png"
    assert parse_item(item).image_url == "https://d2908q01vomqb2.cloudfront.net/x.png"
    fields_of(item)["featuredImageUrl"] = ""
    assert parse_item(item).image_url is None


def test_parse_item_dates_without_timezone_are_utc() -> None:
    rec = record(1, created="2026-01-01T16:00:00", modified="2026-01-02T00:00:00.123")
    assert rec.published == datetime(2026, 1, 1, 16, tzinfo=UTC)
    assert rec.modified == datetime(2026, 1, 2, 0, 0, 0, 123000, tzinfo=UTC)


def test_parse_item_keeps_explicit_offset() -> None:
    rec = record(1, created="2026-01-02T01:00:00+09:00")
    assert rec.published == datetime(2026, 1, 1, 16, tzinfo=UTC)
    assert rec.published.utcoffset() == timedelta(hours=9)


@pytest.mark.parametrize("value", [None, ""])
def test_parse_item_without_modified_date(value: str | None) -> None:
    item = make_item(1, "t")
    if value is None:
        del fields_of(item)["modifiedDate"]
    else:
        fields_of(item)["modifiedDate"] = value
    assert parse_item(item).modified is None


@pytest.mark.parametrize("value", [None, ""])
def test_parse_item_slug_falls_back_to_link(value: str | None) -> None:
    item = make_item(7, "t", slug="from-link")
    if value is None:
        del fields_of(item)["slug"]
    else:
        fields_of(item)["slug"] = value
    rec = parse_item(item)
    assert rec.slug == "from-link"
    assert rec.url == BASE + "from-link/"


def test_parse_item_optional_fields_missing() -> None:
    item = make_item(1, "t")
    del fields_of(item)["postExcerpt"]
    del fields_of(item)["contributors"]
    del item["tags"]
    rec = parse_item(item)
    assert (rec.excerpt, rec.authors, rec.categories, rec.extra_terms) == ("", (), (), ())


def test_parse_item_null_tags() -> None:
    item = make_item(1, "t")
    item["tags"] = None
    assert parse_item(item).categories == ()


@pytest.mark.parametrize("key", ["postExcerpt", "contributors"])
def test_parse_item_null_optional_text_fields(key: str) -> None:
    item = make_item(1, "t")
    fields_of(item)[key] = None
    rec = parse_item(item)
    if key == "postExcerpt":
        assert rec.excerpt == ""
    else:
        assert rec.authors == ()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: f.pop("createdDate"),
        lambda f: f.__setitem__("createdDate", "yesterday"),
        lambda f: f.pop("title"),
        lambda f: f.pop("link"),
        lambda f: f.__setitem__("createdDate", "9999-12-31T23:00:00Z"),  # would overflow in UTC+9
        lambda f: f.__setitem__("createdDate", "0001-01-01T00:00:00Z"),
    ],
)
def test_parse_item_rejects_broken_required_fields(mutate: Any) -> None:
    item = make_item(1, "t")
    mutate(fields_of(item))
    with pytest.raises((KeyError, ValueError)):
        parse_item(item)


@pytest.mark.parametrize("value", [None, 20260101, ["2026-01-01"]])
def test_parse_item_rejects_non_string_created_date(value: Any) -> None:
    item = make_item(1, "t")
    fields_of(item)["createdDate"] = value
    with pytest.raises(TypeError):
        parse_item(item)


@pytest.mark.parametrize(
    ("created", "ok"),
    [
        ("1989-12-31T23:59:59Z", False),
        ("1990-01-01T00:00:00Z", True),
        ("2100-12-31T23:59:59Z", True),
        ("2101-01-01T00:00:00Z", False),
        ("9999-12-31T23:00:00+09:00", False),
    ],
)
def test_parse_item_plausible_year_range(created: str, ok: bool) -> None:
    item = make_item(1, "t", created=created)
    if ok:
        assert parse_item(item).published == datetime.fromisoformat(created)
    else:
        with pytest.raises(ValueError, match="implausible date"):
            parse_item(item)


@pytest.mark.parametrize(
    "value",
    [
        "2026-13-01T00:00:00Z",
        "garbage",
        12,
        None,
        "",
        ["2026-01-01T00:00:00Z"],
        "9999-12-31T23:00:00Z",
        "1970-01-01T00:00:00Z",
    ],
)
def test_parse_item_ignores_broken_modified_date(value: Any) -> None:
    item = make_item(1, "t")
    fields_of(item)["modifiedDate"] = value
    assert parse_item(item).modified is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: f.__setitem__("slug", 42),
        lambda f: f.__setitem__("contributors", ["a", "b"]),
        lambda f: f.__setitem__("postExcerpt", {"x": 1}),
        lambda f: f.__setitem__("featuredImageUrl", 7),
    ],
)
def test_parse_item_tolerates_wrongly_typed_optional_fields(mutate: Any) -> None:
    item = make_item(1, "t", slug="my-slug")
    mutate(fields_of(item))
    rec = parse_item(item)
    assert rec.title == "t"
    assert rec.slug in ("my-slug", "post-1")


@pytest.mark.parametrize(
    "bad_tag",
    [
        None,
        "compute",
        42,
        ["blog-posts#category"],
        {"id": None, "tagNamespaceId": "blog-posts#category", "name": "x"},
        {"id": "x", "tagNamespaceId": None, "name": "x"},
        {"id": "x", "tagNamespaceId": "blog-posts#category", "name": 5},
        {"id": "x", "tagNamespaceId": 7, "name": "x"},
        {"tagNamespaceId": "blog-posts#category"},
    ],
)
def test_parse_item_skips_malformed_tags(bad_tag: Any) -> None:
    item = make_item(1, "t", categories=[COMPUTE])
    item["tags"].insert(0, bad_tag)
    rec = parse_item(item)
    assert [c.tag_id for c in rec.categories] == [tid(COMPUTE)]
    assert rec.extra_terms == ()


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ({"name": "A dict"}, "Odd Slug"),
        ("[1, 2]", "Odd Slug"),
        ('"just a string"', "Odd Slug"),
        (json.dumps({"name": 5}), "Odd Slug"),
        (json.dumps({"name": "  "}), "Odd Slug"),
        (7, "Odd Slug"),
    ],
)
def test_parse_item_tolerates_odd_tag_descriptions(description: Any, expected: str) -> None:
    item = make_item(1, "t")
    item["tags"] = [
        {
            "id": "blog-posts#category#odd-slug",
            "tagNamespaceId": "blog-posts#category",
            "name": "odd-slug",
            "description": description,
        }
    ]
    assert [c.name for c in parse_item(item).categories] == [expected]


def test_parse_item_category_namespaces() -> None:
    rec = record(1, categories=[AI, GENAI, BEDROCK])
    assert rec.categories == (
        CategoryTag(tid(AI), "artificial-intelligence", "Artificial Intelligence", ""),
        CategoryTag(tid(GENAI), "generative-ai", "Generative AI", "artificial-intelligence"),
        CategoryTag(
            tid(BEDROCK),
            "amazon-bedrock",
            "Amazon Bedrock",
            "artificial-intelligence-generative-ai",
        ),
    )
    assert [c.key for c in rec.categories] == [
        "artificial-intelligence",
        "artificial-intelligence-generative-ai",
        "artificial-intelligence-generative-ai-amazon-bedrock",
    ]


def raw_tag(ns: str, slug: str, description: Any) -> dict[str, Any]:
    tag: dict[str, Any] = {"id": f"{ns}#{slug}", "tagNamespaceId": ns, "name": slug}
    if description is not None:
        tag["description"] = description
    return tag


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (json.dumps({"name": "Amazon Bedrock"}), "Amazon Bedrock"),
        (json.dumps({"name": "*Post Types"}), "Post Types"),
        (json.dumps({"name": "**  Starred"}), "Starred"),
        (json.dumps({"name": "AT&amp;T   Cloud"}), "AT&T Cloud"),
        (json.dumps({"name": "*"}), "Machine Learning"),
        (json.dumps({"name": "   "}), "Machine Learning"),
        (json.dumps({"name": 5}), "Machine Learning"),
        (json.dumps({"description": "No name"}), "Machine Learning"),
        (json.dumps(["Amazon Bedrock"]), "Machine Learning"),
        (json.dumps("Amazon Bedrock"), "Machine Learning"),
        ("not json at all", "Machine Learning"),
        ("", "Machine Learning"),
        (None, "Machine Learning"),
    ],
)
def test_category_display_name(description: Any, expected: str) -> None:
    item = make_item(1, "t")
    item["tags"] = [raw_tag("blog-posts#category", "machine-learning", description)]
    (tag,) = parse_item(item).categories
    assert tag.name == expected
    assert tag.slug == "machine-learning"


def test_extra_terms_from_post_tags_and_global_taxonomies() -> None:
    rec = record(
        1,
        categories=[COMPUTE, POST_TAG_GENAI],
        global_tags=[
            ("GLOBAL#product", "[DO NOT USE] Amazon Legacy Thing"),
            ("GLOBAL#product", "[do not use]Amazon Other"),
            ("GLOBAL#tech-category", "데이터베이스"),
            ("GLOBAL#industry", "Games &amp; Media"),
            ("GLOBAL#use-case", "  Spaced Out  "),
            ("GLOBAL#business-category", "Startups"),
            ("GLOBAL#region", "Seoul"),
            ("blog-posts#something-else", "Ignored"),
        ],
    )
    assert [c.slug for c in rec.categories] == ["compute"]
    assert rec.extra_terms == (
        "GenAI",
        "Amazon Legacy Thing",
        "Amazon Other",
        "데이터베이스",
        "Games & Media",
        "Spaced Out",
        "Startups",
    )


def test_extra_terms_drop_empty_and_duplicates() -> None:
    rec = record(
        1,
        categories=[("blog-posts#post-tags", "db", "데이터베이스")],
        global_tags=[
            ("GLOBAL#product", "[DO NOT USE]"),
            ("GLOBAL#tech-category", "데이터베이스"),
            ("GLOBAL#product", "Amazon Aurora"),
            ("GLOBAL#product", "Amazon Aurora"),
        ],
    )
    assert rec.extra_terms == ("데이터베이스", "Amazon Aurora")


def test_do_not_use_prefix_only_stripped_at_start() -> None:
    rec = record(1, global_tags=[("GLOBAL#product", "Amazon Thing [DO NOT USE] Beta")])
    assert rec.extra_terms == ("Amazon Thing [DO NOT USE] Beta",)


# --------------------------------------------------------------------------------------------
# PostRecord.category_names
# --------------------------------------------------------------------------------------------


def test_category_names_hide_uncategorized_and_facet_roots_topics_first() -> None:
    rec = record(1, categories=[UNCAT, POST_TYPES, TUTORIAL, AI, LEVELS, L300, GENAI])
    assert rec.category_names == (
        "Artificial Intelligence",
        "Generative AI",
        "Tutorial",
        "Advanced (300)",
    )


def test_category_names_dedupe_same_display_name() -> None:
    rec = record(1, categories=[KIRO, DEV_KIRO, COMPUTE])
    assert rec.category_names == ("Kiro", "Compute")


def test_category_names_only_hide_top_level_facet_roots() -> None:
    nested = cat("post-types", "Nested Post Types", "compute")
    nested_uncat = cat("uncategorized", "Nested Uncategorized", "compute")
    rec = record(1, categories=[nested, nested_uncat])
    assert rec.category_names == ("Nested Post Types",)


def test_category_names_empty_when_only_hidden() -> None:
    assert record(1, categories=[UNCAT, POST_TYPES]).category_names == ()


# --------------------------------------------------------------------------------------------
# category tree
# --------------------------------------------------------------------------------------------


def test_category_tree_three_levels() -> None:
    snap = snapshot_of(
        record(1, categories=[AI, GENAI, BEDROCK]),
        record(2, categories=[BEDROCK, KIRO]),
        record(3, categories=[COMPUTE, TUTORIAL, POST_TYPES]),
        record(4, categories=[GENAI]),
    )
    nodes = snap.categories
    assert set(nodes) == {tid(c) for c in (AI, GENAI, BEDROCK, KIRO, COMPUTE, TUTORIAL, POST_TYPES)}
    assert nodes[tid(AI)] == CategoryNode(
        tag_id=tid(AI),
        slug="artificial-intelligence",
        name="Artificial Intelligence",
        path="artificial-intelligence",
        display_path="Artificial Intelligence",
        parent=None,
        children=[tid(GENAI), tid(KIRO)],
        post_count=3,
    )
    assert nodes[tid(GENAI)] == CategoryNode(
        tag_id=tid(GENAI),
        slug="generative-ai",
        name="Generative AI",
        path="artificial-intelligence/generative-ai",
        display_path="Artificial Intelligence > Generative AI",
        parent=tid(AI),
        children=[tid(BEDROCK)],
        post_count=3,
    )
    assert nodes[tid(BEDROCK)] == CategoryNode(
        tag_id=tid(BEDROCK),
        slug="amazon-bedrock",
        name="Amazon Bedrock",
        path="artificial-intelligence/generative-ai/amazon-bedrock",
        display_path="Artificial Intelligence > Generative AI > Amazon Bedrock",
        parent=tid(GENAI),
        children=[],
        post_count=2,
    )
    assert (nodes[tid(KIRO)].path, nodes[tid(KIRO)].post_count) == (
        "artificial-intelligence/kiro",
        1,
    )
    assert nodes[tid(POST_TYPES)].name == "Post Types"
    assert nodes[tid(POST_TYPES)].children == [tid(TUTORIAL)]
    assert nodes[tid(TUTORIAL)].path == "post-types/tutorial"
    assert nodes[tid(TUTORIAL)].display_path == "Post Types > Tutorial"
    assert nodes[tid(COMPUTE)].post_count == 1


def test_post_counted_once_with_several_subcategories() -> None:
    snap = snapshot_of(
        record(1, categories=[AI]),
        record(2, categories=[GENAI, KIRO, BEDROCK]),
    )
    assert snap.categories[tid(AI)].post_count == 2
    assert snap.categories[tid(GENAI)].post_count == 1


def test_unseen_parent_keeps_namespace_prefix() -> None:
    snap = snapshot_of(record(1, categories=[KIRO]), record(2, categories=[BEDROCK]))
    kiro, bedrock = snap.categories[tid(KIRO)], snap.categories[tid(BEDROCK)]
    assert (kiro.path, kiro.display_path, kiro.parent) == (
        "artificial-intelligence/kiro",
        "Kiro",
        None,
    )
    assert (bedrock.path, bedrock.display_path, bedrock.parent) == (
        "artificial-intelligence-generative-ai/amazon-bedrock",
        "Amazon Bedrock",
        None,
    )
    assert snap.resolve_category("artificial-intelligence/kiro") == {tid(KIRO)}


def test_unseen_middle_parent_keeps_full_namespace() -> None:
    snap = snapshot_of(record(1, categories=[AI]), record(2, categories=[BEDROCK]))
    bedrock = snap.categories[tid(BEDROCK)]
    assert bedrock.path == "artificial-intelligence-generative-ai/amazon-bedrock"
    assert bedrock.parent is None
    assert snap.categories[tid(AI)].children == []


def test_same_slug_under_different_parents_has_distinct_paths() -> None:
    snap = snapshot_of(
        record(1, categories=[AI, KIRO]),
        record(2, categories=[DEVTOOLS, DEV_KIRO]),
    )
    assert snap.categories[tid(KIRO)].path == "artificial-intelligence/kiro"
    assert snap.categories[tid(DEV_KIRO)].path == "developer-tools/kiro"
    assert snap.resolve_category("kiro") == {tid(KIRO), tid(DEV_KIRO)}
    assert snap.resolve_category("developer-tools/kiro") == {tid(DEV_KIRO)}


def test_empty_snapshot() -> None:
    snap = snapshot_of()
    assert (snap.posts, snap.docs, snap.categories) == ((), (), {})
    assert snap.filter_positions() == []
    assert snap.find(post_id="65-1") is None


# --------------------------------------------------------------------------------------------
# IndexSnapshot: build, find, filters
# --------------------------------------------------------------------------------------------


@pytest.fixture
def snap() -> IndexSnapshot:
    """Four posts around the KST date line, deliberately passed oldest first."""
    return snapshot_of(
        # local 2026-01-01 00:00 KST
        record(
            4,
            created="2025-12-31T15:00:00Z",
            categories=[COMPUTE, TUTORIAL, POST_TYPES],
            contributors="Jiyoung Lee",
        ),
        # local 2026-01-01 23:59:59 KST
        record(
            3,
            created="2026-01-01T14:59:59Z",
            categories=[COMPUTE, POST_TAG_GENAI],
            contributors="Test Author",
        ),
        # local 2026-01-02 01:00 KST
        record(
            2,
            created="2026-01-01T16:00:00Z",
            categories=[KIRO],
            contributors="Jiyoung Lee, Minsu Kim",
        ),
        record(
            1,
            created="2026-01-03T00:00:00Z",
            categories=[AI, GENAI, BEDROCK],
            contributors="Minsu Kim",
            global_tags=[("GLOBAL#tech-category", "데이터베이스")],
        ),
    )


def test_build_orders_newest_first(snap: IndexSnapshot) -> None:
    assert [p.post_id for p in snap.posts] == ["65-1", "65-2", "65-3", "65-4"]
    assert len(snap.docs) == len(snap.posts)
    assert snap.loaded_at == LOADED_AT
    assert snap.stale is False
    assert snap.blog is KO_TECH


def test_find(snap: IndexSnapshot) -> None:
    assert snap.find(url=BASE + "post-2/") is snap.posts[1]
    assert snap.find(slug="post-3") is snap.posts[2]
    assert snap.find(post_id="65-4") is snap.posts[3]
    assert snap.find(url="https://aws.amazon.com/ko/blogs/tech/missing/") is None
    assert snap.find(slug="missing", post_id="65-999") is None
    assert snap.find() is None
    # an unknown url does not stop the slug lookup
    assert snap.find(url="https://aws.amazon.com/x/", slug="post-1") is snap.posts[0]
    # url wins over slug when both match
    assert snap.find(url=BASE + "post-2/", slug="post-1") is snap.posts[1]


def test_find_is_exact(snap: IndexSnapshot) -> None:
    assert snap.find(url=BASE + "post-2") is None
    assert snap.find(slug="POST-2") is None
    assert snap.find(post_id="blog-posts#65-2") is None


def test_with_stale(snap: IndexSnapshot) -> None:
    stale = snap.with_stale(True)
    assert stale.stale is True
    assert snap.stale is False
    assert stale.posts is snap.posts
    assert stale.categories is snap.categories
    assert stale.loaded_at == snap.loaded_at
    assert stale.find(post_id="65-3") is snap.posts[2]
    assert stale.with_stale(False).stale is False


def test_filter_without_arguments(snap: IndexSnapshot) -> None:
    assert snap.filter_positions() == [0, 1, 2, 3]


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("artificial-intelligence", [0, 1]),
        ("artificial-intelligence/generative-ai", [0]),
        ("/artificial-intelligence/generative-ai/", [0]),
        ("artificial-intelligence/generative-ai/amazon-bedrock", [0]),
        ("generative-ai", [0]),
        ("kiro", [1]),
        ("Amazon Bedrock", [0]),
        ("AMAZON BEDROCK", [0]),
        ("  amazon   bedrock ", [0]),
        ("Artificial Intelligence", [0, 1]),
        ("compute", [2, 3]),
        ("Post Types", [3]),
        ("post-types/tutorial", [3]),
        ("Tutorial", [3]),
    ],
)
def test_filter_by_category(snap: IndexSnapshot, category: str, expected: list[int]) -> None:
    assert snap.filter_positions(category=category) == expected


def test_resolve_category_covers_subtree(snap: IndexSnapshot) -> None:
    assert snap.resolve_category("artificial-intelligence") == {
        tid(AI),
        tid(GENAI),
        tid(BEDROCK),
        tid(KIRO),
    }
    assert snap.resolve_category("Generative AI") == {tid(GENAI), tid(BEDROCK)}
    assert snap.resolve_category("amazon-bedrock") == {tid(BEDROCK)}


@pytest.mark.parametrize(
    ("category", "expected"),
    [("데이터베이스", [0]), ("genai", [2]), ("GenAI", [2]), ("  GENAI ", [2])],
)
def test_filter_by_site_wide_taxonomy_name(
    snap: IndexSnapshot, category: str, expected: list[int]
) -> None:
    assert snap.resolve_category(category) is None
    assert snap.filter_positions(category=category) == expected


def test_filter_by_site_wide_taxonomy_name_with_slash(snap: IndexSnapshot) -> None:
    assert snap.resolve_category("데이터베이스/") is None
    assert snap.filter_positions(category="데이터베이스/") == [0]


def test_unknown_category_suggests_close_matches(snap: IndexSnapshot) -> None:
    with pytest.raises(InvalidInputError) as info:
        snap.filter_positions(category="Amazon Bedrok")
    message = str(info.value)
    assert message.startswith(
        "Unknown category 'Amazon Bedrok'. Similar categories: Amazon Bedrock ("
    )
    assert message.endswith("Call list_categories with a query to search category names.")


def test_unknown_category_without_close_matches(snap: IndexSnapshot) -> None:
    with pytest.raises(InvalidInputError) as info:
        snap.resolve_category("zzzzqqqq")
    assert str(info.value) == (
        "Unknown category 'zzzzqqqq'. Call list_categories with a query to search category names."
    )


@pytest.mark.parametrize("value", ["/", " / ", "//"])
def test_category_without_searchable_text_is_unknown(snap: IndexSnapshot, value: str) -> None:
    with pytest.raises(InvalidInputError) as info:
        snap.filter_positions(category=value)
    assert str(info.value) == (
        f"Unknown category {value!r}. Call list_categories with a query to search category names."
    )


def paths(nodes: list[CategoryNode]) -> list[str]:
    return [n.path for n in nodes]


EKS_LABEL = ("GLOBAL#product", "Amazon Elastic Kubernetes Service (EKS)")
S3_LABEL = ("GLOBAL#product", "Amazon Simple Storage Service (S3)")
STORAGE = cat("storage", "Storage")
S3_CAT = cat("simple-storage", "Amazon Simple Storage Service", "storage")
EKS_CAT = cat("amazon-eks", "Amazon Elastic Kubernetes Service", "compute")
AI_BEDROCK = "artificial-intelligence/generative-ai/amazon-bedrock"


@pytest.fixture
def labelled() -> IndexSnapshot:
    return snapshot_of(
        record(1, categories=[COMPUTE, EKS_CAT], global_tags=[EKS_LABEL]),
        record(2, categories=[STORAGE, S3_CAT], created="2026-01-02T00:00:00Z"),
        record(3, categories=[L300], global_tags=[S3_LABEL], created="2026-01-03T00:00:00Z"),
        record(4, categories=[AI, GENAI, BEDROCK], created="2026-01-04T00:00:00Z"),
        record(5, categories=[UNCAT], created="2026-01-05T00:00:00Z"),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("compute/amazon-eks", ["compute/amazon-eks"]),
        ("/compute/amazon-eks/", ["compute/amazon-eks"]),
        ("amazon-eks", ["compute/amazon-eks"]),
        ("Amazon Elastic Kubernetes Service", ["compute/amazon-eks"]),
        ("compute > amazon elastic kubernetes service", ["compute/amazon-eks"]),
        ("COMPUTE", ["compute"]),
        # The root of learning-levels never appears on a post, so it has no node of its own:
        # its path prefix matches the categories below it.
        ("learning-levels", ["learning-levels/advanced-300"]),
        ("learning-levels/advanced-300", ["learning-levels/advanced-300"]),
        ("Advanced (300)", ["learning-levels/advanced-300"]),
        ("EKS", []),
        ("kubernetes", []),
        ("", []),
        (" / ", []),
    ],
)
def test_match_categories(labelled: IndexSnapshot, value: str, expected: list[str]) -> None:
    assert paths(labelled.match_categories(value)) == expected


def test_match_categories_returns_every_category_with_that_slug() -> None:
    snap = snapshot_of(record(1, categories=[AI, KIRO]), record(2, categories=[DEVTOOLS, DEV_KIRO]))
    assert sorted(paths(snap.match_categories("kiro"))) == [
        "artificial-intelligence/kiro",
        "developer-tools/kiro",
    ]
    assert snap.resolve_category("Kiro") == {tid(KIRO), tid(DEV_KIRO)}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("EKS", ["65-1"]),
        ("eks", ["65-1"]),
        ("Elastic Kubernetes", ["65-1"]),
        ("(EKS)", ["65-1"]),
        ("S3", ["65-3"]),
        ("Simple Storage Service", ["65-3"]),
    ],
)
def test_label_filter_matches_whole_words(
    labelled: IndexSnapshot, value: str, expected: list[str]
) -> None:
    assert labelled.resolve_category(value) is None
    positions = labelled.filter_positions(category=value)
    assert [labelled.posts[i].post_id for i in positions] == expected


@pytest.mark.parametrize(
    "value", ["KS", "Kubernetes Serv", "Elast", "S", "Amazon Elastic Kubernetes Service (EK"]
)
def test_label_filter_ignores_partial_words(labelled: IndexSnapshot, value: str) -> None:
    with pytest.raises(InvalidInputError, match=f"^Unknown category {re.escape(repr(value))}"):
        labelled.resolve_category(value)


def test_category_notice(labelled: IndexSnapshot) -> None:
    assert labelled.category_notice("compute") is None
    assert labelled.category_notice("learning-levels") is None
    assert labelled.category_notice("EKS") == (
        "'EKS' is not a blog category; showing posts labelled with it (post tags and AWS "
        "product/topic labels). Related categories: Amazon Elastic Kubernetes Service "
        "(compute/amazon-eks)."
    )
    snap = snapshot_of(record(1, global_tags=[("GLOBAL#industry", "Games")]))
    assert snap.category_notice("games") == (
        "'games' is not a blog category; showing posts labelled with it (post tags and AWS "
        "product/topic labels)."
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("bedrock", [AI_BEDROCK]),  # substring of the name
        ("베드락", [AI_BEDROCK]),  # synonym
        ("eks", ["compute/amazon-eks"]),  # substring of the id
        # "S3" occurs in no category name or id; it abbreviates a product label whose name
        # ("Simple Storage Service") matches a category.
        ("S3", ["storage/simple-storage"]),
        ("Amazon Bedrok", [AI_BEDROCK]),  # close spelling
        ("storage", ["storage", "storage/simple-storage"]),
        ("uncategorized", []),  # hidden categories are never suggested
        ("zzzzqqqq", []),
        ("  ", []),
    ],
)
def test_suggest_categories(labelled: IndexSnapshot, value: str, expected: list[str]) -> None:
    assert paths(labelled.suggest_categories(value)) == expected


def test_suggest_categories_most_used_first_and_limited() -> None:
    gaming = [cat(f"game-{n}", f"Game Tech {n}") for n in range(8)]
    records = [
        record(n * 10 + k, categories=[gaming[n]], created=f"2026-01-{n + 1:02d}T00:0{k}:00Z")
        for n in range(8)
        for k in range(n + 1)
    ]
    snap = snapshot_of(*records)
    assert paths(snap.suggest_categories("game")) == [f"game-{n}" for n in (7, 6, 5, 4, 3)]
    assert len(snap.suggest_categories("game", limit=2)) == 2


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        ("kim", [0, 1]),
        ("MINSU", [0, 1]),
        ("Lee", [1, 3]),
        ("jiyoung lee", [1, 3]),
        ("test author", [2]),
        ("nobody", []),
    ],
)
def test_filter_by_author_substring(snap: IndexSnapshot, author: str, expected: list[int]) -> None:
    assert snap.filter_positions(author=author) == expected


@pytest.mark.parametrize(
    ("after", "before", "expected"),
    [
        (date(2026, 1, 2), None, [0, 1]),  # 2026-01-01T16:00Z is 2026-01-02 in KST
        (None, date(2026, 1, 1), [2, 3]),
        (date(2026, 1, 1), date(2026, 1, 1), [2, 3]),  # bounds are inclusive
        (date(2026, 1, 2), date(2026, 1, 2), [1]),
        (date(2026, 1, 3), None, [0]),
        (None, date(2025, 12, 31), []),  # 2025-12-31T15:00Z is already 2026-01-01 in KST
        (date(2026, 1, 4), None, []),
        (date(2026, 1, 3), date(2026, 1, 1), []),
    ],
)
def test_filter_by_date_in_blog_timezone(
    snap: IndexSnapshot, after: date | None, before: date | None, expected: list[int]
) -> None:
    assert snap.filter_positions(published_after=after, published_before=before) == expected


def test_filters_combine(snap: IndexSnapshot) -> None:
    assert snap.filter_positions(category="compute", author="lee") == [3]
    assert snap.filter_positions(
        category="artificial-intelligence", published_before=date(2026, 1, 2)
    ) == [1]
    assert snap.filter_positions(category="데이터베이스", author="lee") == []


def test_blog_timezone_is_kst() -> None:
    assert KO_TECH.tz == timezone(timedelta(hours=9))


# --------------------------------------------------------------------------------------------
# BlogIndex
# --------------------------------------------------------------------------------------------


@dataclass
class Clock:
    now: float = 1000.0

    def __call__(self) -> float:
        return self.now


@asynccontextmanager
async def index_for(
    fake: FakeAws,
    clock: Clock,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
    refresh_interval: float = 600.0,
    full_reload_interval: float = 3600.0,
    retry_interval: float = 60.0,
    first_load_cooldown: float = 15.0,
    refresh_deadline: float = 30.0,
) -> AsyncIterator[BlogIndex]:
    async with create_http_client(transport or fake.transport()) as http:
        fetcher = Fetcher(http, min_interval=0, max_attempts=1)
        yield BlogIndex(
            KO_TECH,
            DirectoryClient(fetcher),
            refresh_interval=refresh_interval,
            full_reload_interval=full_reload_interval,
            retry_interval=retry_interval,
            first_load_cooldown=first_load_cooldown,
            refresh_deadline=refresh_deadline,
            clock=clock,
        )


def kinds(fake: FakeAws) -> list[str]:
    """Classify directory requests: 'recent' (incremental), 'full' (q trick), 'scan' (no q)."""
    out: list[str] = []
    for r in fake.directory_requests():
        p = r.url.params
        if p.get("sort_by") == SORT_MODIFIED:
            out.append("recent")
        else:
            out.append("full" if "q" in p else "scan")
    return out


def post_ids(snapshot: IndexSnapshot) -> list[str]:
    return [p.post_id for p in snapshot.posts]


def item_on(
    n: int, day: int, *, modified_day: int | None = None, title: str | None = None
) -> dict[str, Any]:
    created = f"2026-01-{day:02d}T00:00:00Z"
    modified = f"2026-01-{modified_day:02d}T00:00:00Z" if modified_day else None
    return make_item(n, title or f"Post {n}", created=created, modified=modified)


def fail_directory(status: int = 503) -> Any:
    return lambda request: httpx2.Response(status)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def three(fake_aws: FakeAws) -> FakeAws:
    fake_aws.items = [item_on(1, 1), item_on(2, 2), item_on(3, 3)]
    return fake_aws


async def test_first_load_is_lazy(three: FakeAws, clock: Clock) -> None:
    async with index_for(three, clock) as index:
        assert three.requests == []
        snap = await index.snapshot()
    assert kinds(three) == ["full"]
    assert post_ids(snap) == ["65-3", "65-2", "65-1"]
    assert snap.stale is False
    assert snap.loaded_at.tzinfo is UTC
    assert index.blog is KO_TECH


async def test_no_refetch_within_refresh_interval(three: FakeAws, clock: Clock) -> None:
    async with index_for(three, clock) as index:
        first = await index.snapshot()
        clock.now += 599.9
        assert await index.snapshot() is first
        assert await index.snapshot() is first
    assert kinds(three) == ["full"]


async def test_incremental_merge_after_refresh_interval(three: FakeAws, clock: Clock) -> None:
    async with index_for(three, clock) as index:
        first = await index.snapshot()
        three.items[1] = item_on(2, 2, modified_day=9, title="Updated title")
        three.items.append(item_on(4, 10))
        clock.now += 600
        second = await index.snapshot()
        clock.now += 1
        third = await index.snapshot()
    assert kinds(three) == ["full", "recent"]
    params = three.directory_requests()[1].url.params
    assert (params["size"], params["q"]) == ("50", "65")
    assert second is not first
    assert third is second
    assert post_ids(second) == ["65-4", "65-3", "65-2", "65-1"]
    updated = second.find(post_id="65-2")
    assert updated is not None
    assert updated.title == "Updated title"
    assert updated.modified == datetime(2026, 1, 9, tzinfo=UTC)
    assert second.stale is False
    assert second.loaded_at >= first.loaded_at
    assert second.find(slug="post-4") is not None
    assert first.find(slug="post-4") is None  # old snapshots are immutable


async def test_incremental_refresh_keeps_deleted_posts_until_full_reload(
    three: FakeAws, clock: Clock
) -> None:
    async with index_for(three, clock) as index:
        await index.snapshot()  # t=1000, full load
        del three.items[0]
        clock.now = 1600
        incremental = await index.snapshot()
        clock.now = 4599  # 3599s after the full load
        still = await index.snapshot()
        clock.now = 5199  # refresh due again, and 4199s after the full load
        reloaded = await index.snapshot()
    assert post_ids(incremental) == ["65-3", "65-2", "65-1"]
    assert post_ids(still) == ["65-3", "65-2", "65-1"]
    assert post_ids(reloaded) == ["65-3", "65-2"]
    assert kinds(three) == ["full", "recent", "recent", "full"]


async def test_full_reload_interval_counts_from_last_full_load(
    three: FakeAws, clock: Clock
) -> None:
    async with index_for(three, clock, refresh_interval=100, full_reload_interval=250) as index:
        for t in (1000, 1100, 1200, 1250, 1350, 1500):
            clock.now = t
            await index.snapshot()
    # 1000 full; 1100 recent; 1200 recent; 1250 nothing (<100s since 1200);
    # 1350: due, but only 350 >= 250 since full -> full; 1500: due, 150 < 250 -> recent
    assert kinds(three) == ["full", "recent", "recent", "full", "recent"]


async def test_gap_detection_triggers_full_reload(three: FakeAws, clock: Clock) -> None:
    async with index_for(three, clock) as index:
        await index.snapshot()
        # 50 new posts: a whole incremental batch is newer than anything known
        three.items += [item_on(100 + i, 10 + i % 15) for i in range(50)]
        clock.now += 600
        snap = await index.snapshot()
    assert kinds(three) == ["full", "recent", "full"]
    assert len(snap.posts) == 53


async def test_no_gap_when_batch_overlaps_known_posts(three: FakeAws, clock: Clock) -> None:
    async with index_for(three, clock) as index:
        await index.snapshot()
        # 49 new posts: the batch of 50 still includes the newest known post (equal time)
        three.items += [item_on(100 + i, 10 + i % 15) for i in range(49)]
        clock.now += 600
        snap = await index.snapshot()
    assert kinds(three) == ["full", "recent"]
    assert len(snap.posts) == 52


async def test_gap_detection_uses_modified_times(three: FakeAws, clock: Clock) -> None:
    async with index_for(three, clock) as index:
        await index.snapshot()
        # every known post was modified later than anything previously known
        three.items = [item_on(n, n, modified_day=20 + n) for n in (1, 2, 3)]
        clock.now += 600
        snap = await index.snapshot()
    assert kinds(three) == ["full", "recent", "full"]
    assert post_ids(snap) == ["65-3", "65-2", "65-1"]


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (503, b""),
        (200, json.dumps({"items": [], "metadata": {}, "fieldTypes": {}}).encode()),
        (200, b"<html>maintenance</html>"),
    ],
    ids=["http-503", "rejected-query", "invalid-json"],
)
async def test_refresh_failure_serves_stale_snapshot_and_waits_retry_interval(
    three: FakeAws, clock: Clock, status: int, body: bytes
) -> None:
    async with index_for(three, clock, retry_interval=60) as index:
        fresh = await index.snapshot()  # t=1000
        three.directory_override = lambda request: httpx2.Response(status, content=body)
        clock.now = 1600
        stale = await index.snapshot()
        assert kinds(three) == ["full", "recent"]
        assert stale.stale is True
        assert post_ids(stale) == post_ids(fresh)
        assert stale.loaded_at == fresh.loaded_at

        clock.now = 1659  # within retry_interval: no new attempt
        assert await index.snapshot() is stale
        assert kinds(three) == ["full", "recent"]

        clock.now = 1660  # retry_interval elapsed: try again (still failing)
        again = await index.snapshot()
        assert again.stale is True
        assert kinds(three) == ["full", "recent", "recent"]

        three.directory_override = None
        clock.now = 1720
        recovered = await index.snapshot()
        assert recovered.stale is False
        assert post_ids(recovered) == post_ids(fresh)
        assert kinds(three) == ["full", "recent", "recent", "recent"]


async def test_refresh_deadline_serves_stale_snapshot(
    three: FakeAws, clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    attempts: list[str] = []

    async def hanging(request: httpx2.Request) -> httpx2.Response:
        if request.url.params.get("sort_by") == SORT_MODIFIED:
            attempts.append("recent")
            await anyio.sleep(30)
        return three.handler(request)

    transport = httpx2.MockTransport(hanging)
    async with index_for(
        three, clock, transport=transport, refresh_deadline=0.05, retry_interval=60
    ) as index:
        fresh = await index.snapshot()
        clock.now += 600
        with anyio.fail_after(5), caplog.at_level(logging.WARNING, logger="aws_blog_mcp.index"):
            stale = await index.snapshot()
        assert stale.stale is True
        assert post_ids(stale) == post_ids(fresh)
        assert attempts == ["recent"]
        assert "Index refresh failed, serving stale data" in caplog.text
        assert not any(r.exc_info for r in caplog.records)  # expected: no traceback

        clock.now += 59
        assert await index.snapshot() is stale
        assert attempts == ["recent"]


async def test_refresh_timeout_is_named_in_the_log(
    three: FakeAws, clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    async def hanging(request: httpx2.Request) -> httpx2.Response:
        if request.url.params.get("sort_by") == SORT_MODIFIED:
            await anyio.sleep(30)
        return three.handler(request)

    transport = httpx2.MockTransport(hanging)
    async with index_for(three, clock, transport=transport, refresh_deadline=0.05) as index:
        await index.snapshot()
        clock.now += 600
        with anyio.fail_after(5), caplog.at_level(logging.WARNING, logger="aws_blog_mcp.index"):
            await index.snapshot()
    assert "Index refresh failed, serving stale data: TimeoutError" in caplog.messages


async def test_unexpected_refresh_error_serves_stale_snapshot(
    three: FakeAws,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken(self: DirectoryClient, *args: Any, **kwargs: Any) -> list[Item]:
        raise KeyError("item")

    async with index_for(three, clock) as index:
        fresh = await index.snapshot()
        monkeypatch.setattr(DirectoryClient, "fetch_recently_modified", broken)
        clock.now += 600
        with caplog.at_level(logging.WARNING, logger="aws_blog_mcp.index"):
            stale = await index.snapshot()
    assert stale.stale is True
    assert post_ids(stale) == post_ids(fresh)
    (warning,) = [r for r in caplog.records if "Index refresh failed" in r.getMessage()]
    # Unexpected errors keep their traceback in the log.
    assert warning.exc_info
    assert warning.exc_info[0] is KeyError


async def test_retry_interval_counts_from_the_end_of_a_failed_refresh(
    three: FakeAws, clock: Clock
) -> None:
    attempts: list[float] = []

    def slow_failure(request: httpx2.Request) -> httpx2.Response:
        if request.url.params.get("sort_by") == SORT_MODIFIED:
            attempts.append(clock.now)
            clock.now += 100  # the failing attempt itself takes a while
            return httpx2.Response(503)
        return three.handler(request)

    transport = httpx2.MockTransport(slow_failure)
    async with index_for(three, clock, transport=transport, retry_interval=60) as index:
        await index.snapshot()  # t=1000
        clock.now = 1600
        assert (await index.snapshot()).stale is True  # attempt from 1600 to 1700
        clock.now = 1759  # 159 s after the start, 59 s after the end: still waiting
        await index.snapshot()
        assert attempts == [1600]
        clock.now = 1760
        await index.snapshot()
    assert attempts == [1600, 1760]


async def test_callers_do_not_wait_for_a_running_refresh(fake_aws: FakeAws, clock: Clock) -> None:
    fake_aws.items = [item_on(1, 1), item_on(2, 2)]
    started = anyio.Event()
    release = anyio.Event()

    async def gated(request: httpx2.Request) -> httpx2.Response:
        if request.url.params.get("sort_by") == SORT_MODIFIED:
            started.set()
            await release.wait()
        return fake_aws.handler(request)

    refreshed: list[IndexSnapshot] = []
    async with index_for(fake_aws, clock, transport=httpx2.MockTransport(gated)) as index:
        first = await index.snapshot()
        fake_aws.items.append(item_on(3, 3))
        clock.now += 600

        async def refresh() -> None:
            refreshed.append(await index.snapshot())

        async with anyio.create_task_group() as tg:
            tg.start_soon(refresh)
            await started.wait()
            with anyio.fail_after(1):  # would hang if callers queued behind the refresh
                assert await index.snapshot() is first
                assert await index.snapshot() is first
            assert refreshed == []
            release.set()
        assert await index.snapshot() is refreshed[0]
    assert post_ids(refreshed[0]) == ["65-3", "65-2", "65-1"]
    assert kinds(fake_aws) == ["full", "recent"]


async def test_full_reload_failure_serves_stale_snapshot(three: FakeAws, clock: Clock) -> None:
    async with index_for(three, clock, retry_interval=30) as index:
        fresh = await index.snapshot()
        three.directory_override = fail_directory()
        clock.now = 1000 + 3600
        stale = await index.snapshot()
        assert stale.stale is True
        assert post_ids(stale) == post_ids(fresh)
        three.directory_override = None
        clock.now += 30
        reloaded = await index.snapshot()
    assert reloaded.stale is False
    assert kinds(three) == ["full", "full", "full"]


async def test_first_load_failure_raises_and_next_call_retries(
    three: FakeAws, clock: Clock
) -> None:
    three.directory_override = fail_directory()
    async with index_for(three, clock) as index:
        with pytest.raises(UpstreamError, match="HTTP 503"):
            await index.snapshot()
        three.directory_override = None
        # Within the cool-down the cached failure is reported without calling upstream again.
        with pytest.raises(UpstreamError, match="HTTP 503"):
            await index.snapshot()
        assert kinds(three) == ["full"]
        clock.now += 15
        snap = await index.snapshot()
    assert post_ids(snap) == ["65-3", "65-2", "65-1"]
    assert kinds(three) == ["full", "full"]


async def test_first_load_cooldown_is_configurable(three: FakeAws, clock: Clock) -> None:
    three.directory_override = fail_directory()
    async with index_for(three, clock, first_load_cooldown=0) as index:
        for _ in range(3):
            with pytest.raises(UpstreamError, match="HTTP 503"):
                await index.snapshot()
    assert kinds(three) == ["full", "full", "full"]


async def test_unexpected_first_load_error_is_reported_and_cached(
    three: FakeAws,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[int] = []

    async def broken(self: DirectoryClient, *args: Any, **kwargs: Any) -> list[Item]:
        calls.append(1)
        raise RuntimeError("unexpected payload shape")

    monkeypatch.setattr(DirectoryClient, "fetch_blog", broken)
    expected = "Loading the post list from aws.amazon.com failed (RuntimeError)."
    async with index_for(three, clock) as index:
        with pytest.raises(UpstreamError) as info:
            await index.snapshot()
        assert str(info.value) == expected
        assert isinstance(info.value.__cause__, RuntimeError)
        assert "Unexpected error while loading the post list" in caplog.text

        clock.now += 14.9
        with pytest.raises(UpstreamError, match=re.escape(expected)):
            await index.snapshot()
        assert len(calls) == 1  # reported from the cache, without trying again

        monkeypatch.undo()
        clock.now += 0.1
        snap = await index.snapshot()
    assert post_ids(snap) == ["65-3", "65-2", "65-1"]
    assert len(calls) == 1


async def test_first_load_success_clears_the_cached_failure(three: FakeAws, clock: Clock) -> None:
    three.directory_override = fail_directory()
    async with index_for(three, clock) as index:
        with pytest.raises(UpstreamError):
            await index.snapshot()
        three.directory_override = None
        clock.now += 15
        first = await index.snapshot()
        # Later refresh failures serve the snapshot; the old first-load error never resurfaces.
        three.directory_override = fail_directory()
        clock.now += 600
        stale = await index.snapshot()
    assert stale.stale is True
    assert post_ids(stale) == post_ids(first)


async def test_first_load_with_empty_directory_raises(fake_aws: FakeAws, clock: Clock) -> None:
    async with index_for(fake_aws, clock) as index:
        with pytest.raises(UpstreamError, match="returned no posts"):
            await index.snapshot()
    assert kinds(fake_aws) == ["full", "scan"]


async def test_full_reload_passes_previous_count_for_fallback(
    fake_aws: FakeAws, clock: Clock
) -> None:
    fake_aws.items = [item_on(n, n) for n in (1, 2, 3, 4)]
    async with index_for(fake_aws, clock) as index:
        await index.snapshot()
        only_one = [item_on(4, 4)]

        def q_trick_degraded(request: httpx2.Request) -> httpx2.Response | None:
            if "q" in request.url.params:
                body = {"items": only_one, "metadata": {"totalHits": 1}, "fieldTypes": FIELD_TYPES}
                return httpx2.Response(200, json=body)
            return None

        fake_aws.directory_override = q_trick_degraded
        clock.now += 3600
        snap = await index.snapshot()
    assert kinds(fake_aws) == ["full", "full", "scan"]
    assert post_ids(snap) == ["65-4", "65-3", "65-2", "65-1"]


@pytest.mark.parametrize(
    "created",
    ["not a date", "9999-12-31T23:00:00Z", "1989-01-01T00:00:00Z", "2026-02-30T00:00:00Z"],
)
async def test_unparsable_items_are_skipped(
    fake_aws: FakeAws, clock: Clock, caplog: pytest.LogCaptureFixture, created: str
) -> None:
    broken = item_on(2, 2)
    fields_of(broken)["createdDate"] = created
    fake_aws.items = [item_on(1, 1), broken, item_on(3, 3)]
    async with index_for(fake_aws, clock) as index:
        with caplog.at_level(logging.WARNING, logger="aws_blog_mcp.index"):
            snap = await index.snapshot()
    assert post_ids(snap) == ["65-3", "65-1"]
    assert "Skipping unparsable directory item" in caplog.text


@pytest.mark.parametrize(
    "bad_tag",
    [
        {"id": "x", "tagNamespaceId": None, "name": "x"},
        "not-a-dict",
        None,
        {"id": None, "tagNamespaceId": "blog-posts#category", "name": "x"},
        {"id": "y", "tagNamespaceId": "blog-posts#category", "name": "y", "description": [1]},
    ],
    ids=["null-namespace", "string-tag", "null-tag", "null-id", "list-description"],
)
async def test_malformed_tag_does_not_break_the_index(
    fake_aws: FakeAws, clock: Clock, bad_tag: Any
) -> None:
    odd = item_on(2, 2)
    odd["tags"].append(bad_tag)
    fake_aws.items = [item_on(1, 1), odd]
    async with index_for(fake_aws, clock) as index:
        snap = await index.snapshot()
    assert post_ids(snap) == ["65-2", "65-1"]


async def test_any_per_item_exception_is_isolated(
    fake_aws: FakeAws,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    real_parse_item = index_module.parse_item

    def flaky(raw: Item) -> PostRecord:
        if raw["item"]["id"].endswith("-2"):
            raise RuntimeError("surprise")
        return real_parse_item(raw)

    monkeypatch.setattr(index_module, "parse_item", flaky)
    fake_aws.items = [item_on(1, 1), item_on(2, 2), item_on(3, 3)]
    async with index_for(fake_aws, clock) as index:
        with caplog.at_level(logging.WARNING, logger="aws_blog_mcp.index"):
            snap = await index.snapshot()
    assert post_ids(snap) == ["65-3", "65-1"]
    assert "Skipping unparsable directory item: RuntimeError('surprise')" in caplog.text


async def test_items_the_directory_rejects_do_not_reach_the_index(
    fake_aws: FakeAws, clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    no_date = item_on(2, 2)
    fields_of(no_date)["createdDate"] = None
    no_title = item_on(4, 4)
    fields_of(no_title)["title"] = None
    fake_aws.items = [item_on(1, 1), item_on(3, 3)]

    def with_broken(request: httpx2.Request) -> httpx2.Response:
        body = fake_aws._search(request)
        body["items"] = [no_date, no_title, *body["items"]]
        return httpx2.Response(200, json=body)

    fake_aws.directory_override = with_broken
    async with index_for(fake_aws, clock) as index:
        with caplog.at_level(logging.WARNING):
            snap = await index.snapshot()
    assert post_ids(snap) == ["65-3", "65-1"]
    assert "Skipped 2 malformed directory items" in caplog.text


async def test_concurrent_first_load_is_single_flight(fake_aws: FakeAws, clock: Clock) -> None:
    fake_aws.items = [item_on(1, 1), item_on(2, 2)]
    in_flight = 0
    peak = 0

    async def slow(request: httpx2.Request) -> httpx2.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await anyio.sleep(0.02)
        in_flight -= 1
        return fake_aws.handler(request)

    results: list[IndexSnapshot] = []
    async with index_for(fake_aws, clock, transport=httpx2.MockTransport(slow)) as index:

        async def call() -> None:
            results.append(await index.snapshot())

        async with anyio.create_task_group() as tg:
            for _ in range(8):
                tg.start_soon(call)
    assert kinds(fake_aws) == ["full"]
    assert peak == 1
    assert len(results) == 8
    assert all(r is results[0] for r in results)


async def test_concurrent_refresh_is_single_flight(fake_aws: FakeAws, clock: Clock) -> None:
    fake_aws.items = [item_on(1, 1), item_on(2, 2)]

    async def slow(request: httpx2.Request) -> httpx2.Response:
        await anyio.sleep(0.02)
        return fake_aws.handler(request)

    results: list[IndexSnapshot] = []
    async with index_for(fake_aws, clock, transport=httpx2.MockTransport(slow)) as index:
        first = await index.snapshot()
        fake_aws.items.append(item_on(3, 3))
        clock.now += 600

        async def call() -> None:
            results.append(await index.snapshot())

        async with anyio.create_task_group() as tg:
            for _ in range(6):
                tg.start_soon(call)
    # One refresh runs; callers arriving meanwhile get the previous snapshot without waiting.
    assert kinds(fake_aws) == ["full", "recent"]
    refreshed = [r for r in results if r is not first]
    assert len(refreshed) == 1
    assert post_ids(refreshed[0]) == ["65-3", "65-2", "65-1"]
    assert all(r is first for r in results if r is not refreshed[0])


K8S_CAT = cat("amazon-kubernetes-service", "Amazon Elastic Kubernetes Service", "compute")


def _eks_snapshot() -> IndexSnapshot:
    labels = [("GLOBAL#product", "Amazon Elastic Kubernetes Service (EKS)")]
    return snapshot_of(
        record(1, categories=[COMPUTE, K8S_CAT], global_tags=labels),
        record(2, categories=[COMPUTE], global_tags=labels),
        record(3, categories=[AI, KIRO]),
    )


@pytest.mark.parametrize("value", ["EKS", "Amazon EKS", "eks", "쿠버네티스", "kubernetes"])
def test_label_filter_accepts_vendor_prefix_and_synonyms(value: str) -> None:
    snap = _eks_snapshot()
    positions = snap.filter_positions(category=value)
    assert sorted(snap.posts[i].post_id for i in positions) == ["65-1", "65-2"]
    notice = snap.category_notice(value)
    assert notice is not None
    assert "Amazon Elastic Kubernetes Service (compute/amazon-kubernetes-service)" in notice


def test_typo_gets_word_level_suggestions() -> None:
    with pytest.raises(InvalidInputError, match="Amazon Elastic Kubernetes Service"):
        _eks_snapshot().resolve_category("Kubernetis")


def test_match_categories_without_prefix_fallback() -> None:
    snap = snapshot_of(record(1, categories=[L300]))  # the learning-levels root never appears
    assert [n.slug for n in snap.match_categories("learning-levels")] == ["advanced-300"]
    assert snap.match_categories("learning-levels", prefix_fallback=False) == []
