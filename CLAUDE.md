# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An unofficial MCP server (stdio) that searches and reads the AWS Korea tech blog
(https://aws.amazon.com/ko/blogs/tech/). It is intended for public release (PyPI + MCP Registry), so
changes are held to a release-quality bar: tests, docs and `CHANGELOG.md` move together with the code.
`CONTRIBUTING.md` has the full contributor workflow and release steps; `README.md` / `README.ko.md`
document every tool parameter and must stay in sync with the code (both languages).

## Commands

```bash
uv sync                                   # .venv with dev deps (Python 3.13 per .python-version; supports >=3.11)
uv run ruff format && uv run ruff check   # format + lint (ruff also formats Python blocks inside *.md)
uv run pyright                            # standard mode, src + tests
uv run pytest -q                          # offline suite (~1600 tests, a few seconds)
uv run pytest tests/test_search.py::test_name -q          # single test
uv run pytest -q --cov                    # coverage, as in CI
uv run pytest tests/test_live.py --run-live -q            # opt-in tests against the real site (keep rare)
uv build --out-dir /tmp/dist && uv run --isolated --no-project --with /tmp/dist/*.whl tests/smoke_test.py
uv run --no-project .github/scripts/check_metadata.py     # pyproject / server.json / README marker / CHANGELOG agree
```

The server is registered for local use in Claude Code (user scope) as `aws-blog`, running from this
checkout: `uv --directory /Users/anhyobin/dev/aws-blog-mcp run --frozen --quiet aws-blog-mcp`.
Code changes take effect after the MCP server restarts (`/mcp` → reconnect).

## Architecture

Two independent data paths, both through one rate-limited `http.Fetcher`:

1. **Search / list / categories** → `index.BlogIndex` → `directory.DirectoryClient` → the undocumented
   directory API (`aws.amazon.com/api/dirs/items/search`). The whole blog (~900 posts) is loaded on
   first use into an immutable `IndexSnapshot`; `search.py` ranks it locally. Refresh is on demand only
   (incremental after 10 min, full after 6 h), never blocks other callers, and serves a stale snapshot
   with a notice on failure.
2. **`get_post`** → `urls.parse_reference` → `reader.PostReader` (LRU cache) fetches the post HTML →
   `extract.parse_post_page` (metadata + body HTML) → `extract.render_body` (HTML cleanup, then
   `markdown.BlogConverter` + `tidy`) in a worker thread → `markdown.slice_markdown` +
   `reader.format_post` paginate by character offset.

`server.create_server(transport=..., request_interval=...)` wires everything in the MCP lifespan;
tests inject an `httpx2.MockTransport` there. `blogs.py` is the blog registry (id prefix = blog
identity); today only `ko-tech` exists and the tools use `DEFAULT_BLOG`.

## Non-obvious rules

- **MCP SDK 2.x, not 1.x examples.** `from mcp.server.mcpserver import MCPServer` (FastMCP no longer
  exists); pass every `MCPServer` argument except `name` by keyword. HTTP is `httpx2`, not `httpx`.
- **Errors:** library code raises `errors.BlogMCPError` subclasses; `server._tool_errors()` turns them
  into `ToolError`. Any other exception reaches the model only as "Error executing tool X", so
  expected failures must never escape as `ValueError` etc. Messages are written for the model and say
  what to do next.
- **Tool output:** list tools return `Annotated[CallToolResult, Model]` via `server._result()` (compact
  JSON text identical to `structuredContent`); `get_post` uses `structured_output=False` to avoid a
  duplicated body. Tool docstrings become descriptions verbatim: no `Args:` blocks, document params
  with `Field(description=...)`, keep descriptions and `INSTRUCTIONS` under 2048 chars.
- **Directory API quirks** (see `directory.py` docstring): a rejected query is HTTP 200 with empty
  `fieldTypes`; `size <= 2000` and `(page+1)*size <= 10000`; there is no blog filter, so `q=<id prefix>`
  plus client-side prefix filtering is used, with a full-locale scan as fallback.
- **Markdown pipeline:** `<br>` becomes the `markdown.HARD_BREAK` sentinel (`\x00`) and only `tidy()`
  turns it into `\` + newline; literal backslashes and fenced code must stay byte-identical. Offsets
  for pagination depend on the `images` mode.
- **Search** matches metadata only (title, excerpt, categories/labels, slug, authors), never post
  bodies. Korean handling is heuristic: particle/ending stripping, `STOPWORDS`, `OPTIONAL_WORDS`
  (boost but don't filter), `SYNONYM_GROUPS`, `BOUNDED_TRANSLITERATIONS` in `search.py`.
- **Network safety:** only `https://aws.amazon.com` (default port, no userinfo) is ever fetched,
  redirects included; keep the descriptive User-Agent and throttling. Never add background crawling.
- **Tests:** fixtures are synthetic (`tests/fakes.py`: `FakeAws`, `make_item`, `cat`, `post_page`);
  never commit real blog prose (copyright). Async tests use `pytestmark = pytest.mark.anyio`; the
  `client` fixture runs each MCP test in both protocol modes (`auto` and `legacy`). `filterwarnings =
  error` is on. A test that exposes a real bug is kept as `xfail(strict=True, reason="BUG: ...")`.

## Open items before a public release

The package name contains "AWS", which AWS trademark guidelines (§7) and Amazon's internal OSS
naming rules discourage; the release path (Amazon OSPO approval vs. personal project with a COI
report) and possibly the name/namespace are undecided. Do not publish to PyPI or the MCP Registry,
create the GitHub repo, or push without the maintainer's explicit go-ahead.
