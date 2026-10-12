"""Structured results returned by the MCP tools."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class PostSummary(BaseModel):
    """One post in a result list."""

    id: str = Field(description="Post id, accepted by get_post (e.g. '65-28623').")
    title: str
    url: str = Field(description="Canonical post URL; cite it when using the post.")
    published: date = Field(description="Publication date (blog local time).")
    authors: list[str]
    categories: list[str]
    excerpt: str = Field(description="Opening of the post, shortened.")


class PostListResult(BaseModel):
    """A page of posts."""

    blog: str = Field(description="Blog the posts come from.")
    total: int = Field(description="Number of posts matching the request.")
    offset: int
    next_offset: int | None = Field(
        description="Pass as offset to get the next page; null when there are no more results."
    )
    posts: list[PostSummary]
    notice: str | None = Field(default=None, description="Caveats about these results, if any.")


class SearchPostsResult(PostListResult):
    """Search results."""

    query: str
    match: Literal["all_terms", "any_term"] = Field(
        description=(
            "'all_terms' if every result contains all required query terms; 'any_term' if nothing "
            "did, and results contain only some of them, ranked so that rarer terms weigh more."
        )
    )


class CategoryInfo(BaseModel):
    """One category."""

    id: str = Field(description="Category id, usable as the category filter.")
    name: str
    path: str = Field(description="Position in the category tree, e.g. 'A > B > C'.")
    post_count: int = Field(description="Posts in this category or its subcategories.")


class CategoriesResult(BaseModel):
    """A list of categories."""

    blog: str
    total: int = Field(description="Number of categories matching the request.")
    categories: list[CategoryInfo] = Field(description="Most used first.")
    notice: str | None = None
