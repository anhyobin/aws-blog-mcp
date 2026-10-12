"""Exceptions raised by the library layer.

The MCP layer (``server.py``) converts every :class:`BlogMCPError` into a ``ToolError`` so the
message reaches the model. Messages are written for the model: say what went wrong and what to
do next.
"""


class BlogMCPError(Exception):
    """Base class for expected failures."""


class InvalidInputError(BlogMCPError):
    """The caller passed something we cannot use (bad URL, unknown category, ...)."""


class NotFoundError(BlogMCPError):
    """The requested post does not exist."""


class UpstreamError(BlogMCPError):
    """aws.amazon.com failed, timed out, or answered in an unexpected shape."""


class NotABlogPostError(UpstreamError):
    """The fetched page is not a single blog post (listing page, error page, template change)."""
