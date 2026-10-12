# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

Initial release.

### Added

- MCP server (stdio) for the AWS Korea tech blog (https://aws.amazon.com/ko/blogs/tech/), run with
  `uvx aws-blog-mcp` or `python -m aws_blog_mcp`.
- `search_posts`: local keyword search over post metadata (title, excerpt, categories and tags,
  slug, authors) with Hangul substring matching, Korean particle and ending stripping,
  Korean/English synonyms, field-weighted ranking and an any-term fallback; filters for category,
  author and publication date; relevance or newest-first sorting; paging.
- `list_recent_posts`: newest-first listing with the same filters and paging.
- `list_categories`: the blog's category tree with post counts, searchable by name (`query`,
  Korean/English synonyms included) and optionally limited to the subcategories of a parent.
- Category filters accept names, ids and display paths; post tags and AWS product labels such as
  `EKS` work too, with a notice suggesting related categories.
- `get_post`: reads a post by URL, path, slug or post id and returns clean markdown (headings,
  lists, tables with row spans expanded, images, callout boxes, fenced code with language) in
  chunks cut at paragraph boundaries, with `images` set to `markdown`, `alt` or `none`. The body
  is delimited by begin/end markers and marked as untrusted reference material.
- Search and list results are returned as structured content with a compact JSON text copy.
- In-memory index loaded from the AWS directory API on first use, refreshed incrementally every
  10 minutes and fully every 6 hours, on demand only; refreshes never block other calls, and stale
  data is served with a notice if a refresh fails. Malformed directory items are skipped.
- In-memory cache of parsed post pages (32 pages, one hour) so paging through a post does not
  refetch it.
- Polite HTTP client: identifiable User-Agent, at most one request start every 0.25 seconds,
  bounded retries with backoff and `Retry-After` support, a 45-second limit per attempt,
  redirects restricted to `https://aws.amazon.com` (default port, no credentials), response size
  limits. Page parsing runs in a worker thread.
- Configuration through `AWS_BLOG_MCP_LOG_LEVEL` and `AWS_BLOG_MCP_USER_AGENT`.
- Blog registry (`blogs.py`) so more AWS blogs can be added later.
- MCP Registry metadata (`server.json`, `io.github.anhyobin/aws-blog-mcp`).

[0.1.0]: https://github.com/anhyobin/aws-blog-mcp/releases/tag/v0.1.0
