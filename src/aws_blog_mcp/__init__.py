"""Unofficial MCP server to search and read AWS blog posts."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("aws-blog-mcp")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without metadata
    __version__ = "0.0.0"

__all__ = ["__version__"]
