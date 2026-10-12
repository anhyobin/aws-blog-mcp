# Contributing

Thanks for your interest in improving aws-blog-mcp. Bug reports, search-quality reports (a query
that should have found a post but did not), fixes and support for more AWS blogs are all welcome.
For larger changes, please open an issue first so we can agree on the approach.

## Ground rules

- **No real blog content in the repository.** Blog posts are copyrighted by AWS or their authors.
  Test fixtures must be synthetic: you may mimic the HTML structure of a post page, but write your
  own text. Link to a post in an issue instead of pasting it.
- **Be polite to aws.amazon.com.** The offline test suite never touches the network. Keep live
  tests few and small, and do not add anything that crawls or polls in the background.
- **Keep the docs true.** If you change a tool's parameters, behaviour or limits, update
  `README.md`, `README.ko.md` and `CHANGELOG.md` in the same pull request.

## Setup

You need [uv](https://docs.astral.sh/uv/). It installs a suitable Python (3.11 or later; the
repository pins 3.13 for development in `.python-version`) and the dev dependencies:

```bash
git clone https://github.com/anhyobin/aws-blog-mcp.git
cd aws-blog-mcp
uv sync
```

## Checks

CI runs the same commands; please run them before opening a pull request.

```bash
uv run ruff format          # format (CI runs: uv run ruff format --check)
uv run ruff check           # lint (add --fix for safe autofixes)
uv run pyright              # type check, standard mode
uv run pytest               # offline tests
uv run pytest -q --cov      # offline tests with coverage, as in CI
```

## Tests

The offline suite runs the real server in-process against `FakeAws` (`tests/fakes.py`), an
in-memory stand-in for the directory API and post pages that is plugged in through an
`httpx2.MockTransport`. Useful building blocks:

- `make_item(...)` builds a directory API item and `cat(...)` a category tag,
- `post_page(...)` builds a synthetic post page in the real page template,
- the `client` fixture (`tests/conftest.py`) is an in-process `mcp.Client` connected to
  `create_server(transport=fake_aws.transport(), request_interval=0)`. It is parametrized over the
  `auto` and `legacy` protocol modes, so tool tests run against both.

Tests are async and use the anyio pytest plugin (`@pytest.mark.anyio`), not pytest-asyncio.

### Live tests

Tests marked `@pytest.mark.live` call the real aws.amazon.com and are skipped unless you opt in:

```bash
uv run pytest -m live --run-live
```

They catch changes in the undocumented directory API and the post page template. The
[Live tests](.github/workflows/live.yml) workflow runs them weekly and on demand.

### Smoke test of the built package

This is what CI does to check that the wheel works outside the source tree:

```bash
uv build
uv run --isolated --no-project --with dist/*.whl tests/smoke_test.py
```

### Trying the server by hand

Point any MCP client at your checkout (see "Run from source" in the README), or use the
[MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```bash
npx @modelcontextprotocol/inspector uv --directory "$PWD" run aws-blog-mcp
```

Set `AWS_BLOG_MCP_LOG_LEVEL=DEBUG` to see requests, retries and index refreshes on stderr.

## Keeping the tool reference accurate

The tool tables in the READMEs must match what the server advertises. To print the tool names,
descriptions and input schemas (in-process, no network access):

```bash
uv run python - <<'EOF'
import json

import anyio
from mcp import Client

from aws_blog_mcp.server import create_server


async def main() -> None:
    async with Client(create_server()) as client:
        for tool in (await client.list_tools()).tools:
            print(json.dumps(tool.model_dump(mode="json", exclude_none=True), indent=2, ensure_ascii=False))


anyio.run(main)
EOF
```

## Adding another AWS blog

Every blog post in the AWS directory API has an id like `blog-posts#65-28623`. The number before
the dash identifies the blog (`65` is the Korean tech blog), so a blog is described by a
`BlogConfig` entry in `src/aws_blog_mcp/blogs.py`:

```python
KO_TECH = BlogConfig(
    key="ko-tech",  # short identifier
    title="AWS 기술 블로그",  # display title as on the site
    locale="ko_KR",  # directory API locale
    id_prefix="65",  # number in blog-posts#<id_prefix>-<post>
    base_url="https://aws.amazon.com/ko/blogs/tech/",  # blog home, with trailing slash
    utc_offset_hours=9,  # for the dates readers see on the site
)

BLOGS: dict[str, BlogConfig] = {blog.key: blog for blog in (KO_TECH,)}
```

To add a blog:

1. Find its locale and id prefix. Look at a few items of the blog's locale in the directory API
   (the same request the server makes; a handful of manual requests is fine):

   ```bash
   curl -s 'https://aws.amazon.com/api/dirs/items/search?item.directoryId=blog-posts&item.locale=ko_KR&sort_by=item.additionalFields.createdDate&sort_order=desc&size=20' \
     | python3 -m json.tool | grep -E '"(id|link)"'
   ```

   All posts whose `link` starts with the blog's home URL should share one `blog-posts#<n>-` id
   prefix. The server checks both (id prefix and URL prefix) for every item.
2. Add a `BlogConfig` and include it in the `BLOGS` tuple. Check that its post pages use the
   shared AWS blog template that `extract.py` expects (`body.aws-blogs-page-single` with exactly
   one `article.blog-post section.blog-post-content`).
3. Add synthetic tests: `make_item(..., prefix="<n>", base="<base_url>")` and `post_page(...)`
   in `tests/fakes.py` already accept another blog's prefix and URL.
4. Update both READMEs and the changelog.

Note the current scope: registered blogs are accepted by `get_post` (URL validation and
reading), but `search_posts`, `list_recent_posts` and `list_categories` index `DEFAULT_BLOG`
only, and the tool descriptions name the Korean tech blog. Making several blogs searchable needs
changes in `server.py` (for example a `blog` parameter); please open an issue to discuss the
design first.

## Pull requests

- Keep each pull request focused, and add or update tests for behaviour changes.
- Add an entry under the topmost section of `CHANGELOG.md` for user-visible changes.
- Fill in the pull request template checklist.

## Releasing (maintainers)

One-time setup:

- On PyPI, add a trusted publisher for the `aws-blog-mcp` project (a pending publisher before the
  first release): owner `anhyobin`, repository `aws-blog-mcp`, workflow `release.yml`,
  environment `pypi`.
- In the GitHub repository settings, create the `pypi` environment, restrict its deployment
  tags to `v*` and add a required reviewer; enable private vulnerability reporting.
- The MCP Registry needs no secret: the release workflow logs in with GitHub OIDC, which grants
  the `io.github.anhyobin/*` namespace.

For each release:

1. Set the new version in `pyproject.toml` and in both `version` fields of `server.json`, then
   run `uv lock` (the lock file records the project version).
2. In `CHANGELOG.md`, turn the top section into `## [X.Y.Z] - YYYY-MM-DD` and add its link at the
   bottom.
3. Check everything agrees:
   `uv run --no-project .github/scripts/check_metadata.py --tag vX.Y.Z --release`
4. Merge to `main`, then tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The [release workflow](.github/workflows/release.yml) checks that the tag, `pyproject.toml` and
`server.json` agree, runs the tests, builds and smoke-tests the package, publishes it to PyPI
through Trusted Publishing, and then publishes `server.json` to the MCP Registry (retrying while
PyPI propagates the new release).

GitHub Actions are pinned to commit SHAs and updated by Dependabot. `mcp-publisher` is pinned
by version and SHA-256 in `.github/actions/setup-mcp-publisher/action.yml`; to update it, take
the checksum of `mcp-publisher_linux_amd64.tar.gz` from the `registry_<version>_checksums.txt`
asset of the [registry release](https://github.com/modelcontextprotocol/registry/releases).
